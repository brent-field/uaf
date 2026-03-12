"""JournaledGraphDB — persistence wrapper around GraphDB."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from uaf.db.graph_db import GraphDB

if TYPE_CHECKING:
    from collections.abc import Iterator

    from uaf.core.edges import Edge
    from uaf.core.node_id import BlobId, EdgeId, NodeId, OperationId
    from uaf.core.nodes import NodeType
    from uaf.db.eavt import EAVTIndex
    from uaf.db.materializer import StateMaterializer
    from uaf.db.operation_log import LogEntry, OperationLog
    from uaf.db.store import Store


class JournaledGraphDB:
    """Persistence wrapper around GraphDB.

    Same public API as GraphDB. On each ``apply()``, the operation is
    written to the journal (write-ahead) before being applied to the
    in-memory GraphDB. On construction, if a journal exists, all
    operations are replayed to rebuild state.
    """

    def __init__(self, store: Store) -> None:
        self._store = store
        self._db = GraphDB()
        self._replay()

    # ------------------------------------------------------------------
    # Internal properties for SecureGraphDB compatibility
    # ------------------------------------------------------------------

    @property
    def _log(self) -> OperationLog:
        return self._db._log

    @property
    def _materializer(self) -> StateMaterializer:
        return self._db._materializer

    @property
    def _index(self) -> EAVTIndex:
        return self._db._index

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def apply(self, op: Any) -> OperationId:
        """Write operation to journal, then apply to in-memory GraphDB."""
        self._store.journal.append(op)
        op_id = self._db.apply(op)
        self._store.write_metadata(len(self._db._log))
        return op_id

    def create_node(
        self, node: Any, *, parent_ops: tuple[OperationId, ...] = ()
    ) -> NodeId:
        """Convenience: create a node and return its NodeId."""
        from uaf.core.node_id import utc_now
        from uaf.core.operations import CreateNode

        op = CreateNode(node=node, parent_ops=parent_ops, timestamp=utc_now())
        self.apply(op)
        return node.meta.id  # type: ignore[no-any-return]

    def update_node(
        self, node: Any, *, parent_ops: tuple[OperationId, ...] = ()
    ) -> None:
        """Convenience: update a node (full state replacement)."""
        from uaf.core.node_id import utc_now
        from uaf.core.operations import UpdateNode

        op = UpdateNode(node=node, parent_ops=parent_ops, timestamp=utc_now())
        self.apply(op)

    def delete_node(
        self, node_id: NodeId, *, parent_ops: tuple[OperationId, ...] = ()
    ) -> None:
        """Convenience: delete a node."""
        from uaf.core.node_id import utc_now
        from uaf.core.operations import DeleteNode

        op = DeleteNode(
            node_id=node_id, parent_ops=parent_ops, timestamp=utc_now()
        )
        self.apply(op)

    def create_edge(
        self, edge: Edge, *, parent_ops: tuple[OperationId, ...] = ()
    ) -> None:
        """Convenience: create an edge."""
        from uaf.core.node_id import utc_now
        from uaf.core.operations import CreateEdge

        op = CreateEdge(edge=edge, parent_ops=parent_ops, timestamp=utc_now())
        self.apply(op)

    def delete_edge(
        self, edge_id: EdgeId, *, parent_ops: tuple[OperationId, ...] = ()
    ) -> None:
        """Convenience: delete an edge."""
        from uaf.core.node_id import utc_now
        from uaf.core.operations import DeleteEdge

        op = DeleteEdge(
            edge_id=edge_id, parent_ops=parent_ops, timestamp=utc_now()
        )
        self.apply(op)

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    @contextmanager
    def action_group(self, principal_id: str) -> Iterator[str]:
        """Context manager grouping operations into a single undo step."""
        with self._db.action_group(principal_id) as group_id:
            yield group_id

    def undo(self, principal_id: str) -> list[OperationId]:
        """Undo the most recent action group. Compensating ops are journaled."""
        return self._db.undo(principal_id)

    def redo(self, principal_id: str) -> list[OperationId]:
        """Redo the most recently undone action group."""
        return self._db.redo(principal_id)

    # ------------------------------------------------------------------
    # Query (delegates to GraphDB)
    # ------------------------------------------------------------------

    def get_node(self, node_id: NodeId) -> Any | None:
        return self._db.get_node(node_id)

    def get_children(self, parent_id: NodeId) -> list[Any]:
        return self._db.get_children(parent_id)

    def get_parent(self, node_id: NodeId) -> Any | None:
        return self._db.get_parent(node_id)

    def get_references_to(self, target_id: NodeId) -> list[Any]:
        return self._db.get_references_to(target_id)

    def find_by_type(self, node_type: NodeType) -> list[Any]:
        return self._db.find_by_type(node_type)

    def find_by_attribute(self, attribute: str, value: str) -> list[Any]:
        return self._db.find_by_attribute(attribute, value)

    def get_edges_from(self, source_id: NodeId) -> list[Edge]:
        return self._db.get_edges_from(source_id)

    def count_nodes(self) -> int:
        return self._db.count_nodes()

    def count_edges(self) -> int:
        return self._db.count_edges()

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_history(self, node_id: NodeId) -> list[LogEntry]:
        return self._db.get_history(node_id)

    # ------------------------------------------------------------------
    # Tree traversal
    # ------------------------------------------------------------------

    def descendants(self, node_id: NodeId) -> set[NodeId]:
        return self._db.descendants(node_id)

    # ------------------------------------------------------------------
    # Blob storage (persisted to disk + in-memory)
    # ------------------------------------------------------------------

    def store_blob(self, data: bytes) -> BlobId:
        """Store blob to disk AND in-memory."""
        bid = self._store.store_blob(data)
        self._db._blobs[bid] = data
        return bid

    def get_blob(self, blob_id: BlobId) -> bytes | None:
        """Check in-memory first, then fall back to disk."""
        result = self._db._blobs.get(blob_id)
        if result is not None:
            return result
        return self._store.get_blob(blob_id)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def _replay(self) -> None:
        """Replay journal to rebuild in-memory state."""
        ops = self._store.journal.read_all()
        for op in ops:
            self._db.apply(op)
