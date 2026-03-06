"""PDF import using PyMuPDF (fitz) — with layout metadata extraction."""

from __future__ import annotations

import dataclasses
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import fitz

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    FontAnnotation,
    Heading,
    LayoutHint,
    MathBlock,
    NodeType,
    Paragraph,
    Shape,
    SpanInfo,
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

@dataclass(frozen=True, slots=True)
class _FontSpan:
    """Internal per-span data for building font annotations."""

    text: str
    css_family: str | None
    font_style: str | None
    pdf_font: str  # original PDF font name (e.g. "CMMI10")
    font_size: float | None
    font_weight: str | None
    y_top: float | None  # top of span bbox (for sub/super detection)

# Reusable 4-float bbox type alias (avoids N806 inside functions).
_Bbox4 = tuple[float, float, float, float]

# Computer Modern font prefixes that indicate mathematical content.
_CM_MATH_PREFIXES = ("CMR", "CMMI", "CMSY", "CMEX", "CMB")

# Pattern to detect equation numbers like "(3)", "(2.1)", "(A.3)".
_EQ_NUM_RE = re.compile(r"\((\d+(?:\.\d+)?|[A-Z](?:\.\d+)?)\)\s*$")


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

                line_ht = _compute_line_height(
                    block, dominant_font_size=font.get("size"),
                )

                # Per-visual-line positions (relative to block bbox origin)
                # for precise per-line positioning in the renderer.
                line_tops, line_lefts = _compute_line_positions(
                    block, x0, y0,
                )

                # Detect math block: majority Computer Modern math fonts
                is_math = _is_math_block(block)

                # Math-majority blocks (display equations) get full per-span
                # metadata with absolute positioning for sub/superscripts.
                # Non-math blocks with some math fonts (inline math in
                # paragraphs) get font annotations — lightweight markers
                # on the display_text that tell the renderer which
                # character ranges need math font styling.  This preserves
                # the existing text flow (line breaks, white-space, spacing)
                # while giving math characters their correct font-family.
                span_list = _build_span_list(block) if is_math else None
                font_annots: tuple[FontAnnotation, ...] | None = None
                if not is_math and (
                    _has_math_fonts(block) or _has_mixed_weight(block)
                ):
                    font_annots = _build_font_annotations(
                        block, font.get("family"),
                        dominant_font_size=font.get("size"),
                        dominant_weight=font.get("weight"),
                    )

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
                    line_height=line_ht,
                    spans=span_list,
                    font_annotations=font_annots,
                    line_tops=line_tops,
                    line_lefts=line_lefts,
                )

                # For math blocks, the character-weighted dominant font size
                # can be wrong: dense subscripts/superscripts (7pt, 5pt) may
                # outnumber base-size (10pt) characters.  Use the max span
                # font size as the base size instead.
                if is_math and span_list:
                    max_sz = max(
                        (s.font_size for s in span_list if s.font_size),
                        default=None,
                    )
                    if max_sz is not None:
                        layout = dataclasses.replace(layout, font_size=max_sz)

                # Detect heading heuristic: large font or bold
                is_heading = (
                    not is_math
                    and font.get("size") is not None
                    and font["size"] >= 16.0
                    and len(text) < 200
                )

                node: Heading | Paragraph | MathBlock
                if is_math:
                    source_text = raw_text if raw_text else text
                    source_clean, eq_num = _extract_equation_number(source_text)
                    node = MathBlock(
                        meta=make_node_metadata(
                            NodeType.MATH_BLOCK, layout=layout,
                        ),
                        source=source_clean,
                        equation_number=eq_num,
                    )
                elif is_heading:
                    level = _heading_level_from_size(font.get("size", 12.0))
                    node = Heading(
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
            elif isinstance(child, MathBlock):
                parts.append(child.source)

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

    PyMuPDF sometimes splits text that appears on the same visual line into
    separate "line" objects (e.g. section numbers and titles, or equation
    parts).  These same-baseline lines are merged with a space so the
    layout view matches PDF viewers.
    """
    visual_lines = _merge_visual_lines(block)
    return "\n".join(visual_lines).strip()


def _merge_visual_lines(block: dict[str, Any]) -> list[str]:
    """Group PyMuPDF lines by visual baseline and return merged text.

    PyMuPDF may split text on the same visual line into separate ``line``
    objects — e.g. "1" and "Introduction" in a section heading, or equation
    fragments with equation numbers.  This function detects lines that share
    the same baseline (significant y-overlap) and merges their text with a
    space separator.

    Returns a list of visual-line strings (one per distinct baseline).
    """
    lines = block.get("lines", [])
    if not lines:
        return []

    # Build list of (text, bbox) for each PyMuPDF line.
    line_data: list[tuple[str, tuple[float, float, float, float]]] = []
    for line in lines:
        parts: list[str] = []
        for span in line.get("spans", []):
            parts.append(span.get("text", ""))
        text = "".join(parts)
        bbox = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
        line_data.append((text, (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))))

    # Group consecutive lines that share the same baseline.
    # Track the *group envelope* bbox (union of all bboxes in the current
    # visual line) so that small sub-fragments (e.g. fraction numerator
    # and denominator) are compared against the full group extent, not
    # just the previous raw line.
    visual: list[str] = [line_data[0][0]]
    group_bbox = line_data[0][1]

    for text, bbox in line_data[1:]:
        if _same_baseline(group_bbox, bbox):
            # Same visual line — append with space and expand envelope.
            visual[-1] = visual[-1] + " " + text
            group_bbox = (
                min(group_bbox[0], bbox[0]),
                min(group_bbox[1], bbox[1]),
                max(group_bbox[2], bbox[2]),
                max(group_bbox[3], bbox[3]),
            )
        else:
            visual.append(text)
            group_bbox = bbox

    return visual


def _same_baseline(
    bbox_a: tuple[float, float, float, float],
    bbox_b: tuple[float, float, float, float],
) -> bool:
    """Check whether two line bboxes share the same visual baseline.

    Two lines are on the same baseline when their y-ranges overlap by more
    than 50 % of the shorter line's height.
    """
    y_a_top, y_a_bot = bbox_a[1], bbox_a[3]
    y_b_top, y_b_bot = bbox_b[1], bbox_b[3]

    overlap = min(y_a_bot, y_b_bot) - max(y_a_top, y_b_top)
    min_height = min(y_a_bot - y_a_top, y_b_bot - y_b_top)

    if min_height <= 0:
        return False
    return overlap / min_height > 0.5


def _compute_line_height(
    block: dict[str, Any],
    dominant_font_size: float | None = None,
) -> float | None:
    """Compute inter-line spacing from PDF block data.

    Returns the median top-to-top distance between consecutive *visual*
    lines (after merging same-baseline segments).  Uses median instead
    of average to resist outliers from subscript/superscript pseudo-lines.
    Sub-line spacings (less than 60% of the dominant font size) are
    filtered out.  Returns ``None`` for single-line blocks.
    """
    lines = block.get("lines", [])
    if not lines:
        return None

    # Collect the y-top of each visual line group.
    # Track the group envelope bbox so that small sub-fragments (e.g.
    # fraction numerator/denominator) are compared against the full
    # group extent, not just the previous raw line.
    visual_tops: list[float] = [
        float(lines[0].get("bbox", (0, 0, 0, 0))[1]),
    ]
    raw_bb = lines[0].get("bbox", (0.0, 0.0, 0.0, 0.0))
    group_bbox = (
        float(raw_bb[0]), float(raw_bb[1]),
        float(raw_bb[2]), float(raw_bb[3]),
    )

    for line in lines[1:]:
        raw = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
        bbox = (
            float(raw[0]), float(raw[1]),
            float(raw[2]), float(raw[3]),
        )
        if not _same_baseline(group_bbox, bbox):
            visual_tops.append(bbox[1])
            group_bbox = bbox
        else:
            group_bbox = (
                min(group_bbox[0], bbox[0]),
                min(group_bbox[1], bbox[1]),
                max(group_bbox[2], bbox[2]),
                max(group_bbox[3], bbox[3]),
            )

    if len(visual_tops) < 2:
        return None

    spacings = [
        visual_tops[i + 1] - visual_tops[i]
        for i in range(len(visual_tops) - 1)
    ]

    # Filter out sub-line spacings from subscript/superscript fragments.
    dom_size = dominant_font_size or 10.0
    min_spacing = dom_size * 0.6
    normal_spacings = [s for s in spacings if s >= min_spacing]

    if not normal_spacings:
        # All spacings were sub-line; fall back to unfiltered
        normal_spacings = spacings

    # Use median instead of average to resist remaining outliers.
    normal_spacings.sort()
    mid = len(normal_spacings) // 2
    if len(normal_spacings) % 2 == 0 and len(normal_spacings) >= 2:
        median = (normal_spacings[mid - 1] + normal_spacings[mid]) / 2
    else:
        median = normal_spacings[mid]

    return round(median, 1)


def _compute_line_positions(
    block: dict[str, Any],
    block_x0: float,
    block_y0: float,
) -> tuple[tuple[float, ...] | None, tuple[float, ...] | None]:
    """Compute per-visual-line positions relative to block bbox origin.

    Uses the same baseline-merging logic as :func:`_merge_visual_lines`
    to identify distinct visual lines, then returns:

    - ``line_tops``: y-offset of each visual line relative to block top
    - ``line_lefts``: x-offset of each visual line relative to block left
      (only when at least one line has a non-zero x-offset; ``None`` otherwise)

    Returns ``(None, None)`` for single-line blocks.
    """
    lines = block.get("lines", [])
    if not lines:
        return None, None

    raw_bb = lines[0].get("bbox", (0.0, 0.0, 0.0, 0.0))
    group_bbox = (
        float(raw_bb[0]), float(raw_bb[1]),
        float(raw_bb[2]), float(raw_bb[3]),
    )
    # Track the minimum y0 across all raw lines in the group so the
    # visual line's top reflects the true content extent (not just
    # the first raw line's y0).
    group_min_y: float = group_bbox[1]
    tops: list[float] = [round(group_min_y - block_y0, 1)]
    # Track min x per visual line group for x-offset.
    cur_min_x: float = group_bbox[0]
    lefts: list[float] = []

    for line in lines[1:]:
        lb = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
        cur_bbox = (
            float(lb[0]), float(lb[1]),
            float(lb[2]), float(lb[3]),
        )
        if not _same_baseline(group_bbox, cur_bbox):
            # Finish previous visual line group.
            lefts.append(round(cur_min_x - block_x0, 1))
            # Start new group.
            group_bbox = cur_bbox
            group_min_y = cur_bbox[1]
            tops.append(round(group_min_y - block_y0, 1))
            cur_min_x = cur_bbox[0]
        else:
            # Expand group envelope and update min y/x.
            group_bbox = (
                min(group_bbox[0], cur_bbox[0]),
                min(group_bbox[1], cur_bbox[1]),
                max(group_bbox[2], cur_bbox[2]),
                max(group_bbox[3], cur_bbox[3]),
            )
            group_min_y = min(group_min_y, cur_bbox[1])
            cur_min_x = min(cur_min_x, cur_bbox[0])

    # Finish last visual line group.
    lefts.append(round(cur_min_x - block_x0, 1))

    if len(tops) < 2:
        return None, None

    # Only return line_lefts if at least one line has a non-zero offset.
    has_offsets = any(x > 0.5 for x in lefts)
    return tuple(tops), tuple(lefts) if has_offsets else None


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
    ("CMSY", '"Cambria Math", "Apple Symbols", Symbol, serif'),  # CM Symbols
    ("CMMI", '"Times New Roman", Times, serif'),  # CM Math Italic
    ("CMEX", "Symbol, serif"),  # CM Extension (large brackets, integrals)
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


def _is_math_block(block: dict[str, Any]) -> bool:
    """Detect whether a block is a math equation based on font usage.

    A block is classified as math if Computer Modern math fonts (CMMI, CMSY,
    CMEX, CMR, etc.) account for the majority of the text characters.
    """
    cm_chars = 0
    total_chars = 0
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text_len = len(span.get("text", ""))
            if text_len == 0:
                continue
            total_chars += text_len
            font = span.get("font", "")
            if any(font.startswith(p) for p in _CM_MATH_PREFIXES):
                cm_chars += text_len
    if total_chars == 0:
        return False
    return cm_chars / total_chars > 0.5


def _extract_equation_number(text: str) -> tuple[str, str | None]:
    """Extract an equation number from the end of display text.

    Returns (cleaned_source, equation_number).  If no equation number is
    found, returns (text, None).
    """
    m = _EQ_NUM_RE.search(text)
    if m is not None:
        eq_num = m.group(0).strip()
        source = text[: m.start()].rstrip()
        return source, eq_num
    return text, None


def _has_math_fonts(block: dict[str, Any]) -> bool:
    """Check whether a block contains any Computer Modern math font spans.

    Returns ``True`` if at least one span uses a CM math font (CMMI, CMSY,
    CMEX, CMR, CMB).  This is a weaker test than :func:`_is_math_block`
    (which requires a *majority* of CM characters) and is used to decide
    whether per-span metadata is needed for sub/superscript positioning.
    """
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            font = span.get("font", "")
            if any(font.startswith(p) for p in _CM_MATH_PREFIXES):
                return True
    return False


def _has_mixed_weight(block: dict[str, Any]) -> bool:
    """Check whether a block contains spans with different font weights.

    Returns ``True`` if the block has both bold and non-bold spans,
    indicating inline bold labels (e.g. "Level Pruning:" in a paragraph).
    """
    has_bold = False
    has_normal = False
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            if not span.get("text", ""):
                continue
            flags = span.get("flags", 0)
            if flags & (1 << 4):
                has_bold = True
            else:
                has_normal = True
            if has_bold and has_normal:
                return True
    return False


def _build_span_list(block: dict[str, Any]) -> tuple[SpanInfo, ...] | None:
    """Build per-span font metadata from the raw PyMuPDF block.

    Returns ``None`` when all spans have uniform font properties (the
    block-level LayoutHint already captures everything needed).

    Only called for math-majority blocks (display equations) where
    absolute positioning is needed for sub/superscript layout.
    Non-math blocks render as plain text to preserve word spacing.
    """
    spans: list[SpanInfo] = []
    seen_sizes: set[float] = set()

    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = span.get("text", "")
            if not text:
                continue
            font = span.get("font", "")
            family = _map_font_family(font) if font else None
            size = round(float(span["size"]), 1) if span.get("size") is not None else None
            flags = span.get("flags", 0)
            weight: str | None = "bold" if flags & (1 << 4) else None
            style: str | None = "italic" if flags & (1 << 1) else None

            # Compute x/y offset from span bbox relative to block bbox.
            # Using bbox (glyph top-left) rather than origin (baseline)
            # because CSS ``top:`` positions from the element's top edge.
            span_bbox = span.get("bbox")
            block_bbox = block.get("bbox", (0.0, 0.0, 0.0, 0.0))
            x_off: float | None = None
            y_off: float | None = None
            if span_bbox is not None and len(span_bbox) >= 4:
                x_off = round(float(span_bbox[0]) - float(block_bbox[0]), 1)
                y_off = round(float(span_bbox[1]) - float(block_bbox[1]), 1)

            spans.append(SpanInfo(
                text=text,
                font_size=size,
                font_family=family,
                font_weight=weight,
                font_style=style,
                y_offset=y_off,
                x_offset=x_off,
            ))

            if size is not None:
                seen_sizes.add(size)

    # Only store spans when font sizes vary significantly (>2pt spread).
    # Tiny variations (9.9, 10.0, 10.1) are rounding noise.
    if len(seen_sizes) < 2 or (max(seen_sizes) - min(seen_sizes) < 2.0):
        return None

    return tuple(spans)


def _is_math_font(pdf_font: str) -> bool:
    """Check if a PDF font name is a Computer Modern math font."""
    return any(pdf_font.startswith(p) for p in _CM_MATH_PREFIXES)


def _detect_vertical_align(
    span: _FontSpan,
    dominant_size: float | None,
    dominant_y: float | None,
) -> str | None:
    """Detect sub/superscript from font size and vertical position.

    A span is a subscript if its font_size is significantly smaller than
    the dominant size AND its y_top is below the dominant baseline.
    A span is a superscript if its y_top is above the dominant line top.
    """
    if dominant_size is None or span.font_size is None:
        return None
    if dominant_y is None or span.y_top is None:
        return None

    size_ratio = span.font_size / dominant_size
    if size_ratio > 0.85:
        return None  # same size, not sub/super

    y_diff = span.y_top - dominant_y
    # Positive y_diff = below dominant top = subscript territory
    # Negative y_diff = above dominant top = superscript territory
    threshold = dominant_size * 0.15
    if y_diff > threshold:
        return "sub"
    if y_diff < -threshold:
        return "super"
    return None


def _build_font_annotations(
    block: dict[str, Any],
    dominant_family: str | None,
    dominant_font_size: float | None = None,
    dominant_weight: str | None = None,
) -> tuple[FontAnnotation, ...] | None:
    """Build font annotations for character ranges that differ from the block default.

    Uses the same baseline-merging logic as :func:`_merge_visual_lines`
    to walk through the block in visual-line order.  Annotations are
    created for spans that:

    - use a Computer Modern math font,
    - have a different CSS font-family than the dominant, or
    - have a different font-weight than the dominant (e.g. bold labels).

    The character offsets are computed by reconstructing the same text that
    :func:`_extract_raw_block_text` produces, ensuring perfect alignment.

    Returns ``None`` if no annotatable spans are found.
    """
    lines = block.get("lines", [])
    if not lines:
        return None

    # Build per-line data with full font metadata per span.
    line_entries: list[tuple[list[_FontSpan], _Bbox4]] = []
    for line in lines:
        spans_data: list[_FontSpan] = []
        for span in line.get("spans", []):
            text = span.get("text", "")
            if not text:
                continue
            font = span.get("font", "")
            family = _map_font_family(font) if font else None
            flags = span.get("flags", 0)
            style: str | None = "italic" if flags & (1 << 1) else None
            weight: str | None = (
                "bold" if flags & (1 << 4) else None
            )
            size: float | None = (
                round(float(span["size"]), 1) if span.get("size") else None
            )
            span_bbox = span.get("bbox")
            y_top: float | None = None
            if span_bbox and len(span_bbox) >= 2:
                y_top = float(span_bbox[1])
            spans_data.append(_FontSpan(
                text=text, css_family=family, font_style=style,
                pdf_font=font, font_size=size, font_weight=weight,
                y_top=y_top,
            ))
        raw_bbox = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
        bbox: _Bbox4 = (
            float(raw_bbox[0]), float(raw_bbox[1]),
            float(raw_bbox[2]), float(raw_bbox[3]),
        )
        line_entries.append((spans_data, bbox))

    if not line_entries:
        return None

    # Compute dominant y-top from non-math spans for sub/super detection.
    non_math_tops: list[float] = []
    for span_list, _bbox in line_entries:
        for span in span_list:
            if not _is_math_font(span.pdf_font) and span.y_top is not None:
                non_math_tops.append(span.y_top)

    dominant_y: float | None = None
    if non_math_tops:
        sorted_tops = sorted(non_math_tops)
        dominant_y = sorted_tops[len(sorted_tops) // 2]

    # Group lines by visual baseline (mirrors _merge_visual_lines) and
    # collect annotations.
    annotations: list[FontAnnotation] = []
    offset = 0  # character position in display_text
    is_first_visual_line = True

    # Pending spans for the current visual line (accumulated across
    # same-baseline PDF lines, with space separators between merges).
    pending: list[_FontSpan] = list(line_entries[0][0])
    prev_bbox = line_entries[0][1]

    def _flush(spans: list[_FontSpan], char_offset: int) -> int:
        """Emit annotations for a merged visual line."""
        dom_w = dominant_weight or "normal"
        for span in spans:
            end = char_offset + len(span.text)
            # Annotate if: CSS family differs, it's a CM math font,
            # or font weight differs from the block dominant.
            family_differs = (
                span.css_family
                and span.css_family != dominant_family
            )
            is_math = bool(span.pdf_font) and _is_math_font(
                span.pdf_font,
            )
            weight_differs = (
                span.font_weight is not None
                and span.font_weight != dom_w
            )
            if family_differs or is_math or weight_differs:
                valign = _detect_vertical_align(
                    span, dominant_font_size, dominant_y,
                )
                annotations.append(FontAnnotation(
                    start=char_offset, end=end,
                    font_family=span.css_family or "",
                    font_style=span.font_style,
                    font_size=span.font_size,
                    font_weight=span.font_weight,
                    vertical_align=valign,
                ))
            char_offset = end
        return char_offset

    for idx in range(1, len(line_entries)):
        spans_data, bbox = line_entries[idx]
        if _same_baseline(prev_bbox, bbox):
            # Same visual line — add space separator then spans.
            pending.append(_FontSpan(
                text=" ", css_family=None, font_style=None,
                pdf_font="", font_size=None, font_weight=None,
                y_top=None,
            ))
            pending.extend(spans_data)
        else:
            # New visual line — flush pending.
            if not is_first_visual_line:
                offset += 1  # skip \n between visual lines
            offset = _flush(pending, offset)
            is_first_visual_line = False
            pending = list(spans_data)
        prev_bbox = bbox

    # Flush last visual line.
    if not is_first_visual_line:
        offset += 1
    _flush(pending, offset)

    if not annotations:
        return None
    return tuple(annotations)


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
