"""LensAction types — user intent as frozen dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
# Union type
# ---------------------------------------------------------------------------

type LensAction = (
    InsertText
    | DeleteText
    | FormatText
    | SetCellValue
    | InsertRow
    | InsertColumn
    | DeleteRow
    | DeleteColumn
    | ReorderNodes
    | MoveNode
    | DeleteNode
    | RenameArtifact
)
