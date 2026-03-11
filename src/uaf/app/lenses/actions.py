"""LensAction types — user intent as frozen dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from uaf.core.node_id import NodeId
    from uaf.core.nodes import CellValue


# ---------------------------------------------------------------------------
# DocLens actions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InsertText:
    """Insert a text node (paragraph, heading, etc.) as a child."""

    parent_id: NodeId
    text: str
    position: int
    style: str = "paragraph"  # "paragraph", "heading", "code_block"


@dataclass(frozen=True, slots=True)
class DeleteText:
    """Delete a text node."""

    node_id: NodeId


@dataclass(frozen=True, slots=True)
class FormatText:
    """Change the format/style of a text node."""

    node_id: NodeId
    style: str  # "heading", "paragraph", "code_block"
    level: int = 1  # Only used for headings


# ---------------------------------------------------------------------------
# GridLens actions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SetCellValue:
    """Set the value of a cell."""

    cell_id: NodeId
    value: CellValue


@dataclass(frozen=True, slots=True)
class SetCellFormula:
    """Set a formula on a cell."""

    cell_id: NodeId
    formula: str
    cached_value: CellValue


@dataclass(frozen=True, slots=True)
class InsertRow:
    """Insert a row of empty cells at a position."""

    sheet_id: NodeId
    position: int


@dataclass(frozen=True, slots=True)
class InsertColumn:
    """Insert a column of empty cells at a position."""

    sheet_id: NodeId
    position: int


@dataclass(frozen=True, slots=True)
class DeleteRow:
    """Delete a row and its cells."""

    sheet_id: NodeId
    position: int


@dataclass(frozen=True, slots=True)
class DeleteColumn:
    """Delete a column and its cells."""

    sheet_id: NodeId
    position: int


# ---------------------------------------------------------------------------
# Shared actions (all lenses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReorderNodes:
    """Reorder children of a parent node."""

    parent_id: NodeId
    new_order: tuple[NodeId, ...]


@dataclass(frozen=True, slots=True)
class MoveNode:
    """Move a node to a new parent."""

    node_id: NodeId
    new_parent_id: NodeId


@dataclass(frozen=True, slots=True)
class DeleteNode:
    """Delete a node."""

    node_id: NodeId


@dataclass(frozen=True, slots=True)
class RenameArtifact:
    """Rename an artifact."""

    artifact_id: NodeId
    title: str


# ---------------------------------------------------------------------------
# FlowLens actions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateTask:
    """Create a new task under a parent."""

    parent_id: NodeId
    title: str
    position: int
    due_date: datetime | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None


@dataclass(frozen=True, slots=True)
class CreateTaskGroup:
    """Create a parent task that contains sub-tasks."""

    parent_id: NodeId
    title: str
    position: int


@dataclass(frozen=True, slots=True)
class UpdateTask:
    """Update a task's title."""

    task_id: NodeId
    title: str


@dataclass(frozen=True, slots=True)
class ToggleTask:
    """Cycle task status: todo → in_progress → done → todo."""

    task_id: NodeId


@dataclass(frozen=True, slots=True)
class SetDependency:
    """Source task depends on target task."""

    source_task_id: NodeId
    target_task_id: NodeId


@dataclass(frozen=True, slots=True)
class RemoveDependency:
    """Remove a dependency between two tasks."""

    source_task_id: NodeId
    target_task_id: NodeId


@dataclass(frozen=True, slots=True)
class SetDueDate:
    """Set or clear a task's due date."""

    task_id: NodeId
    due_date: datetime | None


@dataclass(frozen=True, slots=True)
class SetDateRange:
    """Set a task's start and end dates for Gantt bars."""

    task_id: NodeId
    start_date: datetime
    end_date: datetime


# ---------------------------------------------------------------------------
# Union type
# ---------------------------------------------------------------------------

type LensAction = (
    InsertText
    | DeleteText
    | FormatText
    | SetCellValue
    | SetCellFormula
    | InsertRow
    | InsertColumn
    | DeleteRow
    | DeleteColumn
    | ReorderNodes
    | MoveNode
    | DeleteNode
    | RenameArtifact
    | CreateTask
    | CreateTaskGroup
    | UpdateTask
    | ToggleTask
    | SetDependency
    | RemoveDependency
    | SetDueDate
    | SetDateRange
)
