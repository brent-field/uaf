"""State materializer — replays operations into current state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from uaf.core.edges import EdgeType
from uaf.core.operations import (
    CreateEdge,
    CreateNode,
    DeleteEdge,
    DeleteNode,
    MoveNode,
    ReorderChildren,
    UpdateNode,
)

if TYPE_CHECKING:
    from uaf.core.edges import Edge
    from uaf.core.node_id import EdgeId, NodeId, OperationId
    from uaf.db.operation_log import LogEntry, OperationLog


@dataclass
class MaterializedState:
    """Mutable projection of the current graph state."""

    nodes: dict[NodeId, Any] = field(default_factory=dict)  # NodeId -> NodeData
    edges: dict[EdgeId, Edge] = field(default_factory=dict)
    children_order: dict[NodeId, list[NodeId]] = field(default_factory=dict)
    node_last_op: dict[NodeId, OperationId] = field(default_factory=dict)


class StateMaterializer:
    """Replays operations from the log into a MaterializedState."""

    def __init__(self) -> None:
        self.state = MaterializedState()

    def apply(self, entry: LogEntry) -> None:
        """Dispatch an operation to the appropriate handler."""
        op = entry.operation
        match op:
            case CreateNode(node=node):
                self._handle_create_node(node, entry.operation_id)
            case UpdateNode(node=node):
                self._handle_update_node(node, entry.operation_id)
            case DeleteNode(node_id=node_id):
                self._handle_delete_node(node_id)
            case CreateEdge(edge=edge):
                self._handle_create_edge(edge)
            case DeleteEdge(edge_id=edge_id):
                self._handle_delete_edge(edge_id)
            case MoveNode(node_id=node_id, new_parent_id=new_parent_id):
                self._handle_move_node(node_id, new_parent_id)
            case ReorderChildren(parent_id=parent_id, new_order=new_order):
                self._handle_reorder_children(parent_id, new_order)

    def replay(self, log: OperationLog) -> None:
        """Full replay from genesis — clears state and applies all entries."""
        self.state = MaterializedState()
        for entry in log:
            self.apply(entry)

    def get_node(self, node_id: NodeId) -> Any | None:
        """Direct state lookup for a node."""
        return self.state.nodes.get(node_id)

    def get_edge(self, edge_id: EdgeId) -> Edge | None:
        """Direct state lookup for an edge."""
        return self.state.edges.get(edge_id)

    def get_children(self, parent_id: NodeId) -> list[NodeId]:
        """Return ordered child NodeIds for the given parent."""
        return list(self.state.children_order.get(parent_id, []))

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    def _handle_create_node(self, node: Any, op_id: OperationId) -> None:
        node_id: NodeId = node.meta.id
        self.state.nodes[node_id] = node
        self.state.node_last_op[node_id] = op_id

    def _handle_update_node(self, node: Any, op_id: OperationId) -> None:
        node_id: NodeId = node.meta.id
        self.state.nodes[node_id] = node
        self.state.node_last_op[node_id] = op_id

    def _handle_delete_node(self, node_id: NodeId) -> None:
        # Remove from nodes dict but do NOT cascade-delete children
        self.state.nodes.pop(node_id, None)
        self.state.node_last_op.pop(node_id, None)
        # Remove from any parent's children_order
        for children in self.state.children_order.values():
            if node_id in children:
                children.remove(node_id)
        # Remove the node's own children_order entry
        self.state.children_order.pop(node_id, None)

    def _handle_create_edge(self, edge: Edge) -> None:
        self.state.edges[edge.id] = edge
        # CONTAINS edges form the structural tree
        if edge.edge_type == EdgeType.CONTAINS:
            children = self.state.children_order.setdefault(edge.source, [])
            if edge.target not in children:
                children.append(edge.target)

    def _handle_delete_edge(self, edge_id: EdgeId) -> None:
        edge = self.state.edges.pop(edge_id, None)
        if edge is not None and edge.edge_type == EdgeType.CONTAINS:
            children = self.state.children_order.get(edge.source)
            if children is not None and edge.target in children:
                children.remove(edge.target)

    def _handle_move_node(self, node_id: NodeId, new_parent_id: NodeId) -> None:
        # Remove from all current parents
        for children in self.state.children_order.values():
            if node_id in children:
                children.remove(node_id)
        # Add to new parent
        children = self.state.children_order.setdefault(new_parent_id, [])
        if node_id not in children:
            children.append(node_id)

    def _handle_reorder_children(
        self, parent_id: NodeId, new_order: tuple[NodeId, ...]
    ) -> None:
        self.state.children_order[parent_id] = list(new_order)
