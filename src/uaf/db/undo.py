"""Undo/redo infrastructure — UndoGroup, UndoManager, and inverse computation."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from uaf.core.node_id import utc_now
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
    from collections.abc import Iterator

    from uaf.core.node_id import NodeId, OperationId
    from uaf.core.operations import Operation
    from uaf.db.materializer import MaterializedState
    from uaf.db.operation_log import LogEntry, OperationLog


# ---------------------------------------------------------------------------
# UndoGroup
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UndoGroup:
    """A group of operations that should be undone/redone together."""

    group_id: str
    op_ids: tuple[OperationId, ...]
    principal_id: str


# ---------------------------------------------------------------------------
# UndoManager
# ---------------------------------------------------------------------------


class UndoManager:
    """Per-principal undo/redo stack manager."""

    def __init__(self) -> None:
        self._undo_stacks: dict[str, list[UndoGroup]] = {}
        self._redo_stacks: dict[str, list[UndoGroup]] = {}
        self._current_group: str | None = None
        self._current_group_ops: list[OperationId] = []
        self._current_group_principal: str | None = None
        self._group_depth: int = 0

    def begin_group(self, principal_id: str) -> str:
        """Start a new undo group. Returns the group_id.

        Supports nesting: if a group is already open, the depth counter
        increments and the existing group keeps collecting operations.
        Only the outermost ``end_group`` finalises the group.
        """
        if self._current_group is not None:
            self._group_depth += 1
            return self._current_group
        group_id = uuid.uuid4().hex[:16]
        self._current_group = group_id
        self._current_group_ops = []
        self._current_group_principal = principal_id
        self._group_depth = 1
        return group_id

    def end_group(self) -> None:
        """Close the current group and push it to the undo stack.

        When nested, only the outermost call finalises the group.
        """
        if self._current_group is None:
            return
        self._group_depth -= 1
        if self._group_depth > 0:
            return
        if self._current_group_ops and self._current_group_principal is not None:
            group = UndoGroup(
                group_id=self._current_group,
                op_ids=tuple(self._current_group_ops),
                principal_id=self._current_group_principal,
            )
            stack = self._undo_stacks.setdefault(
                self._current_group_principal, [],
            )
            stack.append(group)
        self._current_group = None
        self._current_group_ops = []
        self._current_group_principal = None

    def record_op(self, op_id: OperationId, principal_id: str) -> None:
        """Record an operation. Auto-groups if no explicit group is open."""
        if self._current_group is not None:
            self._current_group_ops.append(op_id)
        else:
            # Auto-create a single-op group
            group = UndoGroup(
                group_id=uuid.uuid4().hex[:16],
                op_ids=(op_id,),
                principal_id=principal_id,
            )
            stack = self._undo_stacks.setdefault(principal_id, [])
            stack.append(group)

        # Clear redo stack on new action
        self._redo_stacks.pop(principal_id, None)

    def pop_undo(self, principal_id: str) -> UndoGroup | None:
        """Pop the most recent undo group, move it to the redo stack."""
        stack = self._undo_stacks.get(principal_id)
        if not stack:
            return None
        group = stack.pop()
        redo_stack = self._redo_stacks.setdefault(principal_id, [])
        redo_stack.append(group)
        return group

    def pop_redo(self, principal_id: str) -> UndoGroup | None:
        """Pop the most recent redo group, move it to the undo stack."""
        stack = self._redo_stacks.get(principal_id)
        if not stack:
            return None
        group = stack.pop()
        undo_stack = self._undo_stacks.setdefault(principal_id, [])
        undo_stack.append(group)
        return group

    @contextmanager
    def group(self, principal_id: str) -> Iterator[str]:
        """Context manager for undo groups."""
        group_id = self.begin_group(principal_id)
        try:
            yield group_id
        finally:
            self.end_group()


# ---------------------------------------------------------------------------
# Inverse computation
# ---------------------------------------------------------------------------


def _find_previous_node(
    node_id: NodeId,
    log: OperationLog,
    *,
    before_entry: LogEntry | None = None,
) -> Any | None:
    """Walk the log backwards to find the most recent version of a node."""
    found = False
    entries = list(log)
    for entry in reversed(entries):
        if before_entry is not None and not found:
            if entry.operation_id == before_entry.operation_id:
                found = True
            continue
        op = entry.operation
        match op:
            case CreateNode(node=node) | UpdateNode(node=node):
                if node.meta.id == node_id:
                    return node
            case _:
                pass
    return None


def _find_previous_edge(
    edge_id: Any,
    log: OperationLog,
    *,
    before_entry: LogEntry | None = None,
) -> Any | None:
    """Walk the log backwards to find the most recent version of an edge."""
    found = False
    entries = list(log)
    for entry in reversed(entries):
        if before_entry is not None and not found:
            if entry.operation_id == before_entry.operation_id:
                found = True
            continue
        op = entry.operation
        match op:
            case CreateEdge(edge=edge):
                if edge.id == edge_id:
                    return edge
            case _:
                pass
    return None


def _find_previous_parent(
    node_id: NodeId,
    log: OperationLog,
    state: MaterializedState,
    *,
    before_entry: LogEntry | None = None,
) -> NodeId | None:
    """Walk the log backwards to find the previous parent of a node."""
    found = False
    entries = list(log)
    for entry in reversed(entries):
        if before_entry is not None and not found:
            if entry.operation_id == before_entry.operation_id:
                found = True
            continue
        op = entry.operation
        match op:
            case MoveNode(node_id=nid, new_parent_id=pid):
                if nid == node_id:
                    return pid
            case _:
                pass
    # If no MoveNode found, look in CONTAINS edges for original parent
    for children_list in state.children_order.values():
        if node_id in children_list:
            # Return the parent that has this node as child
            for parent_id, children in state.children_order.items():
                if node_id in children:
                    return parent_id
            break
    return None


def _find_previous_order(
    parent_id: NodeId,
    log: OperationLog,
    *,
    before_entry: LogEntry | None = None,
) -> tuple[NodeId, ...] | None:
    """Walk the log backwards to find the previous child order."""
    found = False
    entries = list(log)
    for entry in reversed(entries):
        if before_entry is not None and not found:
            if entry.operation_id == before_entry.operation_id:
                found = True
            continue
        op = entry.operation
        match op:
            case ReorderChildren(parent_id=pid, new_order=order):
                if pid == parent_id:
                    return order
            case _:
                pass
    return None


def compute_inverse(
    entry: LogEntry,
    log: OperationLog,
    state: MaterializedState,
) -> list[Operation]:
    """Compute compensating operations for a single log entry.

    Returns a list of operations that, when applied, undo the effect of
    the original operation.
    """
    op = entry.operation
    now = utc_now()
    principal_id = op.principal_id
    result: list[Operation] = []

    match op:
        case CreateNode(node=node):
            node_id: NodeId = node.meta.id
            # Delete edges to/from this node
            for eid, edge in list(state.edges.items()):
                if edge.source == node_id or edge.target == node_id:
                    result.append(DeleteEdge(
                        edge_id=eid,
                        parent_ops=(),
                        timestamp=now,
                        principal_id=principal_id,
                    ))
            # Delete the node itself
            result.append(DeleteNode(
                node_id=node_id,
                parent_ops=(),
                timestamp=now,
                principal_id=principal_id,
            ))

        case DeleteNode(node_id=del_id):
            # Restore the node from log history
            old_node = _find_previous_node(del_id, log, before_entry=entry)
            if old_node is not None:
                result.append(CreateNode(
                    node=old_node,
                    parent_ops=(),
                    timestamp=now,
                    principal_id=principal_id,
                ))

        case UpdateNode(node=new_node):
            # Restore the previous version
            node_id_upd: NodeId = new_node.meta.id
            old_node = _find_previous_node(
                node_id_upd, log, before_entry=entry,
            )
            if old_node is not None:
                result.append(UpdateNode(
                    node=old_node,
                    parent_ops=(),
                    timestamp=now,
                    principal_id=principal_id,
                ))

        case CreateEdge(edge=edge):
            result.append(DeleteEdge(
                edge_id=edge.id,
                parent_ops=(),
                timestamp=now,
                principal_id=principal_id,
            ))

        case DeleteEdge(edge_id=eid):
            old_edge = _find_previous_edge(eid, log, before_entry=entry)
            if old_edge is not None:
                result.append(CreateEdge(
                    edge=old_edge,
                    parent_ops=(),
                    timestamp=now,
                    principal_id=principal_id,
                ))

        case MoveNode(node_id=nid, new_parent_id=_):
            old_parent = _find_previous_parent(
                nid, log, state, before_entry=entry,
            )
            if old_parent is not None:
                result.append(MoveNode(
                    node_id=nid,
                    new_parent_id=old_parent,
                    parent_ops=(),
                    timestamp=now,
                    principal_id=principal_id,
                ))

        case ReorderChildren(parent_id=pid, new_order=_):
            old_order = _find_previous_order(pid, log, before_entry=entry)
            if old_order is not None:
                result.append(ReorderChildren(
                    parent_id=pid,
                    new_order=old_order,
                    parent_ops=(),
                    timestamp=now,
                    principal_id=principal_id,
                ))

    return result


# ---------------------------------------------------------------------------
# Revert to point-in-time
# ---------------------------------------------------------------------------


def compute_revert_ops(
    target_time: Any,
    log: OperationLog,
    current_state: MaterializedState,
    principal_id: str,
) -> list[Operation]:
    """Compute ops to revert the graph from current_state to state-at-target_time.

    Replays the log up to target_time into a fresh materializer to build
    the target state, then diffs current vs target to generate compensating ops.
    """
    from uaf.db.materializer import StateMaterializer

    # 1. Replay log up to target_time
    target_mat = StateMaterializer()
    for entry in log:
        if entry.operation.timestamp <= target_time:
            target_mat.apply(entry)

    target = target_mat.state
    now = utc_now()
    result: list[Operation] = []

    # 2. Nodes in current but not in target => delete
    for nid in set(current_state.nodes) - set(target.nodes):
        # Delete edges first
        for eid, edge in list(current_state.edges.items()):
            if edge.source == nid or edge.target == nid:
                result.append(DeleteEdge(
                    edge_id=eid,
                    parent_ops=(),
                    timestamp=now,
                    principal_id=principal_id,
                ))
        result.append(DeleteNode(
            node_id=nid,
            parent_ops=(),
            timestamp=now,
            principal_id=principal_id,
        ))

    # 3. Nodes in target but not in current => create
    for nid in set(target.nodes) - set(current_state.nodes):
        result.append(CreateNode(
            node=target.nodes[nid],
            parent_ops=(),
            timestamp=now,
            principal_id=principal_id,
        ))

    # 4. Nodes in both but different => update
    for nid in set(current_state.nodes) & set(target.nodes):
        if current_state.nodes[nid] != target.nodes[nid]:
            result.append(UpdateNode(
                node=target.nodes[nid],
                parent_ops=(),
                timestamp=now,
                principal_id=principal_id,
            ))

    # 5. Edges in current but not in target => delete
    for eid in set(current_state.edges) - set(target.edges):
        if not any(
            isinstance(r, DeleteEdge) and r.edge_id == eid
            for r in result
        ):
            result.append(DeleteEdge(
                edge_id=eid,
                parent_ops=(),
                timestamp=now,
                principal_id=principal_id,
            ))

    # 6. Edges in target but not in current => create
    for eid in set(target.edges) - set(current_state.edges):
        result.append(CreateEdge(
            edge=target.edges[eid],
            parent_ops=(),
            timestamp=now,
            principal_id=principal_id,
        ))

    return result
