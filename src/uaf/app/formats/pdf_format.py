"""PDF import using PyMuPDF (fitz) — with layout metadata extraction."""

from __future__ import annotations

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

                font = _extract_dominant_font(block)
                bbox = block.get("bbox", (0.0, 0.0, 0.0, 0.0))
                x0, y0, x1, y1 = (
                    float(bbox[0]),
                    float(bbox[1]),
                    float(bbox[2]),
                    float(bbox[3]),
                )

                layout = LayoutHint(
                    page=page_num,
                    x=x0,
                    y=y0,
                    width=x1 - x0,
                    height=y1 - y0,
                    font_family=font.get("family"),
                    font_size=font.get("size"),
                    font_weight=font.get("weight"),
                    font_style=font.get("style"),
                    color=font.get("color"),
                    reading_order=block_index,
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
    """Aggregate text from all lines/spans in a dict-format block."""
    parts: list[str] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            parts.append(span.get("text", ""))
    return " ".join(parts).strip()


def _extract_dominant_font(block: dict[str, Any]) -> dict[str, Any]:
    """Pick the most common font properties across spans in a block."""
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
        result["family"] = families.most_common(1)[0][0]
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
