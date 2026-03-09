"""GraphDB facade — the main entry point composing all database components."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from uaf.core.operations import (
    CreateEdge,
    CreateNode,
    DeleteEdge,
    DeleteNode,
    UpdateNode,
)
from uaf.core.serialization import blob_hash
from uaf.db.eavt import Datom, EAVTIndex
from uaf.db.materializer import StateMaterializer
from uaf.db.operation_log import OperationLog
from uaf.db.query import QueryEngine

if TYPE_CHECKING:
    from uaf.core.edges import Edge
    from uaf.core.node_id import BlobId, EdgeId, NodeId, OperationId
    from uaf.core.nodes import NodeType
    from uaf.db.operation_log import LogEntry


class GraphDB:
    """Facade composing OperationLog, StateMaterializer, EAVTIndex, and QueryEngine.

    All mutations go through apply() which orchestrates log -> materialize -> index.
    All queries delegate to QueryEngine.
    """

    def __init__(self) -> None:
        self._log = OperationLog()
        self._materializer = StateMaterializer()
        self._index = EAVTIndex()
        self._query = QueryEngine(self._materializer.state, self._index)
        self._blobs: dict[BlobId, bytes] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def apply(self, op: Any) -> OperationId:
        """Orchestrate: append to log -> materialize state -> update indexes.

        Returns the OperationId (content hash).
        """
        op_id = self._log.append(op)
        entry = self._log.get(op_id)
        assert entry is not None

        # Index old datoms for retraction on update
        match op:
            case UpdateNode(node=node):
                old_node = self._materializer.get_node(node.meta.id)
                if old_node is not None:
                    self._index.retract_entity(str(old_node.meta.id.value))
            case DeleteNode(node_id=node_id):
                old_node = self._materializer.get_node(node_id)
                if old_node is not None:
                    self._index.retract_entity(str(node_id.value))
            case _:
                pass

        # Materialize
        self._materializer.apply(entry)

        # Index new datoms
        match op:
            case CreateNode(node=node) | UpdateNode(node=node):
                self._index_node(node, str(op_id))
            case _:
                pass

        return op_id

    def create_node(self, node: Any, *, parent_ops: tuple[OperationId, ...] = ()) -> NodeId:
        """Convenience: create a node and return its NodeId."""
        from uaf.core.node_id import utc_now

        op = CreateNode(node=node, parent_ops=parent_ops, timestamp=utc_now())
        self.apply(op)
        return node.meta.id  # type: ignore[no-any-return]

    def update_node(self, node: Any, *, parent_ops: tuple[OperationId, ...] = ()) -> None:
        """Convenience: update a node (full state replacement)."""
        from uaf.core.node_id import utc_now

        op = UpdateNode(node=node, parent_ops=parent_ops, timestamp=utc_now())
        self.apply(op)

    def delete_node(self, node_id: NodeId, *, parent_ops: tuple[OperationId, ...] = ()) -> None:
        """Convenience: delete a node."""
        from uaf.core.node_id import utc_now

        op = DeleteNode(node_id=node_id, parent_ops=parent_ops, timestamp=utc_now())
        self.apply(op)

    def create_edge(self, edge: Edge, *, parent_ops: tuple[OperationId, ...] = ()) -> None:
        """Convenience: create an edge."""
        from uaf.core.node_id import utc_now

        op = CreateEdge(edge=edge, parent_ops=parent_ops, timestamp=utc_now())
        self.apply(op)

    def delete_edge(
        self, edge_id: EdgeId, *, parent_ops: tuple[OperationId, ...] = ()
    ) -> None:
        """Convenience: delete an edge."""
        from uaf.core.node_id import utc_now

        op = DeleteEdge(edge_id=edge_id, parent_ops=parent_ops, timestamp=utc_now())
        self.apply(op)

    # ------------------------------------------------------------------
    # Query (delegates to QueryEngine)
    # ------------------------------------------------------------------

    def get_node(self, node_id: NodeId) -> Any | None:
        return self._query.get_node(node_id)

    def get_children(self, parent_id: NodeId) -> list[Any]:
        return self._query.get_children(parent_id)

    def get_parent(self, node_id: NodeId) -> Any | None:
        return self._query.get_parent(node_id)

    def get_references_to(self, target_id: NodeId) -> list[Any]:
        return self._query.get_references_to(target_id)

    def find_by_type(self, node_type: NodeType) -> list[Any]:
        return self._query.find_by_type(node_type)

    def find_by_attribute(self, attribute: str, value: str) -> list[Any]:
        return self._query.find_by_attribute(attribute, value)

    def get_edges_from(self, source_id: NodeId) -> list[Edge]:
        return self._query.get_edges_from(source_id)

    def count_nodes(self) -> int:
        return self._query.count_nodes()

    def count_edges(self) -> int:
        return self._query.count_edges()

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_history(self, node_id: NodeId) -> list[LogEntry]:
        """Return all log entries that affected a given node."""
        result: list[LogEntry] = []
        for entry in self._log:
            op = entry.operation
            match op:
                case CreateNode(node=node) | UpdateNode(node=node):
                    if node.meta.id == node_id:
                        result.append(entry)
                case DeleteNode(node_id=del_id):
                    if del_id == node_id:
                        result.append(entry)
                case _:
                    pass
        return result

    # ------------------------------------------------------------------
    # Tree traversal
    # ------------------------------------------------------------------

    def descendants(self, node_id: NodeId) -> set[NodeId]:
        """Recursively walk CONTAINS edges to get all nodes within a subtree."""
        result: set[NodeId] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            if current in result:
                continue
            result.add(current)
            child_ids = self._materializer.state.children_order.get(current, [])
            stack.extend(child_ids)
        return result

    # ------------------------------------------------------------------
    # Blob storage
    # ------------------------------------------------------------------

    def store_blob(self, data: bytes) -> BlobId:
        """Hash and store binary data. Returns the BlobId (content hash)."""
        bid = blob_hash(data)
        self._blobs[bid] = data
        return bid

    def get_blob(self, blob_id: BlobId) -> bytes | None:
        """Retrieve blob data by content hash."""
        return self._blobs.get(blob_id)

    # ------------------------------------------------------------------
    # Internal: datom extraction
    # ------------------------------------------------------------------

    def _index_node(self, node: Any, tx: str) -> None:
        """Extract datoms from a node's typed fields and add to EAVT index."""
        meta = node.meta
        entity = str(meta.id.value)

        # Always index node_type
        self._index.add(Datom(
            entity=entity, attribute="node_type", value=meta.node_type.value, tx=tx,
        ))

        # Index owner if present
        if meta.owner is not None:
            self._index.add(Datom(entity=entity, attribute="owner", value=meta.owner, tx=tx))

        # Index type-specific fields
        for attr_name, attr_value in self._extract_fields(node):
            self._index.add(Datom(entity=entity, attribute=attr_name, value=attr_value, tx=tx))

    @staticmethod
    def _extract_fields(node: Any) -> list[tuple[str, str]]:
        """Extract indexable field name-value pairs from a typed node."""
        from uaf.core.nodes import (
            Artifact,
            ArtifactACL,
            Cell,
            CodeBlock,
            FormulaCell,
            Heading,
            Image,
            Paragraph,
            Shape,
            Sheet,
            Slide,
            Task,
            TextBlock,
        )

        fields: list[tuple[str, str]] = []
        match node:
            case Artifact(title=title):
                fields.append(("title", title))
            case Paragraph(text=text, style=style):
                fields.append(("text", text))
                fields.append(("style", style))
            case Heading(text=text, level=level):
                fields.append(("text", text))
                fields.append(("level", str(level)))
            case TextBlock(text=text, format=fmt):
                fields.append(("text", text))
                fields.append(("format", fmt))
            case Cell(value=value, row=row, col=col):
                fields.append(("value", str(value) if value is not None else ""))
                fields.append(("row", str(row)))
                fields.append(("col", str(col)))
            case FormulaCell(formula=formula, row=row, col=col):
                fields.append(("formula", formula))
                fields.append(("row", str(row)))
                fields.append(("col", str(col)))
            case Sheet(title=title, rows=rows, cols=cols):
                fields.append(("title", title))
                fields.append(("rows", str(rows)))
                fields.append(("cols", str(cols)))
            case CodeBlock(source=source, language=language):
                fields.append(("source", source))
                fields.append(("language", language))
            case Task(title=title, completed=completed, status=status):
                fields.append(("title", title))
                fields.append(("completed", str(completed)))
                fields.append(("status", status))
            case Slide(title=title, order=order):
                fields.append(("title", title))
                fields.append(("order", str(order)))
            case Shape(shape_type=shape_type):
                fields.append(("shape_type", shape_type))
            case Image(uri=uri, alt_text=alt_text):
                fields.append(("uri", uri))
                fields.append(("alt_text", alt_text))
            case ArtifactACL(default_role=default_role, public_read=public_read):
                if default_role is not None:
                    fields.append(("default_role", default_role))
                fields.append(("public_read", str(public_read)))
            case _:
                pass
        return fields
