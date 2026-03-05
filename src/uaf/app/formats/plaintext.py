"""Plain text import/export/compare."""

from __future__ import annotations

from typing import TYPE_CHECKING

from uaf.app.formats import ComparisonResult
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
    from uaf.db.journaled_graph_db import JournaledGraphDB


class PlainTextHandler:
    """Import/export plain text files via the UAF graph."""

    def import_file(self, path: Path, db: GraphDB | JournaledGraphDB) -> NodeId:
        """Import a plain text file as paragraphs split on blank lines."""
        text = path.read_text(encoding="utf-8")

        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=path.stem)
        art_id = db.create_node(art)

        # Split on blank lines into paragraphs
        paragraphs = _split_paragraphs(text)

        for para_text in paragraphs:
            node = Paragraph(
                meta=make_node_metadata(NodeType.PARAGRAPH), text=para_text, style="plain",
            )
            node_id = db.create_node(node)
            db.create_edge(_contains(art_id, node_id))

        return art_id

    def export_file(
        self, db: GraphDB | JournaledGraphDB, root_id: NodeId, path: Path
    ) -> None:
        """Export a UAF artifact as a plain text file."""
        children = db.get_children(root_id)
        parts: list[str] = []

        for child in children:
            if isinstance(child, Paragraph):
                parts.append(child.text)

        text = "\n\n".join(parts)
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text, encoding="utf-8")


class PlainTextComparator:
    """Compare two plain text files for content equivalence."""

    def compare(self, original: Path, rebuilt: Path) -> ComparisonResult:
        """Compare text files, ignoring trailing whitespace."""
        orig_text = original.read_text(encoding="utf-8")
        rebuilt_text = rebuilt.read_text(encoding="utf-8")

        orig_lines = [line.rstrip() for line in orig_text.splitlines()]
        rebuilt_lines = [line.rstrip() for line in rebuilt_text.splitlines()]

        # Strip trailing empty lines
        while orig_lines and orig_lines[-1] == "":
            orig_lines.pop()
        while rebuilt_lines and rebuilt_lines[-1] == "":
            rebuilt_lines.pop()

        differences: list[str] = []
        ignored: list[str] = []

        if orig_text.rstrip() != rebuilt_text.rstrip() and orig_lines == rebuilt_lines:
            ignored.append("trailing whitespace differs")

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


def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs on blank lines, preserving internal line breaks."""
    lines = text.splitlines()
    paragraphs: list[str] = []
    current: list[str] = []

    for line in lines:
        stripped = line.rstrip()
        if stripped == "":
            if current:
                paragraphs.append("\n".join(current))
                current = []
        else:
            current.append(stripped)

    if current:
        paragraphs.append("\n".join(current))

    return paragraphs
