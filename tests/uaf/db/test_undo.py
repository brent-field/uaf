"""Tests for the undo/redo system."""

from __future__ import annotations

from datetime import timedelta

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, OperationId, utc_now
from uaf.core.nodes import Artifact, NodeType, Paragraph, make_node_metadata
from uaf.core.operations import (
    CreateEdge,
    CreateNode,
    DeleteEdge,
    DeleteNode,
    ReorderChildren,
    UpdateNode,
)
from uaf.db.graph_db import GraphDB
from uaf.db.undo import (
    UndoManager,
    compute_inverse,
    compute_revert_ops,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paragraph(text: str = "hello") -> Paragraph:
    return Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text=text)


def _make_artifact(title: str = "Test") -> Artifact:
    return Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)


def _make_edge(source: NodeId, target: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=source,
        target=target,
        edge_type=EdgeType.CONTAINS,
        created_at=utc_now(),
    )


def _fake_op_id(val: str = "a") -> OperationId:
    """Create a fake OperationId for testing."""
    return OperationId(hex_digest=val.ljust(64, "0"))


# ---------------------------------------------------------------------------
# UndoManager tests
# ---------------------------------------------------------------------------


class TestUndoManager:
    def test_record_op_auto_group(self) -> None:
        mgr = UndoManager()
        oid = _fake_op_id("a")
        mgr.record_op(oid, "user1")
        group = mgr.pop_undo("user1")
        assert group is not None
        assert group.op_ids == (oid,)
        assert group.principal_id == "user1"

    def test_explicit_group(self) -> None:
        mgr = UndoManager()
        oid1 = _fake_op_id("a")
        oid2 = _fake_op_id("b")
        mgr.begin_group("user1")
        mgr.record_op(oid1, "user1")
        mgr.record_op(oid2, "user1")
        mgr.end_group()
        group = mgr.pop_undo("user1")
        assert group is not None
        assert group.op_ids == (oid1, oid2)

    def test_context_manager_group(self) -> None:
        mgr = UndoManager()
        oid = _fake_op_id("c")
        with mgr.group("user1") as gid:
            assert isinstance(gid, str)
            mgr.record_op(oid, "user1")
        group = mgr.pop_undo("user1")
        assert group is not None
        assert group.op_ids == (oid,)

    def test_pop_undo_empty(self) -> None:
        mgr = UndoManager()
        assert mgr.pop_undo("user1") is None

    def test_pop_redo_empty(self) -> None:
        mgr = UndoManager()
        assert mgr.pop_redo("user1") is None

    def test_undo_moves_to_redo(self) -> None:
        mgr = UndoManager()
        oid = _fake_op_id("a")
        mgr.record_op(oid, "user1")
        mgr.pop_undo("user1")
        group = mgr.pop_redo("user1")
        assert group is not None
        assert group.op_ids == (oid,)

    def test_redo_moves_to_undo(self) -> None:
        mgr = UndoManager()
        oid = _fake_op_id("a")
        mgr.record_op(oid, "user1")
        mgr.pop_undo("user1")
        mgr.pop_redo("user1")
        group = mgr.pop_undo("user1")
        assert group is not None
        assert group.op_ids == (oid,)

    def test_new_action_clears_redo(self) -> None:
        mgr = UndoManager()
        oid1 = _fake_op_id("a")
        oid2 = _fake_op_id("b")
        mgr.record_op(oid1, "user1")
        mgr.pop_undo("user1")
        # redo stack has oid1
        mgr.record_op(oid2, "user1")
        # redo stack should be cleared
        assert mgr.pop_redo("user1") is None

    def test_per_principal_stacks(self) -> None:
        mgr = UndoManager()
        oid1 = _fake_op_id("a")
        oid2 = _fake_op_id("b")
        mgr.record_op(oid1, "user1")
        mgr.record_op(oid2, "user2")
        g1 = mgr.pop_undo("user1")
        g2 = mgr.pop_undo("user2")
        assert g1 is not None and g1.op_ids == (oid1,)
        assert g2 is not None and g2.op_ids == (oid2,)

    def test_empty_group_not_pushed(self) -> None:
        mgr = UndoManager()
        mgr.begin_group("user1")
        mgr.end_group()
        assert mgr.pop_undo("user1") is None


# ---------------------------------------------------------------------------
# compute_inverse tests
# ---------------------------------------------------------------------------


class TestComputeInverse:
    def test_inverse_create_node(self) -> None:
        db = GraphDB()
        node = _make_paragraph("test")
        op = CreateNode(
            node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
        )
        op_id = db.apply(op)
        entry = db._log.get(op_id)
        assert entry is not None
        inverses = compute_inverse(entry, db._log, db._materializer.state)
        # Should produce DeleteNode
        assert len(inverses) >= 1
        assert any(isinstance(inv, DeleteNode) for inv in inverses)

    def test_inverse_delete_node(self) -> None:
        db = GraphDB()
        node = _make_paragraph("test")
        create_op = CreateNode(
            node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
        )
        db.apply(create_op)
        del_op = DeleteNode(
            node_id=node.meta.id,
            parent_ops=(),
            timestamp=utc_now(),
            principal_id="u",
        )
        del_id = db.apply(del_op)
        entry = db._log.get(del_id)
        assert entry is not None
        inverses = compute_inverse(entry, db._log, db._materializer.state)
        assert len(inverses) == 1
        assert isinstance(inverses[0], CreateNode)

    def test_inverse_update_node(self) -> None:
        db = GraphDB()
        node = _make_paragraph("v1")
        create_op = CreateNode(
            node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
        )
        db.apply(create_op)
        updated = Paragraph(meta=node.meta, text="v2")
        update_op = UpdateNode(
            node=updated, parent_ops=(), timestamp=utc_now(), principal_id="u",
        )
        upd_id = db.apply(update_op)
        entry = db._log.get(upd_id)
        assert entry is not None
        inverses = compute_inverse(entry, db._log, db._materializer.state)
        assert len(inverses) == 1
        assert isinstance(inverses[0], UpdateNode)
        assert inverses[0].node.text == "v1"

    def test_inverse_create_edge(self) -> None:
        db = GraphDB()
        n1 = _make_paragraph("a")
        n2 = _make_paragraph("b")
        db.apply(CreateNode(
            node=n1, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        db.apply(CreateNode(
            node=n2, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        edge = _make_edge(n1.meta.id, n2.meta.id)
        eid = db.apply(CreateEdge(
            edge=edge, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        entry = db._log.get(eid)
        assert entry is not None
        inverses = compute_inverse(entry, db._log, db._materializer.state)
        assert len(inverses) == 1
        assert isinstance(inverses[0], DeleteEdge)

    def test_inverse_delete_edge(self) -> None:
        db = GraphDB()
        n1 = _make_paragraph("a")
        n2 = _make_paragraph("b")
        db.apply(CreateNode(
            node=n1, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        db.apply(CreateNode(
            node=n2, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        edge = _make_edge(n1.meta.id, n2.meta.id)
        db.apply(CreateEdge(
            edge=edge, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        del_id = db.apply(DeleteEdge(
            edge_id=edge.id,
            parent_ops=(),
            timestamp=utc_now(),
            principal_id="u",
        ))
        entry = db._log.get(del_id)
        assert entry is not None
        inverses = compute_inverse(entry, db._log, db._materializer.state)
        assert len(inverses) == 1
        assert isinstance(inverses[0], CreateEdge)

    def test_inverse_reorder_children(self) -> None:
        db = GraphDB()
        parent = _make_paragraph("p")
        c1 = _make_paragraph("c1")
        c2 = _make_paragraph("c2")
        for n in [parent, c1, c2]:
            db.apply(CreateNode(
                node=n, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))
        # Set initial order
        initial_order = (c1.meta.id, c2.meta.id)
        db.apply(ReorderChildren(
            parent_id=parent.meta.id,
            new_order=initial_order,
            parent_ops=(),
            timestamp=utc_now(),
            principal_id="u",
        ))
        # Reorder
        new_order = (c2.meta.id, c1.meta.id)
        rid = db.apply(ReorderChildren(
            parent_id=parent.meta.id,
            new_order=new_order,
            parent_ops=(),
            timestamp=utc_now(),
            principal_id="u",
        ))
        entry = db._log.get(rid)
        assert entry is not None
        inverses = compute_inverse(entry, db._log, db._materializer.state)
        assert len(inverses) == 1
        assert isinstance(inverses[0], ReorderChildren)
        assert inverses[0].new_order == initial_order


# ---------------------------------------------------------------------------
# GraphDB undo/redo integration tests
# ---------------------------------------------------------------------------


class TestGraphDBUndoRedo:
    def test_undo_create_node(self) -> None:
        db = GraphDB()
        node = _make_paragraph("hello")
        op = CreateNode(
            node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
        )
        db.apply(op)
        assert db.get_node(node.meta.id) is not None

        db.undo("u")
        assert db.get_node(node.meta.id) is None

    def test_redo_after_undo(self) -> None:
        db = GraphDB()
        node = _make_paragraph("hello")
        op = CreateNode(
            node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
        )
        db.apply(op)
        db.undo("u")
        assert db.get_node(node.meta.id) is None

        db.redo("u")
        # Redo re-applies the original CreateNode, restoring the node.
        assert db.get_node(node.meta.id) is not None

    def test_undo_update_node(self) -> None:
        db = GraphDB()
        node = _make_paragraph("v1")
        db.apply(CreateNode(
            node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        updated = Paragraph(meta=node.meta, text="v2")
        db.apply(UpdateNode(
            node=updated, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        result = db.get_node(node.meta.id)
        assert result is not None
        assert result.text == "v2"

        db.undo("u")
        result = db.get_node(node.meta.id)
        assert result is not None
        assert result.text == "v1"

    def test_undo_delete_node(self) -> None:
        db = GraphDB()
        node = _make_paragraph("hello")
        db.apply(CreateNode(
            node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        db.apply(DeleteNode(
            node_id=node.meta.id,
            parent_ops=(),
            timestamp=utc_now(),
            principal_id="u",
        ))
        assert db.get_node(node.meta.id) is None

        db.undo("u")
        assert db.get_node(node.meta.id) is not None

    def test_undo_with_no_history(self) -> None:
        db = GraphDB()
        result = db.undo("u")
        assert result == []

    def test_redo_with_no_history(self) -> None:
        db = GraphDB()
        result = db.redo("u")
        assert result == []

    def test_action_group(self) -> None:
        db = GraphDB()
        n1 = _make_paragraph("a")
        n2 = _make_paragraph("b")
        with db.action_group("u"):
            db.apply(CreateNode(
                node=n1, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))
            db.apply(CreateNode(
                node=n2, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))

        assert db.get_node(n1.meta.id) is not None
        assert db.get_node(n2.meta.id) is not None

        # Single undo should revert both
        db.undo("u")
        assert db.get_node(n1.meta.id) is None
        assert db.get_node(n2.meta.id) is None

    def test_undo_does_not_record_to_undo_stack(self) -> None:
        """Compensating ops from undo should not create new undo groups."""
        db = GraphDB()
        node = _make_paragraph("test")
        db.apply(CreateNode(
            node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        # Undo stack has 1 group
        db.undo("u")
        # After undo, undo stack should be empty (group moved to redo)
        result = db.undo("u")
        assert result == []


# ---------------------------------------------------------------------------
# compute_revert_ops tests
# ---------------------------------------------------------------------------


class TestComputeRevertOps:
    def test_revert_to_empty_state(self) -> None:
        db = GraphDB()
        t0 = utc_now() - timedelta(hours=1)
        node = _make_paragraph("hello")
        db.apply(CreateNode(
            node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))

        ops = compute_revert_ops(
            t0, db._log, db._materializer.state, "u",
        )
        # Should produce DeleteNode to go back to empty state
        assert len(ops) >= 1
        assert any(isinstance(op, DeleteNode) for op in ops)

    def test_revert_creates_missing_nodes(self) -> None:
        db = GraphDB()
        node = _make_paragraph("hello")
        t_create = utc_now()
        db.apply(CreateNode(
            node=node, parent_ops=(), timestamp=t_create, principal_id="u",
        ))
        # Use a future timestamp for the delete so revert target
        # (t_create) includes the create but not the delete
        future = t_create + timedelta(seconds=10)
        db.apply(DeleteNode(
            node_id=node.meta.id,
            parent_ops=(),
            timestamp=future,
            principal_id="u",
        ))

        ops = compute_revert_ops(
            t_create, db._log, db._materializer.state, "u",
        )
        # Should produce CreateNode to restore the deleted node
        assert any(isinstance(op, CreateNode) for op in ops)


# ---------------------------------------------------------------------------
# Redo bug regression tests
# ---------------------------------------------------------------------------


class TestRedoBug:
    """Verify that redo re-applies the original operations (not inverses)."""

    def test_redo_restores_update(self) -> None:
        """Create a node, update it, undo, verify old value, redo, verify new."""
        db = GraphDB()
        node = _make_paragraph("v1")
        db.apply(CreateNode(
            node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        updated = Paragraph(meta=node.meta, text="v2")
        db.apply(UpdateNode(
            node=updated, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        result = db.get_node(node.meta.id)
        assert result is not None
        assert result.text == "v2"

        # Undo should revert to v1
        db.undo("u")
        result = db.get_node(node.meta.id)
        assert result is not None
        assert result.text == "v1"

        # Redo should restore v2
        db.redo("u")
        result = db.get_node(node.meta.id)
        assert result is not None
        assert result.text == "v2"

    def test_redo_restores_create(self) -> None:
        """Create a node, undo (deletes it), redo, verify it exists again."""
        db = GraphDB()
        node = _make_paragraph("restored")
        db.apply(CreateNode(
            node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
        ))
        assert db.get_node(node.meta.id) is not None

        db.undo("u")
        assert db.get_node(node.meta.id) is None

        db.redo("u")
        result = db.get_node(node.meta.id)
        assert result is not None
        assert result.text == "restored"


# ---------------------------------------------------------------------------
# Nested action group tests
# ---------------------------------------------------------------------------


class TestNestedActionGroups:
    """Verify that nested action groups collapse into a single undo step."""

    def test_nested_groups_produce_single_undo(self) -> None:
        mgr = UndoManager()
        oid1 = _fake_op_id("a")
        oid2 = _fake_op_id("b")
        oid3 = _fake_op_id("c")
        with mgr.group("user1"):
            mgr.record_op(oid1, "user1")
            with mgr.group("user1"):
                mgr.record_op(oid2, "user1")
                mgr.record_op(oid3, "user1")
        group = mgr.pop_undo("user1")
        assert group is not None
        assert group.op_ids == (oid1, oid2, oid3)
        # Only one group should have been created
        assert mgr.pop_undo("user1") is None
