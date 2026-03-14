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
    UndoEvent,
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
        mgr.record_op(oid, "user1", "art1")
        group = mgr.pop_undo("user1", "art1")
        assert group is not None
        assert group.op_ids == (oid,)
        assert group.principal_id == "user1"

    def test_explicit_group(self) -> None:
        mgr = UndoManager()
        oid1 = _fake_op_id("a")
        oid2 = _fake_op_id("b")
        mgr.begin_group("user1", "art1")
        mgr.record_op(oid1, "user1")
        mgr.record_op(oid2, "user1")
        mgr.end_group()
        group = mgr.pop_undo("user1", "art1")
        assert group is not None
        assert group.op_ids == (oid1, oid2)

    def test_context_manager_group(self) -> None:
        mgr = UndoManager()
        oid = _fake_op_id("c")
        with mgr.group("user1", "art1") as gid:
            assert isinstance(gid, str)
            mgr.record_op(oid, "user1")
        group = mgr.pop_undo("user1", "art1")
        assert group is not None
        assert group.op_ids == (oid,)

    def test_pop_undo_empty(self) -> None:
        mgr = UndoManager()
        assert mgr.pop_undo("user1", "art1") is None

    def test_pop_redo_empty(self) -> None:
        mgr = UndoManager()
        assert mgr.pop_redo("user1", "art1") is None

    def test_undo_moves_to_redo(self) -> None:
        mgr = UndoManager()
        oid = _fake_op_id("a")
        mgr.record_op(oid, "user1", "art1")
        mgr.pop_undo("user1", "art1")
        group = mgr.pop_redo("user1", "art1")
        assert group is not None
        assert group.op_ids == (oid,)

    def test_redo_moves_to_undo(self) -> None:
        mgr = UndoManager()
        oid = _fake_op_id("a")
        mgr.record_op(oid, "user1", "art1")
        mgr.pop_undo("user1", "art1")
        mgr.pop_redo("user1", "art1")
        group = mgr.pop_undo("user1", "art1")
        assert group is not None
        assert group.op_ids == (oid,)

    def test_new_action_clears_redo(self) -> None:
        mgr = UndoManager()
        oid1 = _fake_op_id("a")
        oid2 = _fake_op_id("b")
        mgr.record_op(oid1, "user1", "art1")
        mgr.pop_undo("user1", "art1")
        # redo stack has oid1
        mgr.record_op(oid2, "user1", "art1")
        # redo stack should be cleared
        assert mgr.pop_redo("user1", "art1") is None

    def test_per_principal_stacks(self) -> None:
        mgr = UndoManager()
        oid1 = _fake_op_id("a")
        oid2 = _fake_op_id("b")
        mgr.record_op(oid1, "user1", "art1")
        mgr.record_op(oid2, "user2", "art1")
        g1 = mgr.pop_undo("user1", "art1")
        g2 = mgr.pop_undo("user2", "art1")
        assert g1 is not None and g1.op_ids == (oid1,)
        assert g2 is not None and g2.op_ids == (oid2,)

    def test_empty_group_not_pushed(self) -> None:
        mgr = UndoManager()
        mgr.begin_group("user1", "art1")
        mgr.end_group()
        assert mgr.pop_undo("user1", "art1") is None

    def test_record_op_without_artifact_id_no_group(self) -> None:
        """record_op without artifact_id outside a group is a no-op."""
        mgr = UndoManager()
        oid = _fake_op_id("a")
        mgr.record_op(oid, "user1")
        assert mgr.pop_undo("user1", "art1") is None


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
        with db.action_group("u", "art1"):
            op = CreateNode(
                node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
            )
            db.apply(op)
        assert db.get_node(node.meta.id) is not None

        db.undo("u", "art1")
        assert db.get_node(node.meta.id) is None

    def test_redo_after_undo(self) -> None:
        db = GraphDB()
        node = _make_paragraph("hello")
        with db.action_group("u", "art1"):
            op = CreateNode(
                node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
            )
            db.apply(op)
        db.undo("u", "art1")
        assert db.get_node(node.meta.id) is None

        db.redo("u", "art1")
        # Redo re-applies the original CreateNode, restoring the node.
        assert db.get_node(node.meta.id) is not None

    def test_undo_update_node(self) -> None:
        db = GraphDB()
        node = _make_paragraph("v1")
        with db.action_group("u", "art1"):
            db.apply(CreateNode(
                node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))
        updated = Paragraph(meta=node.meta, text="v2")
        with db.action_group("u", "art1"):
            db.apply(UpdateNode(
                node=updated, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))
        result = db.get_node(node.meta.id)
        assert result is not None
        assert result.text == "v2"

        db.undo("u", "art1")
        result = db.get_node(node.meta.id)
        assert result is not None
        assert result.text == "v1"

    def test_undo_delete_node(self) -> None:
        db = GraphDB()
        node = _make_paragraph("hello")
        with db.action_group("u", "art1"):
            db.apply(CreateNode(
                node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))
        with db.action_group("u", "art1"):
            db.apply(DeleteNode(
                node_id=node.meta.id,
                parent_ops=(),
                timestamp=utc_now(),
                principal_id="u",
            ))
        assert db.get_node(node.meta.id) is None

        db.undo("u", "art1")
        assert db.get_node(node.meta.id) is not None

    def test_undo_with_no_history(self) -> None:
        db = GraphDB()
        result = db.undo("u", "art1")
        assert result == []

    def test_redo_with_no_history(self) -> None:
        db = GraphDB()
        result = db.redo("u", "art1")
        assert result == []

    def test_action_group(self) -> None:
        db = GraphDB()
        n1 = _make_paragraph("a")
        n2 = _make_paragraph("b")
        with db.action_group("u", "art1"):
            db.apply(CreateNode(
                node=n1, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))
            db.apply(CreateNode(
                node=n2, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))

        assert db.get_node(n1.meta.id) is not None
        assert db.get_node(n2.meta.id) is not None

        # Single undo should revert both
        db.undo("u", "art1")
        assert db.get_node(n1.meta.id) is None
        assert db.get_node(n2.meta.id) is None

    def test_undo_does_not_record_to_undo_stack(self) -> None:
        """Compensating ops from undo should not create new undo groups."""
        db = GraphDB()
        node = _make_paragraph("test")
        with db.action_group("u", "art1"):
            db.apply(CreateNode(
                node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))
        # Undo stack has 1 group
        db.undo("u", "art1")
        # After undo, undo stack should be empty (group moved to redo)
        result = db.undo("u", "art1")
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
        with db.action_group("u", "art1"):
            db.apply(CreateNode(
                node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))
        updated = Paragraph(meta=node.meta, text="v2")
        with db.action_group("u", "art1"):
            db.apply(UpdateNode(
                node=updated, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))
        result = db.get_node(node.meta.id)
        assert result is not None
        assert result.text == "v2"

        # Undo should revert to v1
        db.undo("u", "art1")
        result = db.get_node(node.meta.id)
        assert result is not None
        assert result.text == "v1"

        # Redo should restore v2
        db.redo("u", "art1")
        result = db.get_node(node.meta.id)
        assert result is not None
        assert result.text == "v2"

    def test_redo_restores_create(self) -> None:
        """Create a node, undo (deletes it), redo, verify it exists again."""
        db = GraphDB()
        node = _make_paragraph("restored")
        with db.action_group("u", "art1"):
            db.apply(CreateNode(
                node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))
        assert db.get_node(node.meta.id) is not None

        db.undo("u", "art1")
        assert db.get_node(node.meta.id) is None

        db.redo("u", "art1")
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
        with mgr.group("user1", "art1"):
            mgr.record_op(oid1, "user1")
            with mgr.group("user1", "art1"):
                mgr.record_op(oid2, "user1")
                mgr.record_op(oid3, "user1")
        group = mgr.pop_undo("user1", "art1")
        assert group is not None
        assert group.op_ids == (oid1, oid2, oid3)
        # Only one group should have been created
        assert mgr.pop_undo("user1", "art1") is None


# ---------------------------------------------------------------------------
# Per-artifact undo/redo tests
# ---------------------------------------------------------------------------


class TestPerArtifactUndo:
    """Verify undo/redo is scoped per-artifact."""

    def test_undo_scoped_to_artifact(self) -> None:
        """Ops in artifact A and B; undo on A only affects A."""
        db = GraphDB()
        node_a = _make_paragraph("artA")
        node_b = _make_paragraph("artB")
        with db.action_group("u", "art_a"):
            db.apply(CreateNode(
                node=node_a, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))
        with db.action_group("u", "art_b"):
            db.apply(CreateNode(
                node=node_b, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))

        assert db.get_node(node_a.meta.id) is not None
        assert db.get_node(node_b.meta.id) is not None

        # Undo only artifact A
        db.undo("u", "art_a")
        assert db.get_node(node_a.meta.id) is None
        assert db.get_node(node_b.meta.id) is not None  # B untouched

    def test_undo_empty_returns_none(self) -> None:
        """pop_undo for artifact with no ops returns None."""
        mgr = UndoManager()
        assert mgr.pop_undo("user1", "nonexistent") is None

    def test_redo_scoped_to_artifact(self) -> None:
        """Undo in artifact A creates redo for A; redo on B returns nothing."""
        db = GraphDB()
        node_a = _make_paragraph("artA")
        with db.action_group("u", "art_a"):
            db.apply(CreateNode(
                node=node_a, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))

        db.undo("u", "art_a")
        assert db.get_node(node_a.meta.id) is None

        # Redo on art_b should do nothing
        result = db.redo("u", "art_b")
        assert result == []
        assert db.get_node(node_a.meta.id) is None

        # Redo on art_a should restore
        db.redo("u", "art_a")
        assert db.get_node(node_a.meta.id) is not None

    def test_redo_cleared_per_artifact(self) -> None:
        """New op in A clears A's redo stack; B's redo stack untouched."""
        mgr = UndoManager()
        oid1 = _fake_op_id("a")
        oid2 = _fake_op_id("b")
        oid3 = _fake_op_id("c")

        mgr.record_op(oid1, "u", "art_a")
        mgr.record_op(oid2, "u", "art_b")

        # Create redo stacks by undoing
        mgr.pop_undo("u", "art_a")
        mgr.pop_undo("u", "art_b")

        # New op on art_a clears art_a redo only
        mgr.record_op(oid3, "u", "art_a")
        assert mgr.pop_redo("u", "art_a") is None  # cleared
        assert mgr.pop_redo("u", "art_b") is not None  # still there

    def test_cross_artifact_undo_redo_roundtrip(self) -> None:
        """Edit A, edit B, undo A, redo A -- B stays intact throughout."""
        db = GraphDB()
        node_a = _make_paragraph("A-text")
        node_b = _make_paragraph("B-text")

        with db.action_group("u", "art_a"):
            db.apply(CreateNode(
                node=node_a, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))
        with db.action_group("u", "art_b"):
            db.apply(CreateNode(
                node=node_b, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))

        # Undo A
        db.undo("u", "art_a")
        assert db.get_node(node_a.meta.id) is None
        assert db.get_node(node_b.meta.id) is not None

        # Redo A
        db.redo("u", "art_a")
        assert db.get_node(node_a.meta.id) is not None
        assert db.get_node(node_b.meta.id) is not None

    def test_undo_exhausted_returns_empty(self) -> None:
        """Undo all ops in artifact, next undo returns empty (no-op)."""
        db = GraphDB()
        node = _make_paragraph("only")
        with db.action_group("u", "art_a"):
            db.apply(CreateNode(
                node=node, parent_ops=(), timestamp=utc_now(), principal_id="u",
            ))

        db.undo("u", "art_a")
        assert db.get_node(node.meta.id) is None

        # Second undo should return empty
        result = db.undo("u", "art_a")
        assert result == []


# ---------------------------------------------------------------------------
# UndoManager callback tests
# ---------------------------------------------------------------------------


class TestUndoManagerCallback:
    """Verify on_event callback is invoked with correct UndoEvent objects."""

    def test_auto_group_emits_created(self) -> None:
        events: list[UndoEvent] = []
        mgr = UndoManager(on_event=events.append)
        oid = _fake_op_id("a")
        mgr.record_op(oid, "user1", "art1")
        assert len(events) == 1
        assert events[0].event_type == "created"
        assert events[0].op_ids == (oid,)

    def test_explicit_group_emits_created(self) -> None:
        events: list[UndoEvent] = []
        mgr = UndoManager(on_event=events.append)
        oid1 = _fake_op_id("a")
        oid2 = _fake_op_id("b")
        mgr.begin_group("user1", "art1")
        mgr.record_op(oid1, "user1")
        mgr.record_op(oid2, "user1")
        mgr.end_group()
        # Only one "created" event for the whole group
        created = [e for e in events if e.event_type == "created"]
        assert len(created) == 1
        assert created[0].op_ids == (oid1, oid2)

    def test_pop_undo_emits_undone(self) -> None:
        events: list[UndoEvent] = []
        mgr = UndoManager(on_event=events.append)
        oid = _fake_op_id("a")
        mgr.record_op(oid, "user1", "art1")
        events.clear()
        mgr.pop_undo("user1", "art1")
        assert len(events) == 1
        assert events[0].event_type == "undone"

    def test_pop_redo_emits_redone(self) -> None:
        events: list[UndoEvent] = []
        mgr = UndoManager(on_event=events.append)
        oid = _fake_op_id("a")
        mgr.record_op(oid, "user1", "art1")
        mgr.pop_undo("user1", "art1")
        events.clear()
        mgr.pop_redo("user1", "art1")
        assert len(events) == 1
        assert events[0].event_type == "redone"

    def test_redo_cleared_emits_event(self) -> None:
        events: list[UndoEvent] = []
        mgr = UndoManager(on_event=events.append)
        oid1 = _fake_op_id("a")
        oid2 = _fake_op_id("b")
        mgr.record_op(oid1, "user1", "art1")
        mgr.pop_undo("user1", "art1")
        events.clear()
        # New op should clear redo and emit redo_cleared
        mgr.record_op(oid2, "user1", "art1")
        cleared = [e for e in events if e.event_type == "redo_cleared"]
        assert len(cleared) == 1
        assert cleared[0].op_ids == (oid1,)


# ---------------------------------------------------------------------------
# UndoManager replay_events tests
# ---------------------------------------------------------------------------


class TestUndoManagerReplay:
    """Verify replay_events rebuilds stacks correctly."""

    def test_replay_created(self) -> None:
        mgr = UndoManager()
        oid = _fake_op_id("a")
        mgr.replay_events([UndoEvent(
            event_type="created",
            group_id="g1",
            op_ids=(oid,),
            principal_id="user1",
            artifact_id="art1",
        )])
        group = mgr.pop_undo("user1", "art1")
        assert group is not None
        assert group.op_ids == (oid,)

    def test_replay_created_then_undone(self) -> None:
        mgr = UndoManager()
        oid = _fake_op_id("a")
        mgr.replay_events([
            UndoEvent(
                event_type="created",
                group_id="g1",
                op_ids=(oid,),
                principal_id="user1",
                artifact_id="art1",
            ),
            UndoEvent(
                event_type="undone",
                group_id="g1",
                op_ids=(oid,),
                principal_id="user1",
                artifact_id="art1",
            ),
        ])
        # Undo stack should be empty
        assert mgr.pop_undo("user1", "art1") is None
        # Redo stack should have the group
        group = mgr.pop_redo("user1", "art1")
        assert group is not None
        assert group.op_ids == (oid,)

    def test_replay_created_undone_redone(self) -> None:
        mgr = UndoManager()
        oid = _fake_op_id("a")
        mgr.replay_events([
            UndoEvent(
                event_type="created",
                group_id="g1",
                op_ids=(oid,),
                principal_id="user1",
                artifact_id="art1",
            ),
            UndoEvent(
                event_type="undone",
                group_id="g1",
                op_ids=(oid,),
                principal_id="user1",
                artifact_id="art1",
            ),
            UndoEvent(
                event_type="redone",
                group_id="g1",
                op_ids=(oid,),
                principal_id="user1",
                artifact_id="art1",
            ),
        ])
        # Should be back on undo stack
        group = mgr.pop_undo("user1", "art1")
        assert group is not None
        assert group.op_ids == (oid,)

    def test_replay_redo_cleared(self) -> None:
        mgr = UndoManager()
        oid = _fake_op_id("a")
        mgr.replay_events([
            UndoEvent(
                event_type="created",
                group_id="g1",
                op_ids=(oid,),
                principal_id="user1",
                artifact_id="art1",
            ),
            UndoEvent(
                event_type="undone",
                group_id="g1",
                op_ids=(oid,),
                principal_id="user1",
                artifact_id="art1",
            ),
            UndoEvent(
                event_type="redo_cleared",
                group_id="g1",
                op_ids=(oid,),
                principal_id="user1",
                artifact_id="art1",
            ),
        ])
        # Redo stack should be empty
        assert mgr.pop_redo("user1", "art1") is None
