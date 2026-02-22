"""Query engine — read-only high-level API over MaterializedState + EAVTIndex."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from uaf.core.edges import EdgeType

if TYPE_CHECKING:
    from uaf.core.edges import Edge
    from uaf.core.node_id import NodeId
    from uaf.core.nodes import NodeType
    from uaf.db.eavt import EAVTIndex
    from uaf.db.materializer import MaterializedState


class QueryEngine:
    """Read-only query API composing MaterializedState and EAVTIndex."""

    def __init__(self, state: MaterializedState, index: EAVTIndex) -> None:
        self._state = state
        self._index = index

    def get_node(self, node_id: NodeId) -> Any | None:
        """Look up a node by ID. Returns None if not found."""
        return self._state.nodes.get(node_id)

    def get_children(self, parent_id: NodeId) -> list[Any]:
        """Return ordered child nodes for the given parent."""
        child_ids = self._state.children_order.get(parent_id, [])
        result: list[Any] = []
        for cid in child_ids:
            node = self._state.nodes.get(cid)
            if node is not None:
                result.append(node)
        return result

    def get_parent(self, node_id: NodeId) -> Any | None:
        """Return the first CONTAINS-parent of a node, or None."""
        for edge in self._state.edges.values():
            if edge.target == node_id and edge.edge_type == EdgeType.CONTAINS:
                return self._state.nodes.get(edge.source)
        return None

    def get_references_to(self, target_id: NodeId) -> list[Any]:
        """Return all nodes that reference target_id via REFERENCES edges."""
        result: list[Any] = []
        for edge in self._state.edges.values():
            if edge.target == target_id and edge.edge_type == EdgeType.REFERENCES:
                node = self._state.nodes.get(edge.source)
                if node is not None:
                    result.append(node)
        return result

    def find_by_type(self, node_type: NodeType) -> list[Any]:
        """Find all nodes of a given type via the AVET index."""
        datoms = self._index.attr_value("node_type", node_type.value)
        result: list[Any] = []
        seen: set[str] = set()
        for d in datoms:
            if d.entity not in seen:
                seen.add(d.entity)
                # Look up by entity string — need to find the NodeId
                node = self._find_node_by_entity_str(d.entity)
                if node is not None:
                    result.append(node)
        return result

    def find_by_attribute(self, attribute: str, value: str) -> list[Any]:
        """Find all nodes where attribute equals value via the AVET index."""
        datoms = self._index.attr_value(attribute, value)
        result: list[Any] = []
        seen: set[str] = set()
        for d in datoms:
            if d.entity not in seen:
                seen.add(d.entity)
                node = self._find_node_by_entity_str(d.entity)
                if node is not None:
                    result.append(node)
        return result

    def get_edges_from(self, source_id: NodeId) -> list[Edge]:
        """Return all edges originating from source_id."""
        return [e for e in self._state.edges.values() if e.source == source_id]

    def count_nodes(self) -> int:
        """Return the number of nodes in the graph."""
        return len(self._state.nodes)

    def count_edges(self) -> int:
        """Return the number of edges in the graph."""
        return len(self._state.edges)

    def _find_node_by_entity_str(self, entity: str) -> Any | None:
        """Resolve an entity string (UUID) to a node."""
        for node_id, node in self._state.nodes.items():
            if str(node_id.value) == entity:
                return node
        return None
