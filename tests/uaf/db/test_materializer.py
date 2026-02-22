"""Tests for the state materializer."""

from __future__ import annotations

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    Heading,
    NodeType,
    Paragraph,
    make_node_metadata,
)
from uaf.core.operations import (
    CreateEdge,
    CreateNode,
    DeleteEdge,
    DeleteNode,
    MoveNode,
    ReorderChildren,
    UpdateNode,
)
from uaf.db.materializer import StateMaterializer
from uaf.db.operation_log import LogEntry, OperationLog


def _make_entry(op: object) -> LogEntry:
    """Create a LogEntry with a dummy OperationId for direct materializer testing."""
    from uaf.core.operations import compute_operation_id

    op_id = compute_operation_id(op)  # type: ignore[arg-type]
    return LogEntry(operation_id=op_id, operation=op)  # type: ignore[arg-type]


def _artifact(title: str = "Doc") -> Artifact:
    return Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)


def _paragraph(text: str = "Hello") -> Paragraph:
    return Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text=text)


def _heading(text: str = "H1", level: int = 1) -> Heading:
    return Heading(meta=make_node_metadata(NodeType.HEADING), text=text, level=level)


def _contains_edge(source: NodeId, target: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=source,
        target=target,
        edge_type=EdgeType.CONTAINS,
        created_at=utc_now(),
    )


class TestCreateNode:
    def test_node_is_stored(self) -> None:
        mat = StateMaterializer()
        node = _artifact()
        entry = _make_entry(CreateNode(node=node, parent_ops=(), timestamp=utc_now()))
        mat.apply(entry)
        assert mat.get_node(node.meta.id) == node

    def test_node_last_op_tracked(self) -> None:
        mat = StateMaterializer()
        node = _artifact()
        entry = _make_entry(CreateNode(node=node, parent_ops=(), timestamp=utc_now()))
        mat.apply(entry)
        assert mat.state.node_last_op[node.meta.id] == entry.operation_id


class TestUpdateNode:
    def test_update_replaces_node(self) -> None:
        mat = StateMaterializer()
        node = _artifact("V1")
        entry1 = _make_entry(CreateNode(node=node, parent_ops=(), timestamp=utc_now()))
        mat.apply(entry1)

        updated = Artifact(meta=node.meta, title="V2")
        entry2 = _make_entry(UpdateNode(node=updated, parent_ops=(), timestamp=utc_now()))
        mat.apply(entry2)

        result = mat.get_node(node.meta.id)
        assert result is not None
        assert result.title == "V2"


class TestDeleteNode:
    def test_delete_removes_node(self) -> None:
        mat = StateMaterializer()
        node = _artifact()
        entry1 = _make_entry(CreateNode(node=node, parent_ops=(), timestamp=utc_now()))
        mat.apply(entry1)

        entry2 = _make_entry(DeleteNode(node_id=node.meta.id, parent_ops=(), timestamp=utc_now()))
        mat.apply(entry2)

        assert mat.get_node(node.meta.id) is None

    def test_delete_does_not_cascade(self) -> None:
        mat = StateMaterializer()
        parent = _artifact()
        child = _paragraph()
        edge = _contains_edge(parent.meta.id, child.meta.id)

        mat.apply(_make_entry(CreateNode(node=parent, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateNode(node=child, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateEdge(edge=edge, parent_ops=(), timestamp=utc_now())))

        # Delete parent — child should survive
        mat.apply(
            _make_entry(DeleteNode(node_id=parent.meta.id, parent_ops=(), timestamp=utc_now()))
        )
        assert mat.get_node(parent.meta.id) is None
        assert mat.get_node(child.meta.id) == child


class TestCreateEdge:
    def test_edge_is_stored(self) -> None:
        mat = StateMaterializer()
        parent = _artifact()
        child = _paragraph()
        edge = _contains_edge(parent.meta.id, child.meta.id)

        mat.apply(_make_entry(CreateNode(node=parent, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateNode(node=child, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateEdge(edge=edge, parent_ops=(), timestamp=utc_now())))

        assert mat.get_edge(edge.id) == edge

    def test_contains_updates_children_order(self) -> None:
        mat = StateMaterializer()
        parent = _artifact()
        child = _paragraph()
        edge = _contains_edge(parent.meta.id, child.meta.id)

        mat.apply(_make_entry(CreateNode(node=parent, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateNode(node=child, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateEdge(edge=edge, parent_ops=(), timestamp=utc_now())))

        children = mat.get_children(parent.meta.id)
        assert children == [child.meta.id]


class TestDeleteEdge:
    def test_delete_removes_edge(self) -> None:
        mat = StateMaterializer()
        parent = _artifact()
        child = _paragraph()
        edge = _contains_edge(parent.meta.id, child.meta.id)

        mat.apply(_make_entry(CreateNode(node=parent, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateNode(node=child, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateEdge(edge=edge, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(DeleteEdge(edge_id=edge.id, parent_ops=(), timestamp=utc_now())))

        assert mat.get_edge(edge.id) is None
        assert mat.get_children(parent.meta.id) == []


class TestMoveNode:
    def test_move_reparents(self) -> None:
        mat = StateMaterializer()
        parent1 = _artifact("P1")
        parent2 = _artifact("P2")
        child = _paragraph()
        edge = _contains_edge(parent1.meta.id, child.meta.id)

        mat.apply(_make_entry(CreateNode(node=parent1, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateNode(node=parent2, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateNode(node=child, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateEdge(edge=edge, parent_ops=(), timestamp=utc_now())))

        assert mat.get_children(parent1.meta.id) == [child.meta.id]

        mat.apply(_make_entry(MoveNode(
            node_id=child.meta.id, new_parent_id=parent2.meta.id,
            parent_ops=(), timestamp=utc_now(),
        )))

        assert mat.get_children(parent1.meta.id) == []
        assert mat.get_children(parent2.meta.id) == [child.meta.id]


class TestReorderChildren:
    def test_reorder(self) -> None:
        mat = StateMaterializer()
        parent = _artifact()
        c1 = _paragraph("P1")
        c2 = _heading("H1")

        mat.apply(_make_entry(CreateNode(node=parent, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateNode(node=c1, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateNode(node=c2, parent_ops=(), timestamp=utc_now())))
        mat.apply(_make_entry(CreateEdge(
            edge=_contains_edge(parent.meta.id, c1.meta.id),
            parent_ops=(), timestamp=utc_now(),
        )))
        mat.apply(_make_entry(CreateEdge(
            edge=_contains_edge(parent.meta.id, c2.meta.id),
            parent_ops=(), timestamp=utc_now(),
        )))

        assert mat.get_children(parent.meta.id) == [c1.meta.id, c2.meta.id]

        mat.apply(_make_entry(ReorderChildren(
            parent_id=parent.meta.id,
            new_order=(c2.meta.id, c1.meta.id),
            parent_ops=(), timestamp=utc_now(),
        )))

        assert mat.get_children(parent.meta.id) == [c2.meta.id, c1.meta.id]


class TestReplay:
    def test_replay_rebuilds_state(self) -> None:
        log = OperationLog()
        node = _artifact()
        id1 = log.append(CreateNode(node=node, parent_ops=(), timestamp=utc_now()))

        child = _paragraph()
        log.append(CreateNode(node=child, parent_ops=(id1,), timestamp=utc_now()))

        mat = StateMaterializer()
        mat.replay(log)

        assert mat.get_node(node.meta.id) == node
        assert mat.get_node(child.meta.id) == child

    def test_replay_clears_previous_state(self) -> None:
        log = OperationLog()
        node = _artifact()
        log.append(CreateNode(node=node, parent_ops=(), timestamp=utc_now()))

        mat = StateMaterializer()
        mat.replay(log)
        assert len(mat.state.nodes) == 1

        # Replay again — should still have exactly 1 node (not 2)
        mat.replay(log)
        assert len(mat.state.nodes) == 1
