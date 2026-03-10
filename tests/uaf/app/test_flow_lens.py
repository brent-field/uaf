"""Tests for FlowLens — project management with Gantt, Dependency, DAG, Kanban views."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from uaf.app.lenses import Lens
from uaf.app.lenses.actions import (
    CreateTask,
    CreateTaskGroup,
    DeleteNode,
    RemoveDependency,
    ReorderNodes,
    SetDateRange,
    SetDependency,
    SetDueDate,
    ToggleTask,
    UpdateTask,
)
from uaf.app.lenses.flow_lens import FlowLens
from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import Artifact, NodeType, Task, make_node_metadata
from uaf.db.graph_db import GraphDB
from uaf.security.auth import LocalAuthProvider, PasswordCredentials
from uaf.security.secure_graph_db import SecureGraphDB


def _setup() -> tuple[SecureGraphDB, object, NodeId]:
    """Create a SecureGraphDB, authenticate, create a project artifact."""
    db = GraphDB()
    auth = LocalAuthProvider()
    sdb = SecureGraphDB(db, auth)
    principal = auth.create_principal("TestUser", "secret")
    session = sdb.authenticate(PasswordCredentials(principal_id=principal.id, password="secret"))

    # Create project artifact
    art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title="Test Project")
    art_id = sdb.create_node(session, art)

    # Register in security layer
    from uaf.security.acl import ACL, ACLEntry
    from uaf.security.primitives import Role

    sdb._resolver.register_artifact(art_id)
    acl = ACL(
        artifact_id=art_id,
        entries=(ACLEntry(
            principal_id=session.principal.id, role=Role.OWNER,
            granted_at=utc_now(), granted_by=session.principal.id,
        ),),
    )
    sdb._resolver.set_acl(acl)

    return sdb, session, art_id


def _add_task(
    sdb: SecureGraphDB, session: object, parent_id: NodeId, title: str, **kwargs: object,
) -> NodeId:
    """Helper to add a task directly."""
    task = Task(meta=make_node_metadata(NodeType.TASK), title=title, **kwargs)  # type: ignore[arg-type]
    task_id = sdb.create_node(session, task)  # type: ignore[arg-type]
    edge = Edge(
        id=EdgeId.generate(), source=parent_id, target=task_id,
        edge_type=EdgeType.CONTAINS, created_at=utc_now(),
    )
    sdb.create_edge(session, edge)  # type: ignore[arg-type]
    sdb._resolver.register_parent(task_id, parent_id)
    return task_id


# ---------------------------------------------------------------------------
# TestFlowLensProtocol
# ---------------------------------------------------------------------------


class TestFlowLensProtocol:
    def test_implements_lens_protocol(self) -> None:
        assert isinstance(FlowLens(), Lens)

    def test_lens_type(self) -> None:
        assert FlowLens().lens_type == "flow"

    def test_supported_node_types(self) -> None:
        supported = FlowLens().supported_node_types
        assert NodeType.ARTIFACT in supported
        assert NodeType.TASK in supported


# ---------------------------------------------------------------------------
# TestGanttView
# ---------------------------------------------------------------------------


class TestGanttView:
    def test_empty_project_placeholder(self) -> None:
        sdb, session, art_id = _setup()
        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="gantt")  # type: ignore[arg-type]
        assert "No tasks yet" in view.content

    def test_single_task_with_dates(self) -> None:
        sdb, session, art_id = _setup()
        now = datetime.now(tz=UTC)
        _add_task(sdb, session, art_id, "Task 1", start_date=now, end_date=now + timedelta(days=5))

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="gantt")  # type: ignore[arg-type]
        assert "Task 1" in view.content
        assert "gantt-bar" in view.content

    def test_milestone_for_due_date_only(self) -> None:
        sdb, session, art_id = _setup()
        due = datetime.now(tz=UTC) + timedelta(days=7)
        _add_task(sdb, session, art_id, "Milestone", due_date=due)

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="gantt")  # type: ignore[arg-type]
        assert "gantt-milestone" in view.content

    def test_unscheduled_task_no_bar(self) -> None:
        sdb, session, art_id = _setup()
        _add_task(sdb, session, art_id, "Unscheduled")

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="gantt")  # type: ignore[arg-type]
        assert "Unscheduled" in view.content
        assert "gantt-bar" not in view.content
        assert "gantt-milestone" not in view.content

    def test_multiple_tasks_aligned(self) -> None:
        sdb, session, art_id = _setup()
        now = datetime.now(tz=UTC)
        _add_task(sdb, session, art_id, "Task A", start_date=now, end_date=now + timedelta(days=3))
        _add_task(
            sdb, session, art_id, "Task B",
            start_date=now + timedelta(days=2), end_date=now + timedelta(days=7),
        )

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="gantt")  # type: ignore[arg-type]
        assert "Task A" in view.content
        assert "Task B" in view.content
        assert view.node_count == 3  # artifact + 2 tasks

    def test_task_names_in_table(self) -> None:
        sdb, session, art_id = _setup()
        _add_task(sdb, session, art_id, "My Task")

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="gantt")  # type: ignore[arg-type]
        assert "task-name" in view.content
        assert "My Task" in view.content

    def test_drag_handle_and_row_data_node_id(self) -> None:
        """Drag handles need data-node-id on the <tr> so the whole row is a drop zone."""
        sdb, session, art_id = _setup()
        _add_task(sdb, session, art_id, "Draggable")

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="gantt")  # type: ignore[arg-type]
        assert "drag-handle" in view.content
        # data-node-id must be on the <tr> (not just <td>) for drop targeting
        assert "<tr data-node-id=" in view.content

    def test_gantt_shows_dependency_info(self) -> None:
        """Gantt rows should show inline dependency info."""
        sdb, session, art_id = _setup()
        t1_id = _add_task(sdb, session, art_id, "Prerequisite")
        t2_id = _add_task(sdb, session, art_id, "Follower")

        dep = Edge(
            id=EdgeId.generate(), source=t2_id, target=t1_id,
            edge_type=EdgeType.DEPENDS_ON, created_at=utc_now(),
        )
        sdb.create_edge(session, dep)  # type: ignore[arg-type]

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="gantt")  # type: ignore[arg-type]
        assert "task-dep-info" in view.content
        assert "depends on:" in view.content
        assert "Prerequisite" in view.content


# ---------------------------------------------------------------------------
# TestDependencyView
# ---------------------------------------------------------------------------


class TestDependencyView:
    def test_tasks_as_rows(self) -> None:
        sdb, session, art_id = _setup()
        _add_task(sdb, session, art_id, "Task 1")
        _add_task(sdb, session, art_id, "Task 2")

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="deps")  # type: ignore[arg-type]
        assert "Task 1" in view.content
        assert "Task 2" in view.content
        assert "flow-deps" in view.content

    def test_dependency_arrows(self) -> None:
        sdb, session, art_id = _setup()
        t1_id = _add_task(sdb, session, art_id, "Predecessor")
        t2_id = _add_task(sdb, session, art_id, "Dependent")

        # Add dependency: t2 depends on t1
        dep_edge = Edge(
            id=EdgeId.generate(), source=t2_id, target=t1_id,
            edge_type=EdgeType.DEPENDS_ON, created_at=utc_now(),
        )
        sdb.create_edge(session, dep_edge)  # type: ignore[arg-type]

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="deps")  # type: ignore[arg-type]
        assert "dep-arrows" in view.content
        assert "&#8594;" in view.content  # right arrow

    def test_no_arrows_for_independent_tasks(self) -> None:
        sdb, session, art_id = _setup()
        _add_task(sdb, session, art_id, "Independent A")
        _add_task(sdb, session, art_id, "Independent B")

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="deps")  # type: ignore[arg-type]
        # All dependency cells should show "none"
        assert view.content.count("&#8594;") == 0

    def test_drag_handle_on_dep_rows(self) -> None:
        """Deps view rows should have drag handles and data-node-id on <tr>."""
        sdb, session, art_id = _setup()
        _add_task(sdb, session, art_id, "Task X")

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="deps")  # type: ignore[arg-type]
        assert "drag-handle" in view.content
        assert "<tr data-node-id=" in view.content

    def test_dep_chip_with_remove_button(self) -> None:
        """Dependencies should render as chips with remove buttons."""
        sdb, session, art_id = _setup()
        t1_id = _add_task(sdb, session, art_id, "Alpha")
        t2_id = _add_task(sdb, session, art_id, "Beta")

        dep = Edge(
            id=EdgeId.generate(), source=t2_id, target=t1_id,
            edge_type=EdgeType.DEPENDS_ON, created_at=utc_now(),
        )
        sdb.create_edge(session, dep)  # type: ignore[arg-type]

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="deps")  # type: ignore[arg-type]
        assert "dep-chip" in view.content
        assert "Alpha" in view.content
        assert "dep-remove" in view.content
        assert "remove-dependency" in view.content


# ---------------------------------------------------------------------------
# TestDAGView
# ---------------------------------------------------------------------------


class TestDAGView:
    def test_tasks_as_dag_nodes(self) -> None:
        sdb, session, art_id = _setup()
        _add_task(sdb, session, art_id, "Node 1")
        _add_task(sdb, session, art_id, "Node 2")

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="dag")  # type: ignore[arg-type]
        assert "dag-node" in view.content
        assert "Node 1" in view.content
        assert "Node 2" in view.content

    def test_topological_layout(self) -> None:
        sdb, session, art_id = _setup()
        t1_id = _add_task(sdb, session, art_id, "First")
        t2_id = _add_task(sdb, session, art_id, "Second")

        # Second depends on First
        dep = Edge(
            id=EdgeId.generate(), source=t2_id, target=t1_id,
            edge_type=EdgeType.DEPENDS_ON, created_at=utc_now(),
        )
        sdb.create_edge(session, dep)  # type: ignore[arg-type]

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="dag")  # type: ignore[arg-type]
        # First should appear before Second in the output
        first_pos = view.content.index("First")
        second_pos = view.content.index("Second")
        assert first_pos < second_pos

    def test_nodes_have_row_data(self) -> None:
        sdb, session, art_id = _setup()
        _add_task(sdb, session, art_id, "Task A")

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="dag")  # type: ignore[arg-type]
        assert 'data-row="0"' in view.content


# ---------------------------------------------------------------------------
# TestKanbanView
# ---------------------------------------------------------------------------


class TestKanbanView:
    def test_tasks_grouped_by_status(self) -> None:
        sdb, session, art_id = _setup()
        _add_task(sdb, session, art_id, "Todo Task", status="todo")
        _add_task(sdb, session, art_id, "WIP Task", status="in_progress")
        _add_task(sdb, session, art_id, "Done Task", status="done")

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="kanban")  # type: ignore[arg-type]
        assert "Todo Task" in view.content
        assert "WIP Task" in view.content
        assert "Done Task" in view.content
        assert "flow-kanban" in view.content

    def test_completed_in_done_column(self) -> None:
        sdb, session, art_id = _setup()
        _add_task(sdb, session, art_id, "Completed", status="done", completed=True)

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="kanban")  # type: ignore[arg-type]
        # The done column should contain the task
        assert 'data-status="done"' in view.content
        assert "Completed" in view.content

    def test_task_cards_show_title(self) -> None:
        sdb, session, art_id = _setup()
        _add_task(sdb, session, art_id, "Card Task")

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="kanban")  # type: ignore[arg-type]
        assert "kanban-card" in view.content
        assert "Card Task" in view.content

    def test_empty_column_placeholder(self) -> None:
        sdb, session, art_id = _setup()
        # Only add a todo task — in_progress and done columns should be empty
        _add_task(sdb, session, art_id, "Only Todo")

        lens = FlowLens()
        view = lens.render(sdb, session, art_id, mode="kanban")  # type: ignore[arg-type]
        assert "No tasks" in view.content


# ---------------------------------------------------------------------------
# TestFlowLensActions
# ---------------------------------------------------------------------------


class TestFlowLensActions:
    def test_create_task(self) -> None:
        sdb, session, art_id = _setup()
        lens = FlowLens()
        action = CreateTask(parent_id=art_id, title="New Task", position=0)
        lens.apply_action(sdb, session, art_id, action)  # type: ignore[arg-type]

        children = sdb.get_children(session, art_id)  # type: ignore[arg-type]
        assert len(children) == 1
        assert isinstance(children[0], Task)
        assert children[0].title == "New Task"

    def test_update_task_title(self) -> None:
        sdb, session, art_id = _setup()
        task_id = _add_task(sdb, session, art_id, "Original")

        lens = FlowLens()
        action = UpdateTask(task_id=task_id, title="Updated")
        lens.apply_action(sdb, session, art_id, action)  # type: ignore[arg-type]

        task = sdb.get_node(session, task_id)  # type: ignore[arg-type]
        assert task.title == "Updated"

    def test_toggle_task(self) -> None:
        sdb, session, art_id = _setup()
        task_id = _add_task(sdb, session, art_id, "Toggle Me")

        lens = FlowLens()

        # todo → in_progress
        lens.apply_action(sdb, session, art_id, ToggleTask(task_id=task_id))  # type: ignore[arg-type]
        task = sdb.get_node(session, task_id)  # type: ignore[arg-type]
        assert task.status == "in_progress"

        # in_progress → done
        lens.apply_action(sdb, session, art_id, ToggleTask(task_id=task_id))  # type: ignore[arg-type]
        task = sdb.get_node(session, task_id)  # type: ignore[arg-type]
        assert task.status == "done"
        assert task.completed is True

        # done → todo
        lens.apply_action(sdb, session, art_id, ToggleTask(task_id=task_id))  # type: ignore[arg-type]
        task = sdb.get_node(session, task_id)  # type: ignore[arg-type]
        assert task.status == "todo"
        assert task.completed is False

    def test_set_dependency(self) -> None:
        sdb, session, art_id = _setup()
        t1_id = _add_task(sdb, session, art_id, "Task 1")
        t2_id = _add_task(sdb, session, art_id, "Task 2")

        lens = FlowLens()
        action = SetDependency(source_task_id=t2_id, target_task_id=t1_id)
        lens.apply_action(sdb, session, art_id, action)  # type: ignore[arg-type]

        edges = sdb._db.get_edges_from(t2_id)
        dep_edges = [e for e in edges if e.edge_type == EdgeType.DEPENDS_ON]
        assert len(dep_edges) == 1
        assert dep_edges[0].target == t1_id

    def test_remove_dependency(self) -> None:
        sdb, session, art_id = _setup()
        t1_id = _add_task(sdb, session, art_id, "Task 1")
        t2_id = _add_task(sdb, session, art_id, "Task 2")

        # Add then remove
        dep = Edge(
            id=EdgeId.generate(), source=t2_id, target=t1_id,
            edge_type=EdgeType.DEPENDS_ON, created_at=utc_now(),
        )
        sdb.create_edge(session, dep)  # type: ignore[arg-type]

        lens = FlowLens()
        action = RemoveDependency(source_task_id=t2_id, target_task_id=t1_id)
        lens.apply_action(sdb, session, art_id, action)  # type: ignore[arg-type]

        edges = sdb._db.get_edges_from(t2_id)
        dep_edges = [e for e in edges if e.edge_type == EdgeType.DEPENDS_ON]
        assert len(dep_edges) == 0

    def test_set_due_date(self) -> None:
        sdb, session, art_id = _setup()
        task_id = _add_task(sdb, session, art_id, "Deadline Task")

        lens = FlowLens()
        due = datetime(2026, 6, 15, tzinfo=UTC)
        action = SetDueDate(task_id=task_id, due_date=due)
        lens.apply_action(sdb, session, art_id, action)  # type: ignore[arg-type]

        task = sdb.get_node(session, task_id)  # type: ignore[arg-type]
        assert task.due_date == due

    def test_set_date_range(self) -> None:
        sdb, session, art_id = _setup()
        task_id = _add_task(sdb, session, art_id, "Ranged Task")

        lens = FlowLens()
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 15, tzinfo=UTC)
        action = SetDateRange(task_id=task_id, start_date=start, end_date=end)
        lens.apply_action(sdb, session, art_id, action)  # type: ignore[arg-type]

        task = sdb.get_node(session, task_id)  # type: ignore[arg-type]
        assert task.start_date == start
        assert task.end_date == end

    def test_delete_task(self) -> None:
        sdb, session, art_id = _setup()
        task_id = _add_task(sdb, session, art_id, "To Delete")

        lens = FlowLens()
        action = DeleteNode(node_id=task_id)
        lens.apply_action(sdb, session, art_id, action)  # type: ignore[arg-type]

        task = sdb._db.get_node(task_id)
        assert task is None

    def test_reorder_tasks(self) -> None:
        sdb, session, art_id = _setup()
        t1_id = _add_task(sdb, session, art_id, "First")
        t2_id = _add_task(sdb, session, art_id, "Second")

        lens = FlowLens()
        action = ReorderNodes(parent_id=art_id, new_order=(t2_id, t1_id))
        lens.apply_action(sdb, session, art_id, action)  # type: ignore[arg-type]

        children = sdb.get_children(session, art_id)  # type: ignore[arg-type]
        assert children[0].title == "Second"
        assert children[1].title == "First"

    def test_create_task_group(self) -> None:
        sdb, session, art_id = _setup()
        lens = FlowLens()
        action = CreateTaskGroup(parent_id=art_id, title="Phase 1", position=0)
        lens.apply_action(sdb, session, art_id, action)  # type: ignore[arg-type]

        children = sdb.get_children(session, art_id)  # type: ignore[arg-type]
        assert len(children) == 1
        assert isinstance(children[0], Task)
        assert children[0].title == "Phase 1"


# ---------------------------------------------------------------------------
# TestCircularDependency
# ---------------------------------------------------------------------------


class TestCircularDependency:
    def test_direct_cycle_rejected(self) -> None:
        sdb, session, art_id = _setup()
        t1_id = _add_task(sdb, session, art_id, "A")
        t2_id = _add_task(sdb, session, art_id, "B")

        lens = FlowLens()
        # A depends on B
        lens.apply_action(
            sdb, session, art_id,  # type: ignore[arg-type]
            SetDependency(source_task_id=t1_id, target_task_id=t2_id),
        )
        # B depends on A → cycle!
        with pytest.raises(ValueError, match="cycle"):
            lens.apply_action(
                sdb, session, art_id,  # type: ignore[arg-type]
                SetDependency(source_task_id=t2_id, target_task_id=t1_id),
            )

    def test_indirect_cycle_rejected(self) -> None:
        sdb, session, art_id = _setup()
        t1_id = _add_task(sdb, session, art_id, "A")
        t2_id = _add_task(sdb, session, art_id, "B")
        t3_id = _add_task(sdb, session, art_id, "C")

        lens = FlowLens()
        # A → B → C (chain of deps)
        lens.apply_action(
            sdb, session, art_id,  # type: ignore[arg-type]
            SetDependency(source_task_id=t1_id, target_task_id=t2_id),
        )
        lens.apply_action(
            sdb, session, art_id,  # type: ignore[arg-type]
            SetDependency(source_task_id=t2_id, target_task_id=t3_id),
        )
        # C → A → cycle!
        with pytest.raises(ValueError, match="cycle"):
            lens.apply_action(
                sdb, session, art_id,  # type: ignore[arg-type]
                SetDependency(source_task_id=t3_id, target_task_id=t1_id),
            )


# ---------------------------------------------------------------------------
# TestFlowLensActionTypes (frozen dataclass tests)
# ---------------------------------------------------------------------------


class TestFlowLensActionTypes:
    def test_create_task_frozen(self) -> None:
        nid = NodeId.generate()
        a = CreateTask(parent_id=nid, title="T", position=0)
        assert a.title == "T"
        with pytest.raises(AttributeError):
            a.title = "X"  # type: ignore[misc]

    def test_create_task_group_frozen(self) -> None:
        nid = NodeId.generate()
        a = CreateTaskGroup(parent_id=nid, title="G", position=0)
        assert a.title == "G"

    def test_update_task_frozen(self) -> None:
        nid = NodeId.generate()
        a = UpdateTask(task_id=nid, title="T")
        assert a.title == "T"

    def test_toggle_task_frozen(self) -> None:
        nid = NodeId.generate()
        a = ToggleTask(task_id=nid)
        assert a.task_id == nid

    def test_set_dependency_frozen(self) -> None:
        a, b = NodeId.generate(), NodeId.generate()
        d = SetDependency(source_task_id=a, target_task_id=b)
        assert d.source_task_id == a
        assert d.target_task_id == b

    def test_remove_dependency_frozen(self) -> None:
        a, b = NodeId.generate(), NodeId.generate()
        d = RemoveDependency(source_task_id=a, target_task_id=b)
        assert d.source_task_id == a

    def test_set_due_date_frozen(self) -> None:
        nid = NodeId.generate()
        due = datetime(2026, 6, 1, tzinfo=UTC)
        a = SetDueDate(task_id=nid, due_date=due)
        assert a.due_date == due

    def test_set_date_range_frozen(self) -> None:
        nid = NodeId.generate()
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 15, tzinfo=UTC)
        a = SetDateRange(task_id=nid, start_date=start, end_date=end)
        assert a.start_date == start
        assert a.end_date == end
