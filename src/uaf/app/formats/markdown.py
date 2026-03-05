"""Markdown import/export/compare using mistune AST."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import mistune

from uaf.app.formats import ComparisonResult
from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    CodeBlock,
    Heading,
    Image,
    NodeType,
    Paragraph,
    TextBlock,
    make_node_metadata,
)

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.db.graph_db import GraphDB
    from uaf.db.journaled_graph_db import JournaledGraphDB


class MarkdownHandler:
    """Import/export Markdown files via the UAF graph."""

    def import_file(self, path: Path, db: GraphDB | JournaledGraphDB) -> NodeId:
        """Parse a Markdown file into UAF nodes and edges."""
        text = path.read_text(encoding="utf-8")
        md = mistune.create_markdown(renderer="ast")
        ast: list[dict[str, Any]] = md(text)  # type: ignore[assignment]

        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=path.stem)
        art_id = db.create_node(art)

        for token in ast:
            self._import_token(token, art_id, db)

        return art_id

    def export_file(
        self, db: GraphDB | JournaledGraphDB, root_id: NodeId, path: Path
    ) -> None:
        """Export a UAF artifact as a Markdown file."""
        children = db.get_children(root_id)
        parts: list[str] = []

        for child in children:
            parts.append(self._export_node(child))

        text = "\n".join(parts)
        # Ensure single trailing newline
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text, encoding="utf-8")

    def _import_token(
        self, token: dict[str, Any], parent_id: NodeId, db: GraphDB | JournaledGraphDB
    ) -> None:
        """Convert a mistune AST token into a UAF node and attach it to the parent."""
        token_type = token.get("type", "")

        if token_type == "blank_line":
            return

        if token_type == "heading":
            level: int = token["attrs"]["level"]
            text = _extract_inline_text(token.get("children", []))
            heading = Heading(meta=make_node_metadata(NodeType.HEADING), text=text, level=level)
            nid = db.create_node(heading)
            db.create_edge(_contains(parent_id, nid))

        elif token_type == "paragraph":
            text = _render_inline(token.get("children", []))
            para = Paragraph(meta=make_node_metadata(NodeType.PARAGRAPH), text=text)
            nid = db.create_node(para)
            db.create_edge(_contains(parent_id, nid))

        elif token_type == "block_code":
            info = token.get("attrs", {}).get("info", "") or ""
            source = token.get("raw", "")
            # Strip trailing newline that mistune adds
            if source.endswith("\n"):
                source = source[:-1]
            code = CodeBlock(
                meta=make_node_metadata(NodeType.CODE_BLOCK), source=source, language=info,
            )
            nid = db.create_node(code)
            db.create_edge(_contains(parent_id, nid))

        elif token_type == "list":
            md_text = _render_list(token)
            tb = TextBlock(
                meta=make_node_metadata(NodeType.TEXT_BLOCK), text=md_text, format="markdown",
            )
            nid = db.create_node(tb)
            db.create_edge(_contains(parent_id, nid))

        # Silently skip unknown block-level tokens (thematic_break, etc.)

    def _export_node(self, node: object) -> str:
        """Convert a UAF node back into a Markdown string."""
        match node:
            case Heading(text=text, level=level):
                prefix = "#" * level
                return f"{prefix} {text}\n"
            case Paragraph(text=text):
                return f"{text}\n"
            case CodeBlock(source=source, language=language):
                return f"```{language}\n{source}\n```\n"
            case TextBlock(text=text, format="markdown"):
                return f"{text}\n"
            case Image(uri=uri, alt_text=alt_text):
                return f"![{alt_text}]({uri})\n"
            case _:
                return ""


class MarkdownComparator:
    """Compare two Markdown files for semantic equivalence."""

    def compare(self, original: Path, rebuilt: Path) -> ComparisonResult:
        """Compare original and rebuilt Markdown, ignoring whitespace differences."""
        orig_text = original.read_text(encoding="utf-8")
        rebuilt_text = rebuilt.read_text(encoding="utf-8")

        orig_lines = _normalize_md(orig_text)
        rebuilt_lines = _normalize_md(rebuilt_text)

        differences: list[str] = []
        ignored: list[str] = []

        # Check trailing whitespace / blank line differences
        if orig_text != rebuilt_text:
            orig_stripped = orig_text.rstrip()
            rebuilt_stripped = rebuilt_text.rstrip()
            if orig_stripped == rebuilt_stripped:
                ignored.append("trailing whitespace differs")

        # Compare normalized content
        max_len = max(len(orig_lines), len(rebuilt_lines))
        matching = 0

        for i in range(max_len):
            orig_line = orig_lines[i] if i < len(orig_lines) else ""
            rebuilt_line = rebuilt_lines[i] if i < len(rebuilt_lines) else ""
            if orig_line == rebuilt_line:
                matching += 1
            else:
                differences.append(
                    f"Line {i + 1}: {orig_line!r} != {rebuilt_line!r}"
                )

        score = matching / max_len if max_len > 0 else 1.0
        is_eq = len(differences) == 0

        return ComparisonResult(
            is_equivalent=is_eq,
            differences=differences,
            ignored=ignored,
            similarity_score=score,
        )


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


def _extract_inline_text(children: list[dict[str, Any]]) -> str:
    """Extract plain text from inline AST nodes (for headings)."""
    parts: list[str] = []
    for child in children:
        if child.get("type") == "text":
            parts.append(child.get("raw", ""))
        elif "children" in child:
            parts.append(_extract_inline_text(child["children"]))
        elif "raw" in child:
            parts.append(child["raw"])
    return "".join(parts)


def _render_inline(children: list[dict[str, Any]]) -> str:
    """Render inline AST nodes back to Markdown syntax."""
    parts: list[str] = []
    for child in children:
        child_type = child.get("type", "")
        if child_type == "text":
            parts.append(child.get("raw", ""))
        elif child_type == "strong":
            inner = _render_inline(child.get("children", []))
            parts.append(f"**{inner}**")
        elif child_type == "emphasis":
            inner = _render_inline(child.get("children", []))
            parts.append(f"*{inner}*")
        elif child_type == "codespan":
            parts.append(f"`{child.get('raw', '')}`")
        elif child_type == "link":
            inner = _render_inline(child.get("children", []))
            url = child.get("attrs", {}).get("url", "")
            title = child.get("attrs", {}).get("title")
            if title:
                parts.append(f'[{inner}]({url} "{title}")')
            else:
                parts.append(f"[{inner}]({url})")
        elif child_type == "image":
            alt = _render_inline(child.get("children", []))
            url = child.get("attrs", {}).get("url", "")
            parts.append(f"![{alt}]({url})")
        elif child_type == "softbreak":
            parts.append("\n")
        elif "children" in child:
            parts.append(_render_inline(child["children"]))
        elif "raw" in child:
            parts.append(child["raw"])
    return "".join(parts)


def _render_list(token: dict[str, Any], indent: int = 0) -> str:
    """Render a list AST token back to Markdown."""
    attrs = token.get("attrs", {})
    ordered: bool = attrs.get("ordered", False)
    items = token.get("children", [])
    lines: list[str] = []
    prefix_space = "  " * indent

    for idx, item in enumerate(items):
        bullet = f"{idx + 1}." if ordered else "-"
        item_children = item.get("children", [])
        first = True
        for sub in item_children:
            if sub.get("type") == "list":
                lines.append(_render_list(sub, indent + 1))
            elif sub.get("type") in ("block_text", "paragraph"):
                text = _render_inline(sub.get("children", []))
                if first:
                    lines.append(f"{prefix_space}{bullet} {text}")
                    first = False
                else:
                    lines.append(f"{prefix_space}  {text}")
            else:
                text = _render_inline(sub.get("children", []))
                if first:
                    lines.append(f"{prefix_space}{bullet} {text}")
                    first = False
                else:
                    lines.append(f"{prefix_space}  {text}")

    return "\n".join(lines)


def _normalize_md(text: str) -> list[str]:
    """Normalize Markdown text for comparison: strip trailing whitespace,
    collapse multiple blank lines, strip leading/trailing blank lines."""
    lines = [line.rstrip() for line in text.splitlines()]
    # Collapse multiple consecutive blank lines into one
    normalized: list[str] = []
    prev_blank = False
    for line in lines:
        if line == "":
            if not prev_blank:
                normalized.append("")
            prev_blank = True
        else:
            normalized.append(line)
            prev_blank = False
    # Strip leading and trailing blank lines
    while normalized and normalized[0] == "":
        normalized.pop(0)
    while normalized and normalized[-1] == "":
        normalized.pop()
    return normalized
