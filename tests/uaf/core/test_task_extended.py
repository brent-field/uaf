"""Tests for extended Task fields — start_date, end_date, status."""

from __future__ import annotations

from datetime import UTC, datetime

from uaf.core.nodes import NodeType, Task, make_node_metadata
from uaf.core.serialization import node_from_dict, node_to_dict
from uaf.db.graph_db import GraphDB


class TestTaskExtendedFields:
    def test_task_has_start_end_dates(self) -> None:
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 15, tzinfo=UTC)
        task = Task(
            meta=make_node_metadata(NodeType.TASK),
            title="Task",
            start_date=start,
            end_date=end,
        )
        assert task.start_date == start
        assert task.end_date == end

    def test_task_has_status_field(self) -> None:
        task = Task(
            meta=make_node_metadata(NodeType.TASK),
            title="Task",
            status="in_progress",
        )
        assert task.status == "in_progress"

    def test_task_defaults(self) -> None:
        task = Task(meta=make_node_metadata(NodeType.TASK), title="Default")
        assert task.start_date is None
        assert task.end_date is None
        assert task.status == "todo"
        assert task.completed is False
        assert task.due_date is None

    def test_task_serialization_roundtrip(self) -> None:
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 15, tzinfo=UTC)
        due = datetime(2026, 3, 10, tzinfo=UTC)
        task = Task(
            meta=make_node_metadata(NodeType.TASK),
            title="Roundtrip",
            completed=True,
            due_date=due,
            start_date=start,
            end_date=end,
            status="done",
        )
        d = node_to_dict(task)
        restored = node_from_dict(d)

        assert isinstance(restored, Task)
        assert restored.title == "Roundtrip"
        assert restored.completed is True
        assert restored.due_date == due
        assert restored.start_date == start
        assert restored.end_date == end
        assert restored.status == "done"

    def test_task_status_indexed(self) -> None:
        db = GraphDB()
        task = Task(
            meta=make_node_metadata(NodeType.TASK),
            title="Indexed",
            status="in_progress",
        )
        db.create_node(task)

        results = db.find_by_attribute("status", "in_progress")
        assert len(results) == 1
        assert results[0].title == "Indexed"
