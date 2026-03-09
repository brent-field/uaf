"""LaTeX import/export/compare using pylatexenc AST."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pylatexenc.latexwalker import (
    LatexCharsNode,
    LatexCommentNode,
    LatexEnvironmentNode,
    LatexGroupNode,
    LatexMacroNode,
    LatexMathNode,
    LatexWalker,
)

from uaf.app.formats import ComparisonResult
from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, NodeId, utc_now
from uaf.core.nodes import (
    Artifact,
    CodeBlock,
    Heading,
    MathBlock,
    NodeType,
    Paragraph,
    TextBlock,
    make_node_metadata,
)

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.db.graph_db import GraphDB

# Section commands → heading levels
_SECTION_LEVELS: dict[str, int] = {
    "section": 1,
    "subsection": 2,
    "subsubsection": 3,
}

# Environments that produce MathBlock nodes
_MATH_ENVS = {"equation", "equation*", "align", "align*", "gather", "gather*", "multline"}

# Environments that produce CodeBlock nodes
_CODE_ENVS = {"verbatim", "lstlisting"}

# Environments that produce TextBlock nodes (lists)
_LIST_ENVS = {"itemize", "enumerate"}


class LatexHandler:
    """Import/export LaTeX files via the UAF graph."""

    def import_file(self, path: Path, db: GraphDB) -> NodeId:
        """Parse a LaTeX file into UAF nodes and edges."""
        text = path.read_text(encoding="utf-8")

        # Extract title from \title{...} if present
        title = _extract_title(text) or path.stem

        art = Artifact(meta=make_node_metadata(NodeType.ARTIFACT), title=title)
        art_id = db.create_node(art)

        # Parse the document body
        doc_nodes = _get_document_body(text)
        if doc_nodes is None:
            # No \begin{document}, parse the whole thing
            walker = LatexWalker(text)
            doc_nodes, _, _ = walker.get_latex_nodes()

        # Walk the AST and create graph nodes
        self._import_nodes(doc_nodes, art_id, db)

        return art_id

    def export_file(self, db: GraphDB, root_id: NodeId, path: Path) -> None:
        """Export a UAF artifact as a LaTeX file."""
        art = db.get_node(root_id)
        children = db.get_children(root_id)

        parts: list[str] = []
        parts.append(r"\documentclass{article}")
        if art is not None and isinstance(art, Artifact):
            parts.append(rf"\title{{{art.title}}}")
        parts.append(r"\begin{document}")
        parts.append(r"\maketitle")
        parts.append("")

        for child in children:
            parts.append(self._export_node(child))

        parts.append(r"\end{document}")
        parts.append("")  # trailing newline

        path.write_text("\n".join(parts), encoding="utf-8")

    def _import_nodes(self, nodes: list[Any], parent_id: NodeId, db: GraphDB) -> None:
        """Walk AST nodes and create UAF graph nodes."""
        # Accumulate inline content (text + inline math) into paragraphs
        text_buffer: list[str] = []

        for node in nodes:
            if isinstance(node, LatexCommentNode):
                continue

            if isinstance(node, LatexMacroNode):
                if node.macroname in _SECTION_LEVELS:
                    # Flush any buffered text as paragraphs
                    _flush_paragraphs(text_buffer, parent_id, db)
                    text_buffer = []
                    # Create heading
                    heading_text = _extract_macro_arg(node)
                    level = _SECTION_LEVELS[node.macroname]
                    h = Heading(
                        meta=make_node_metadata(NodeType.HEADING),
                        text=heading_text,
                        level=level,
                    )
                    nid = db.create_node(h)
                    db.create_edge(_contains(parent_id, nid))
                elif node.macroname in ("maketitle", "tableofcontents"):
                    # Skip structural macros
                    continue
                elif node.macroname == "item":
                    # Items handled by list environment processing
                    continue
                else:
                    # Unknown macro — render back to LaTeX and add to text buffer
                    text_buffer.append(_macro_to_text(node))

            elif isinstance(node, LatexEnvironmentNode):
                # Flush buffered text first
                _flush_paragraphs(text_buffer, parent_id, db)
                text_buffer = []

                env_name = node.environmentname

                if env_name in _MATH_ENVS:
                    source = _extract_env_content(node).strip()
                    mb = MathBlock(
                        meta=make_node_metadata(NodeType.MATH_BLOCK),
                        source=source,
                        display="block",
                    )
                    nid = db.create_node(mb)
                    db.create_edge(_contains(parent_id, nid))

                elif env_name in _CODE_ENVS:
                    raw = _extract_env_content(node)
                    # lstlisting may have [options] at the start
                    source, language = _parse_code_content(raw, env_name)
                    cb = CodeBlock(
                        meta=make_node_metadata(NodeType.CODE_BLOCK),
                        source=source,
                        language=language,
                    )
                    nid = db.create_node(cb)
                    db.create_edge(_contains(parent_id, nid))

                elif env_name in _LIST_ENVS:
                    list_text = _render_list_env(node)
                    tb = TextBlock(
                        meta=make_node_metadata(NodeType.TEXT_BLOCK),
                        text=list_text,
                        format="latex",
                    )
                    nid = db.create_node(tb)
                    db.create_edge(_contains(parent_id, nid))

                elif env_name == "document":
                    # Process document body recursively
                    if node.nodelist:
                        self._import_nodes(list(node.nodelist), parent_id, db)

                # else: skip unknown environments

            elif isinstance(node, LatexMathNode):
                # Inline math — add to text buffer with delimiters
                content = _nodes_to_text(node.nodelist) if node.nodelist else ""
                text_buffer.append(f"${content}$")

            elif isinstance(node, LatexCharsNode):
                text_buffer.append(node.chars)

            elif isinstance(node, LatexGroupNode) and node.nodelist:
                text_buffer.append(_nodes_to_text(node.nodelist))

        # Flush remaining text
        _flush_paragraphs(text_buffer, parent_id, db)

    def _export_node(self, node: object) -> str:
        """Convert a UAF node back into a LaTeX string."""
        match node:
            case Heading(text=text, level=level):
                cmd = {1: "section", 2: "subsection", 3: "subsubsection"}.get(
                    level, "section",
                )
                return f"\\{cmd}{{{text}}}\n"
            case Paragraph(text=text):
                return f"{text}\n"
            case MathBlock(source=source, display="block"):
                return f"\\begin{{equation}}\n{source}\n\\end{{equation}}\n"
            case MathBlock(source=source):
                return f"${source}$\n"
            case CodeBlock(source=source, language=language):
                if language:
                    return (
                        f"\\begin{{lstlisting}}[language={language}]\n"
                        f"{source}\n"
                        f"\\end{{lstlisting}}\n"
                    )
                return f"\\begin{{verbatim}}\n{source}\n\\end{{verbatim}}\n"
            case TextBlock(text=text):
                return f"{text}\n"
            case _:
                return ""


class LatexComparator:
    """Compare two LaTeX files for semantic equivalence."""

    def compare(self, original: Path, rebuilt: Path) -> ComparisonResult:
        """Compare original and rebuilt LaTeX, ignoring comments, whitespace, and preamble."""
        orig_text = original.read_text(encoding="utf-8")
        rebuilt_text = rebuilt.read_text(encoding="utf-8")

        orig_lines = _normalize_latex(orig_text, strip_preamble=True)
        rebuilt_lines = _normalize_latex(rebuilt_text, strip_preamble=True)

        differences: list[str] = []
        ignored: list[str] = []

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


def _extract_title(text: str) -> str | None:
    """Extract the title from \\title{...}."""
    m = re.search(r"\\title\{([^}]+)\}", text)
    return m.group(1) if m else None


def _get_document_body(text: str) -> list[Any] | None:
    """Parse the text and return the nodelist of the document environment."""
    walker = LatexWalker(text)
    nodes, _, _ = walker.get_latex_nodes()
    for node in nodes:
        if isinstance(node, LatexEnvironmentNode) and node.environmentname == "document":
            return list(node.nodelist) if node.nodelist else []
    return None


def _extract_macro_arg(node: LatexMacroNode) -> str:
    """Extract the text of the first braced argument of a macro."""
    if node.nodeargd and hasattr(node.nodeargd, "argnlist"):
        for arg in node.nodeargd.argnlist:
            if arg is not None and isinstance(arg, LatexGroupNode):
                return _nodes_to_text(arg.nodelist) if arg.nodelist else ""
    return ""


def _extract_env_content(node: LatexEnvironmentNode) -> str:
    """Extract the raw text content of an environment."""
    # Verbatim-style environments store content in nodeargd.verbatim_text
    if hasattr(node.nodeargd, "verbatim_text") and node.nodeargd.verbatim_text:
        return node.nodeargd.verbatim_text  # type: ignore[no-any-return]
    if node.nodelist:
        return _nodes_to_text(node.nodelist)
    return ""


def _nodes_to_text(nodes: Any) -> str:
    """Convert a list of AST nodes to plain text."""
    parts: list[str] = []
    for n in nodes:
        if isinstance(n, LatexCharsNode):
            parts.append(n.chars)
        elif isinstance(n, LatexMathNode):
            inner = _nodes_to_text(n.nodelist) if n.nodelist else ""
            parts.append(f"${inner}$")
        elif isinstance(n, LatexMacroNode):
            parts.append(_macro_to_text(n))
        elif isinstance(n, LatexGroupNode):
            if n.nodelist:
                inner = _nodes_to_text(n.nodelist)
                # Preserve braces for grouping in math/LaTeX context
                if n.delimiters and n.delimiters[0] == "{":
                    parts.append(f"{{{inner}}}")
                else:
                    parts.append(inner)
        elif isinstance(n, LatexCommentNode):
            continue
    return "".join(parts)


def _macro_to_text(node: LatexMacroNode) -> str:
    """Render a macro back to LaTeX text."""
    text = f"\\{node.macroname}"
    if node.nodeargd and hasattr(node.nodeargd, "argnlist"):
        for arg in node.nodeargd.argnlist:
            if arg is not None and isinstance(arg, LatexGroupNode):
                inner = _nodes_to_text(arg.nodelist) if arg.nodelist else ""
                text += f"{{{inner}}}"
    return text


def _parse_code_content(raw: str, env_name: str) -> tuple[str, str]:
    """Parse code content, extracting optional language from lstlisting."""
    language = ""
    source = raw

    if env_name == "lstlisting":
        # lstlisting body may start with [options]
        m = re.match(r"\[([^\]]*)\](.*)", source, re.DOTALL)
        if m:
            opts = m.group(1)
            source = m.group(2)
            # Extract language= option
            lang_m = re.search(r"language=(\w+)", opts)
            if lang_m:
                language = lang_m.group(1)

    # Strip leading/trailing newlines
    source = source.strip("\n")
    return source, language


def _render_list_env(node: LatexEnvironmentNode) -> str:
    """Render an itemize/enumerate environment to LaTeX source."""
    items: list[str] = []
    current_text: list[str] = []

    if not node.nodelist:
        return ""

    for child in node.nodelist:
        if isinstance(child, LatexMacroNode) and child.macroname == "item":
            if current_text:
                items.append("".join(current_text).strip())
                current_text = []
        elif isinstance(child, LatexCharsNode):
            current_text.append(child.chars)
        elif isinstance(child, LatexMathNode):
            inner = _nodes_to_text(child.nodelist) if child.nodelist else ""
            current_text.append(f"${inner}$")

    if current_text:
        items.append("".join(current_text).strip())

    # Filter empty items
    items = [item for item in items if item]

    env = node.environmentname
    lines: list[str] = [f"\\begin{{{env}}}"]
    for item in items:
        lines.append(f"\\item {item}")
    lines.append(f"\\end{{{env}}}")

    return "\n".join(lines)


def _flush_paragraphs(
    text_buffer: list[str], parent_id: NodeId, db: GraphDB,
) -> None:
    """Split accumulated text on double-newlines and create Paragraph nodes."""
    if not text_buffer:
        return

    full_text = "".join(text_buffer)
    # Split on double newlines (paragraph breaks)
    raw_paragraphs = re.split(r"\n\n+", full_text)

    for raw in raw_paragraphs:
        cleaned = raw.strip()
        if not cleaned:
            continue
        # Collapse internal whitespace runs to single spaces
        cleaned = re.sub(r"\s+", " ", cleaned)
        para = Paragraph(
            meta=make_node_metadata(NodeType.PARAGRAPH),
            text=cleaned,
        )
        nid = db.create_node(para)
        db.create_edge(_contains(parent_id, nid))

    text_buffer.clear()


def _normalize_latex(text: str, *, strip_preamble: bool = False) -> list[str]:
    """Normalize LaTeX for comparison: strip comments, collapse whitespace.

    If strip_preamble is True, remove everything before \\begin{document} and
    the \\end{document} line, as well as \\documentclass, \\usepackage, \\title,
    \\maketitle lines.
    """
    lines: list[str] = []
    for line in text.splitlines():
        # Strip comments (but not escaped %)
        stripped = re.sub(r"(?<!\\)%.*$", "", line)
        stripped = stripped.rstrip()
        lines.append(stripped)

    if strip_preamble:
        # Remove preamble lines and structural commands
        filtered: list[str] = []
        in_body = False
        for line in lines:
            if r"\begin{document}" in line:
                in_body = True
                continue
            if r"\end{document}" in line:
                continue
            if not in_body:
                continue
            # Skip structural commands that aren't content
            if re.match(r"\\(documentclass|usepackage|title|maketitle)\b", line):
                continue
            # Normalize inline math delimiters: \(...\) → $...$
            line = re.sub(r"\\\((.+?)\\\)", r"$\1$", line)
            filtered.append(line)
        lines = filtered

    # Collapse multiple blank lines
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

    # Strip leading/trailing blank lines
    while normalized and normalized[0] == "":
        normalized.pop(0)
    while normalized and normalized[-1] == "":
        normalized.pop()

    return normalized
