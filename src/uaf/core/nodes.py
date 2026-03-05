"""Node types — all concrete node types, NodeMetadata, LayoutHint, and the NodeData union."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import TYPE_CHECKING, Any

from uaf.core.node_id import NodeId, utc_now

if TYPE_CHECKING:
    from datetime import datetime


@unique
class NodeType(Enum):
    """Enumeration of all node types in the graph."""

    ARTIFACT = "artifact"
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TEXT_BLOCK = "text_block"
    CELL = "cell"
    FORMULA_CELL = "formula_cell"
    SHEET = "sheet"
    CODE_BLOCK = "code_block"
    MATH_BLOCK = "math_block"
    TASK = "task"
    SLIDE = "slide"
    SHAPE = "shape"
    IMAGE = "image"
    ARTIFACT_ACL = "artifact_acl"
    RAW = "raw"


@dataclass(frozen=True, slots=True)
class SpanInfo:
    """Per-span font/position metadata for preserving inline typographic variation."""

    text: str
    font_size: float | None = None
    font_family: str | None = None
    font_weight: str | None = None
    font_style: str | None = None
    y_offset: float | None = None
    x_offset: float | None = None


@dataclass(frozen=True, slots=True)
class FontAnnotation:
    """Marks a character range in display_text that uses a non-default font.

    Used for inline math in paragraphs — the display_text preserves correct
    line breaks, and annotations mark which character ranges need math font
    styling without restructuring the text into spans.
    """

    start: int
    end: int
    font_family: str
    font_style: str | None = None
    font_size: float | None = None
    font_weight: str | None = None
    vertical_align: float | None = None  # pt offset (>0=sub, <0=super)


@dataclass(frozen=True, slots=True)
class LayoutHint:
    """Optional layout metadata for preserving visual fidelity on import/export."""

    page: int | None = None
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    font_family: str | None = None
    font_size: float | None = None
    font_weight: str | None = None
    font_style: str | None = None
    color: str | None = None
    reading_order: int | None = None
    rotation: float | None = None
    first_line_weight: str | None = None
    header_footer: bool = False
    display_text: str | None = None
    line_height: float | None = None
    spans: tuple[SpanInfo, ...] | None = None
    font_annotations: tuple[FontAnnotation, ...] | None = None


@dataclass(frozen=True, slots=True)
class NodeMetadata:
    """Shared metadata carried by every node."""

    id: NodeId
    node_type: NodeType
    created_at: datetime
    updated_at: datetime
    owner: str | None = None
    layout: LayoutHint | None = None


def make_node_metadata(
    node_type: NodeType,
    *,
    owner: str | None = None,
    layout: LayoutHint | None = None,
    node_id: NodeId | None = None,
) -> NodeMetadata:
    """Create NodeMetadata with sensible defaults (generated ID, current timestamps)."""
    now = utc_now()
    return NodeMetadata(
        id=node_id if node_id is not None else NodeId.generate(),
        node_type=node_type,
        created_at=now,
        updated_at=now,
        owner=owner,
        layout=layout,
    )


# ---------------------------------------------------------------------------
# Concrete node types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Artifact:
    """Top-level container node (document, spreadsheet, chess game, CAD model)."""

    meta: NodeMetadata
    title: str


@dataclass(frozen=True, slots=True)
class Paragraph:
    """A paragraph of text."""

    meta: NodeMetadata
    text: str
    style: str = "body"


@dataclass(frozen=True, slots=True)
class Heading:
    """A heading with a level (1-6)."""

    meta: NodeMetadata
    text: str
    level: int


@dataclass(frozen=True, slots=True)
class TextBlock:
    """A block of text with a format hint (e.g. 'plain', 'html', 'wiki')."""

    meta: NodeMetadata
    text: str
    format: str = "plain"


CellValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class Cell:
    """A spreadsheet cell with a typed value."""

    meta: NodeMetadata
    value: CellValue
    row: int
    col: int


@dataclass(frozen=True, slots=True)
class FormulaCell:
    """A spreadsheet cell containing a formula."""

    meta: NodeMetadata
    formula: str
    cached_value: CellValue
    row: int
    col: int


@dataclass(frozen=True, slots=True)
class Sheet:
    """A spreadsheet sheet container."""

    meta: NodeMetadata
    title: str
    rows: int
    cols: int


@dataclass(frozen=True, slots=True)
class CodeBlock:
    """A block of source code."""

    meta: NodeMetadata
    source: str
    language: str


@dataclass(frozen=True, slots=True)
class MathBlock:
    """A block of mathematical content (equation, formula)."""

    meta: NodeMetadata
    source: str
    equation_number: str | None = None
    display: str = "block"


@dataclass(frozen=True, slots=True)
class Task:
    """A task / to-do item."""

    meta: NodeMetadata
    title: str
    completed: bool = False
    due_date: datetime | None = None


@dataclass(frozen=True, slots=True)
class Slide:
    """A presentation slide."""

    meta: NodeMetadata
    title: str
    order: int


@dataclass(frozen=True, slots=True)
class Shape:
    """A visual shape element."""

    meta: NodeMetadata
    shape_type: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class Image:
    """An image node referencing a blob or external URI."""

    meta: NodeMetadata
    uri: str
    alt_text: str = ""
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class ArtifactACL:
    """Per-artifact access control list metadata (used by security layer)."""

    meta: NodeMetadata
    default_role: str | None = None
    public_read: bool = False


@dataclass(frozen=True, slots=True)
class RawNode:
    """Fallback node for unknown types during schema evolution."""

    meta: NodeMetadata
    raw: dict[str, Any]
    original_type: str


# ---------------------------------------------------------------------------
# Union type
# ---------------------------------------------------------------------------

type NodeData = (
    Artifact
    | Paragraph
    | Heading
    | TextBlock
    | Cell
    | FormulaCell
    | Sheet
    | CodeBlock
    | MathBlock
    | Task
    | Slide
    | Shape
    | Image
    | ArtifactACL
    | RawNode
)
