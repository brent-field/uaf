"""PDF import using PyMuPDF (fitz)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import fitz

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    NodeType,
    Paragraph,
    make_node_metadata,
)

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.db.graph_db import GraphDB


class PdfHandler:
    """Import PDF files via the UAF graph (export not supported)."""

    def import_file(self, path: Path, db: GraphDB) -> NodeId:
        """Extract text blocks from a PDF and create paragraph nodes."""
        doc = fitz.open(str(path))

        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=path.stem)
        art_id = db.create_node(art)

        for page in doc:
            blocks = page.get_text("blocks")
            for block in blocks:
                # block format: (x0, y0, x1, y1, text, block_no, block_type)
                # block_type 0 = text, 1 = image
                if block[6] == 0:  # text block
                    text = block[4].strip()
                    if text:
                        para = Paragraph(
                            meta=make_node_metadata(NodeType.PARAGRAPH), text=text,
                        )
                        nid = db.create_node(para)
                        db.create_edge(_contains(art_id, nid))

        doc.close()
        return art_id

    def export_file(self, db: GraphDB, root_id: NodeId, path: Path) -> None:
        """Export as plain text (PDF generation not supported)."""
        children = db.get_children(root_id)
        parts: list[str] = []

        for child in children:
            if isinstance(child, Paragraph):
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
