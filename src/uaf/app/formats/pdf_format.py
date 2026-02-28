"""PDF import using PyMuPDF (fitz) — with layout metadata extraction."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import TYPE_CHECKING, Any

import fitz

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    Heading,
    LayoutHint,
    NodeType,
    Paragraph,
    Shape,
    make_node_metadata,
)

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.db.graph_db import GraphDB

# Threshold in points for header/footer detection (≈1 inch from edge).
_HEADER_FOOTER_MARGIN = 72.0

# Minimum pages a text must repeat on to be considered a header/footer.
_MIN_HF_PAGES = 2

# Pattern to normalise page numbers for header/footer matching.
_PAGE_NUM_RE = re.compile(r"\b\d+\b")

# Detects a line-end hyphenation: a letter followed by a hyphen at end of line
# where the next line starts with a lowercase letter.
_HYPHEN_RE = re.compile(r"([a-zA-Z])-\n([a-z])")


class PdfHandler:
    """Import PDF files via the UAF graph (export as plain text)."""

    def import_file(self, path: Path, db: GraphDB) -> NodeId:
        """Extract text blocks from a PDF and create nodes with layout metadata."""
        doc = fitz.open(str(path))

        # Store page dimensions from the first page on the Artifact.
        first_rect = doc[0].rect if len(doc) > 0 else fitz.Rect(0, 0, 612, 792)
        art_layout = LayoutHint(
            width=float(first_rect.width),
            height=float(first_rect.height),
        )
        art = Artifact(
            meta=make_node_metadata(NodeType.ARTIFACT, layout=art_layout),
            title=path.stem,
        )
        art_id = db.create_node(art)

        # First pass: import all text blocks and collect info for header/footer detection.
        block_index = 0
        # Tracks (node_id, page, y, normalised_text) for hf detection.
        block_records: list[tuple[NodeId, int, float, str, float]] = []

        for page_num, page in enumerate(doc):
            page_height = float(page.rect.height)
            raw = page.get_text("dict")
            page_dict: dict[str, Any] = raw if isinstance(raw, dict) else {}

            for block in page_dict.get("blocks", []):
                if block.get("type", 0) != 0:  # skip image blocks
                    continue

                text = _extract_block_text(block)
                if not text:
                    continue

                # Raw text preserves original PDF line breaks and hyphens
                # for layout rendering.  Only store when it differs from
                # the semantic (dehyphenated) text to save space.
                raw_text = _extract_raw_block_text(block)
                display_text: str | None = raw_text if raw_text != text else None

                font = _extract_dominant_font(block)
                first_line_font = _extract_first_line_font(block)
                rotation = _extract_rotation(block)
                bbox = block.get("bbox", (0.0, 0.0, 0.0, 0.0))
                x0, y0, x1, y1 = (
                    float(bbox[0]),
                    float(bbox[1]),
                    float(bbox[2]),
                    float(bbox[3]),
                )

                # For ≈90° rotated blocks the bbox width is the line
                # thickness and height is the text run length.  CSS needs
                # the run length as width (text is laid out then rotated).
                #
                # The CSS anchor point (top/left + transform-origin: top left)
                # also needs adjustment so the rotated text fills the correct
                # region of the page:
                #   -90° (bottom→top): rotate() swings the text *upward* from
                #       the anchor, so place it at the bbox bottom (y1).
                #   +90° (top→bottom): rotate() swings the text *downward*,
                #       so the bbox top (y0) is already correct.
                layout_y = y0
                if rotation is not None and abs(abs(rotation) - 90.0) < 5.0:
                    layout_w = y1 - y0  # text run length
                    if rotation < 0:
                        layout_y = y1  # anchor at bbox bottom
                else:
                    layout_w = x1 - x0

                # first_line_weight is stored only when it differs from
                # the block-level dominant weight.
                fl_weight = first_line_font.get("weight")
                block_weight = font.get("weight")
                first_lw: str | None = None
                if fl_weight and fl_weight != (block_weight or "normal"):
                    first_lw = fl_weight

                layout = LayoutHint(
                    page=page_num,
                    x=x0,
                    y=layout_y,
                    width=layout_w,
                    height=y1 - y0,
                    font_family=font.get("family"),
                    font_size=font.get("size"),
                    font_weight=font.get("weight"),
                    font_style=font.get("style"),
                    color=font.get("color"),
                    reading_order=block_index,
                    rotation=rotation,
                    first_line_weight=first_lw,
                    display_text=display_text,
                )

                # Detect heading heuristic: large font or bold
                is_heading = (
                    font.get("size") is not None
                    and font["size"] >= 16.0
                    and len(text) < 200
                )

                if is_heading:
                    level = _heading_level_from_size(font.get("size", 12.0))
                    node: Heading | Paragraph = Heading(
                        meta=make_node_metadata(NodeType.HEADING, layout=layout),
                        text=text,
                        level=level,
                    )
                else:
                    node = Paragraph(
                        meta=make_node_metadata(NodeType.PARAGRAPH, layout=layout),
                        text=text,
                    )

                nid = db.create_node(node)
                db.create_edge(_contains(art_id, nid))

                normalised = _PAGE_NUM_RE.sub("N", text.strip())
                block_records.append((nid, page_num, y0, normalised, page_height))
                block_index += 1

        # Extract vector shapes (lines, rectangles) from each page.
        for page_num, page in enumerate(doc):
            for shape_node in _extract_shapes(page, page_num, block_index):
                nid = db.create_node(shape_node)
                db.create_edge(_contains(art_id, nid))
                block_index += 1

        doc.close()

        # Second pass: detect and tag headers/footers.
        _tag_headers_footers(db, block_records)

        return art_id

    def export_file(self, db: GraphDB, root_id: NodeId, path: Path) -> None:
        """Export as plain text (PDF generation not supported)."""
        children = db.get_children(root_id)
        parts: list[str] = []

        for child in children:
            if isinstance(child, (Paragraph, Heading)):
                parts.append(child.text)

        text = "\n\n".join(parts)
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contains(source: NodeId, target: NodeId) -> Edge:
    return Edge(
        id=EdgeId.generate(),
        source=source,
        target=target,
        edge_type=EdgeType.CONTAINS,
        created_at=utc_now(),
    )


def _extract_block_text(block: dict[str, Any]) -> str:
    """Aggregate text from all lines/spans, storing semantic text.

    Spans within a line are concatenated directly (PyMuPDF already
    includes leading spaces where needed).  Lines are joined with
    newlines.  Multiple consecutive spaces — common with small-caps
    or split-span fonts — are collapsed to a single space.

    End-of-line hyphens that split a word across lines are removed and
    the fragments rejoined so the stored text is the semantic form
    (e.g. "capability") rather than the display form ("capa-" + "bility").
    """
    raw = _extract_raw_block_text(block)
    # Dehyphenate: "capa-\nbilities" → "capabilities".
    text = _HYPHEN_RE.sub(r"\1\2", raw)
    # Collapse runs of multiple spaces (but not newlines).
    text = re.sub(r"  +", " ", text)
    return text.strip()


def _extract_raw_block_text(block: dict[str, Any]) -> str:
    """Aggregate text from all lines/spans, preserving original line breaks.

    Unlike :func:`_extract_block_text`, this does **not** dehyphenate or
    collapse spaces.  The result is the display form of the text — exactly
    as it appears in the PDF — suitable for layout rendering where line
    breaks must match the original document.
    """
    line_texts: list[str] = []
    for line in block.get("lines", []):
        parts: list[str] = []
        for span in line.get("spans", []):
            parts.append(span.get("text", ""))
        line_texts.append("".join(parts))
    return "\n".join(line_texts).strip()


def _extract_dominant_font(block: dict[str, Any]) -> dict[str, Any]:
    """Pick the most common font properties across all spans in a block.

    All properties (family, size, weight, style, color) use character-weighted
    voting across every span.  The winning font family is mapped to a
    web-safe CSS font stack via :func:`_map_font_family`.
    """
    families: Counter[str] = Counter()
    sizes: Counter[float] = Counter()
    weights: Counter[str] = Counter()
    styles: Counter[str] = Counter()
    colors: Counter[str] = Counter()

    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text_len = len(span.get("text", ""))
            if text_len == 0:
                continue

            family = span.get("font", "")
            if family:
                families[family] += text_len

            size = span.get("size")
            if size is not None:
                sizes[round(float(size), 1)] += text_len

            flags = span.get("flags", 0)
            weight = "bold" if flags & (1 << 4) else "normal"
            weights[weight] += text_len

            style = "italic" if flags & (1 << 1) else "normal"
            styles[style] += text_len

            color_int = span.get("color", 0)
            hex_color = f"#{color_int:06x}"
            colors[hex_color] += text_len

    result: dict[str, Any] = {}
    if families:
        result["family"] = _map_font_family(families.most_common(1)[0][0])
    if sizes:
        result["size"] = sizes.most_common(1)[0][0]

    top_weight = weights.most_common(1)[0][0] if weights else "normal"
    if top_weight != "normal":
        result["weight"] = top_weight

    top_style = styles.most_common(1)[0][0] if styles else "normal"
    if top_style != "normal":
        result["style"] = top_style

    if colors:
        top_color = colors.most_common(1)[0][0]
        if top_color != "#000000":
            result["color"] = top_color

    return result


def _extract_first_line_font(block: dict[str, Any]) -> dict[str, Any]:
    """Extract the dominant weight/style from the first text line only."""
    lines = block.get("lines", [])
    if not lines:
        return {}

    weights: Counter[str] = Counter()
    styles: Counter[str] = Counter()

    for span in lines[0].get("spans", []):
        text_len = len(span.get("text", ""))
        if text_len == 0:
            continue
        flags = span.get("flags", 0)
        weights["bold" if flags & (1 << 4) else "normal"] += text_len
        styles["italic" if flags & (1 << 1) else "normal"] += text_len

    result: dict[str, Any] = {}
    if weights:
        w = weights.most_common(1)[0][0]
        if w != "normal":
            result["weight"] = w
    if styles:
        s = styles.most_common(1)[0][0]
        if s != "normal":
            result["style"] = s
    return result


# ---------------------------------------------------------------------------
# Font mapping — PDF font names → web-safe CSS font stacks
# ---------------------------------------------------------------------------

_FONT_MAP: list[tuple[str, str]] = [
    # Nimbus Roman (URW clone of Times New Roman, common in TeX PDFs)
    ("NimbusRomNo9L", '"Times New Roman", Times, serif'),
    ("NimbusRomNo9", '"Times New Roman", Times, serif'),
    # Nimbus Sans (URW clone of Helvetica)
    ("NimbusSanL", "Helvetica, Arial, sans-serif"),
    ("NimbusSan", "Helvetica, Arial, sans-serif"),
    # Standard PostScript core fonts
    ("Times", '"Times New Roman", Times, serif'),
    ("Helvetica", "Helvetica, Arial, sans-serif"),
    ("Courier", '"Courier New", Courier, monospace'),
    ("Arial", "Arial, Helvetica, sans-serif"),
    # Computer Modern (TeX) families
    ("SFTT", '"Courier New", Courier, monospace'),  # CM Typewriter
    ("CMTT", '"Courier New", Courier, monospace'),
    ("CMSS", "Helvetica, Arial, sans-serif"),  # CM Sans-Serif
    ("CMR", '"Times New Roman", Times, serif'),  # CM Roman
    ("CMSY", "Symbol, serif"),  # CM Symbols
    ("CMMI", '"Times New Roman", Times, serif'),  # CM Math Italic
    ("CMB", '"Times New Roman", Times, serif'),  # CM Bold
    # Liberation (metric-compatible with MS core fonts)
    ("LiberationSerif", '"Times New Roman", Times, serif'),
    ("LiberationSans", "Arial, Helvetica, sans-serif"),
    ("LiberationMono", '"Courier New", Courier, monospace'),
    # DejaVu
    ("DejaVuSerif", "Georgia, serif"),
    ("DejaVuSans", "Verdana, sans-serif"),
    ("DejaVuSansMono", '"Courier New", monospace'),
]


def _map_font_family(pdf_font: str) -> str:
    """Map a PDF font name to a web-safe CSS font-family value.

    Uses prefix matching against a table of common PDF fonts.
    Unrecognised fonts are returned as-is with a generic fallback.
    """
    for prefix, css_stack in _FONT_MAP:
        if pdf_font.startswith(prefix):
            return css_stack
    # Unknown — keep original and append a generic fallback.
    return f"{pdf_font}, serif"


def _heading_level_from_size(font_size: float) -> int:
    """Map font size in points to a heading level (1-6)."""
    if font_size >= 28:
        return 1
    if font_size >= 22:
        return 2
    if font_size >= 18:
        return 3
    if font_size >= 16:
        return 4
    if font_size >= 14:
        return 5
    return 6


def _extract_rotation(block: dict[str, Any]) -> float | None:
    """Extract text rotation angle from the first line's direction vector.

    PyMuPDF reports text direction as ``(cos θ, sin θ)``:

    - ``(1, 0)``  → horizontal (0°)
    - ``(0, -1)`` → 90° counter-clockwise (text reads bottom-to-top)
    - ``(-1, 0)`` → 180° (upside down)
    - ``(0, 1)``  → 90° clockwise (text reads top-to-bottom)

    Returns the angle in degrees, or *None* for horizontal text.
    """
    lines = block.get("lines", [])
    if not lines:
        return None

    dir_vec = lines[0].get("dir")
    if dir_vec is None or len(dir_vec) < 2:
        return None

    dx, dy = float(dir_vec[0]), float(dir_vec[1])

    # Horizontal text (default) — no rotation needed.
    if abs(dx - 1.0) < 0.01 and abs(dy) < 0.01:
        return None

    angle = math.degrees(math.atan2(dy, dx))
    # Round to avoid floating-point noise.
    angle = round(angle, 1)

    return angle if angle != 0.0 else None


def _tag_headers_footers(
    db: GraphDB,
    records: list[tuple[NodeId, int, float, str, float]],
) -> None:
    """Detect repeating text near page edges and tag as header/footer.

    records: list of (node_id, page_num, y_pos, normalised_text, page_height).
    """
    if not records:
        return

    # Group by normalised text that appears near top or bottom of pages.
    from collections import defaultdict

    candidates: dict[str, list[tuple[NodeId, int, float, float]]] = defaultdict(list)

    for nid, page_num, y, norm_text, page_height in records:
        is_near_top = y < _HEADER_FOOTER_MARGIN
        is_near_bottom = y > (page_height - _HEADER_FOOTER_MARGIN)
        if is_near_top or is_near_bottom:
            candidates[norm_text].append((nid, page_num, y, page_height))

    # For texts that repeat across >= _MIN_HF_PAGES distinct pages, tag them.
    for _norm_text, occurrences in candidates.items():
        distinct_pages = {pg for _, pg, _, _ in occurrences}
        if len(distinct_pages) >= _MIN_HF_PAGES:
            for nid, _pg, _y, _ph in occurrences:
                node = db.get_node(nid)
                if node is not None and hasattr(node, "meta") and node.meta.layout is not None:
                    from dataclasses import replace

                    new_layout = replace(node.meta.layout, header_footer=True)
                    new_meta = replace(node.meta, layout=new_layout)
                    new_node = replace(node, meta=new_meta)
                    db.update_node(new_node)


# ---------------------------------------------------------------------------
# Shape extraction — vector graphics from PDF drawing commands
# ---------------------------------------------------------------------------

# Minimum dimension threshold: ignore shapes smaller than this in both axes.
_MIN_SHAPE_DIM = 1.0

# Horizontal/vertical rule detection: height (or width) below this is a rule.
_RULE_THICKNESS_MAX = 5.0


def _extract_shapes(
    page: Any,
    page_num: int,
    block_index_start: int,
) -> list[Shape]:
    """Extract simple vector shapes (lines, rectangles, rules) from a PDF page.

    Uses ``page.get_drawings()`` to find drawing commands and converts them
    to ``Shape`` nodes.  Only simple shapes are extracted — complex paths
    (Bézier curves) are skipped.

    Classification:
    - **hrule**: thin rectangle or line where ``width >> height`` and ``height < 5pt``
    - **vrule**: thin rectangle or line where ``height >> width`` and ``width < 5pt``
    - **rect**: all other rectangles
    - **line**: all other lines
    """
    shapes: list[Shape] = []
    block_idx = block_index_start

    for drawing in page.get_drawings():
        items = drawing.get("items", [])
        if not items:
            continue

        # Only handle simple drawings: single-item lines or rectangles.
        item_types = {it[0] for it in items}
        if not item_types & {"l", "re"}:
            continue  # skip curves, quads, etc.

        rect = drawing.get("rect")
        if rect is None:
            continue

        x0, y0, x1, y1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
        w = x1 - x0
        h = y1 - y0

        # Skip invisible / degenerate shapes.
        if w < _MIN_SHAPE_DIM and h < _MIN_SHAPE_DIM:
            continue

        # Determine fill color for LayoutHint.
        fill = drawing.get("fill")
        stroke = drawing.get("color")
        color_tuple = fill if fill is not None else stroke
        hex_color: str | None = None
        if color_tuple is not None:
            r, g, b = color_tuple[0], color_tuple[1], color_tuple[2]
            hex_color = f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"

        # Classify shape type.
        if h < _RULE_THICKNESS_MAX and w > h * 3:
            shape_type = "hrule"
        elif w < _RULE_THICKNESS_MAX and h > w * 3:
            shape_type = "vrule"
        elif "re" in item_types:
            shape_type = "rect"
        else:
            shape_type = "line"

        layout = LayoutHint(
            page=page_num,
            x=x0,
            y=y0,
            width=w,
            height=h,
            reading_order=block_idx,
            color=hex_color,
        )
        node = Shape(
            meta=make_node_metadata(NodeType.SHAPE, layout=layout),
            shape_type=shape_type,
            x=x0,
            y=y0,
            width=w,
            height=h,
        )
        shapes.append(node)
        block_idx += 1

    return shapes
