"""Google Docs import — accepts exported JSON or .gdoc reference files."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    Heading,
    NodeType,
    Paragraph,
    make_node_metadata,
)

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.db.graph_db import GraphDB


class GdocHandler:
    """Import Google Docs JSON exports or .gdoc reference files."""

    def import_file(self, path: Path, db: GraphDB) -> NodeId:
        """Parse a Google Docs JSON export into UAF nodes."""
        raw = path.read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(raw)

        title = data.get("title", path.stem)
        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)
        art_id = db.create_node(art)

        # Google Docs JSON export format: body.content[] with structural elements
        body = data.get("body", {})
        content_list: list[dict[str, Any]] = body.get("content", [])

        for element in content_list:
            self._import_structural_element(element, art_id, db)

        return art_id

    def export_file(self, db: GraphDB, root_id: NodeId, path: Path) -> None:
        """Export as Google Docs JSON format."""
        children = db.get_children(root_id)
        art = db.get_node(root_id)
        title = art.title if isinstance(art, Artifact) else "Untitled"

        content: list[dict[str, Any]] = []
        for child in children:
            element = self._export_node(child)
            if element:
                content.append(element)

        doc: dict[str, Any] = {
            "title": title,
            "body": {"content": content},
        }

        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    def _import_structural_element(
        self, element: dict[str, Any], parent_id: NodeId, db: GraphDB,
    ) -> None:
        """Convert a Google Docs structural element into UAF nodes."""
        para_data = element.get("paragraph")
        if para_data is None:
            return

        style = para_data.get("paragraphStyle", {})
        named_style = style.get("namedStyleType", "NORMAL_TEXT")

        text = _extract_paragraph_text(para_data)
        if not text:
            return

        graph_node: Heading | Paragraph
        if named_style.startswith("HEADING_"):
            level = _parse_heading_level(named_style)
            graph_node = Heading(
                meta=make_node_metadata(NodeType.HEADING), text=text, level=level,
            )
        else:
            graph_node = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text=text)

        nid = db.create_node(graph_node)
        db.create_edge(_contains(parent_id, nid))

    def _export_node(self, node: object) -> dict[str, Any] | None:
        """Convert a UAF node to a Google Docs structural element."""
        if isinstance(node, Heading):
            return {
                "paragraph": {
                    "paragraphStyle": {
                        "namedStyleType": f"HEADING_{node.level}",
                    },
                    "elements": [{"textRun": {"content": node.text + "\n"}}],
                },
            }
        if isinstance(node, Paragraph):
            return {
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "elements": [{"textRun": {"content": node.text + "\n"}}],
                },
            }
        return None


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


def _extract_paragraph_text(para_data: dict[str, Any]) -> str:
    """Extract text from a Google Docs paragraph's elements."""
    elements: list[dict[str, Any]] = para_data.get("elements", [])
    parts: list[str] = []
    for el in elements:
        text_run = el.get("textRun")
        if text_run:
            content = text_run.get("content", "")
            parts.append(content)
    text = "".join(parts).strip()
    return text


def _parse_heading_level(named_style: str) -> int:
    """Extract heading level from a named style like 'HEADING_2'."""
    suffix = named_style.replace("HEADING_", "")
    if suffix.isdigit():
        return max(1, min(int(suffix), 6))
    return 1
