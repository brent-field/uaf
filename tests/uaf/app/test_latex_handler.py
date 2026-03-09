"""Tests for the LaTeX format handler — import, export, comparator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from uaf.core.nodes import (
    Artifact,
    CodeBlock,
    Heading,
    MathBlock,
    Paragraph,
    TextBlock,
)
from uaf.db.graph_db import GraphDB

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# TestLatexGraphStructure
# ---------------------------------------------------------------------------


class TestLatexGraphStructure:
    """Verify the graph structure produced by LaTeX import."""

    def test_title_becomes_artifact_title(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        tex = tmp_path / "doc.tex"
        tex.write_text(
            r"\documentclass{article}" "\n"
            r"\title{My Title}" "\n"
            r"\begin{document}" "\n"
            r"\maketitle" "\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        db = GraphDB()
        root_id = LatexHandler().import_file(tex, db)
        art = db.get_node(root_id)
        assert isinstance(art, Artifact)
        assert art.title == "My Title"

    def test_section_becomes_heading(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        tex = tmp_path / "doc.tex"
        tex.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            r"\section{Intro}" "\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        db = GraphDB()
        root_id = LatexHandler().import_file(tex, db)
        children = db.get_children(root_id)
        assert len(children) == 1
        assert isinstance(children[0], Heading)
        assert children[0].text == "Intro"
        assert children[0].level == 1

    def test_subsection_levels(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        tex = tmp_path / "doc.tex"
        tex.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            r"\section{S1}" "\n"
            r"\subsection{S2}" "\n"
            r"\subsubsection{S3}" "\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        db = GraphDB()
        root_id = LatexHandler().import_file(tex, db)
        children = db.get_children(root_id)
        assert len(children) == 3
        assert children[0].level == 1
        assert children[1].level == 2
        assert children[2].level == 3

    def test_paragraphs_imported(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        tex = tmp_path / "doc.tex"
        tex.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            "First paragraph.\n\nSecond paragraph.\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        db = GraphDB()
        root_id = LatexHandler().import_file(tex, db)
        children = db.get_children(root_id)
        paras = [c for c in children if isinstance(c, Paragraph)]
        assert len(paras) == 2
        assert "First paragraph" in paras[0].text
        assert "Second paragraph" in paras[1].text

    def test_display_equation_becomes_math_block(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        tex = tmp_path / "doc.tex"
        tex.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            r"\begin{equation}" "\n"
            r"E = mc^2" "\n"
            r"\end{equation}" "\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        db = GraphDB()
        root_id = LatexHandler().import_file(tex, db)
        children = db.get_children(root_id)
        math_blocks = [c for c in children if isinstance(c, MathBlock)]
        assert len(math_blocks) == 1
        assert "E = mc^2" in math_blocks[0].source
        assert math_blocks[0].display == "block"

    def test_inline_math_in_paragraph(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        tex = tmp_path / "doc.tex"
        tex.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            r"The formula $x^2$ is simple." "\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        db = GraphDB()
        root_id = LatexHandler().import_file(tex, db)
        children = db.get_children(root_id)
        # Inline math is kept within the paragraph text
        paras = [c for c in children if isinstance(c, Paragraph)]
        assert len(paras) == 1
        assert "$x^2$" in paras[0].text or "x^2" in paras[0].text

    def test_verbatim_becomes_code_block(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        tex = tmp_path / "doc.tex"
        tex.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            r"\begin{verbatim}" "\n"
            "hello()\n"
            r"\end{verbatim}" "\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        db = GraphDB()
        root_id = LatexHandler().import_file(tex, db)
        children = db.get_children(root_id)
        code_blocks = [c for c in children if isinstance(c, CodeBlock)]
        assert len(code_blocks) == 1
        assert "hello()" in code_blocks[0].source

    def test_lstlisting_becomes_code_block(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        tex = tmp_path / "doc.tex"
        tex.write_text(
            r"\documentclass{article}" "\n"
            r"\usepackage{listings}" "\n"
            r"\begin{document}" "\n"
            r"\begin{lstlisting}[language=Python]" "\n"
            "x = 1\n"
            r"\end{lstlisting}" "\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        db = GraphDB()
        root_id = LatexHandler().import_file(tex, db)
        children = db.get_children(root_id)
        code_blocks = [c for c in children if isinstance(c, CodeBlock)]
        assert len(code_blocks) == 1
        assert "x = 1" in code_blocks[0].source

    def test_itemize_becomes_text_block(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        tex = tmp_path / "doc.tex"
        tex.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            r"\begin{itemize}" "\n"
            r"\item First" "\n"
            r"\item Second" "\n"
            r"\end{itemize}" "\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        db = GraphDB()
        root_id = LatexHandler().import_file(tex, db)
        children = db.get_children(root_id)
        tbs = [c for c in children if isinstance(c, TextBlock)]
        assert len(tbs) == 1
        assert "First" in tbs[0].text
        assert "Second" in tbs[0].text
        assert tbs[0].format == "latex"

    def test_comments_stripped(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        tex = tmp_path / "doc.tex"
        tex.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            "% This is a comment\n"
            "Real paragraph.\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        db = GraphDB()
        root_id = LatexHandler().import_file(tex, db)
        children = db.get_children(root_id)
        paras = [c for c in children if isinstance(c, Paragraph)]
        assert len(paras) >= 1
        # Comment text should not appear in any node
        for child in children:
            if hasattr(child, "text"):
                assert "This is a comment" not in child.text

    def test_no_title_uses_filename(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        tex = tmp_path / "myfile.tex"
        tex.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            "Hello.\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        db = GraphDB()
        root_id = LatexHandler().import_file(tex, db)
        art = db.get_node(root_id)
        assert isinstance(art, Artifact)
        assert art.title == "myfile"


# ---------------------------------------------------------------------------
# TestLatexExport
# ---------------------------------------------------------------------------


class TestLatexExport:
    """Verify LaTeX export produces valid output."""

    def test_document_structure(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        handler = LatexHandler()
        db = GraphDB()

        tex_in = tmp_path / "in.tex"
        tex_in.write_text(
            r"\documentclass{article}" "\n"
            r"\title{Export Test}" "\n"
            r"\begin{document}" "\n"
            r"\maketitle" "\n"
            r"\section{Hello}" "\n"
            "World.\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        root_id = handler.import_file(tex_in, db)

        tex_out = tmp_path / "out.tex"
        handler.export_file(db, root_id, tex_out)
        content = tex_out.read_text(encoding="utf-8")

        assert r"\documentclass{article}" in content
        assert r"\begin{document}" in content
        assert r"\end{document}" in content

    def test_heading_export(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        handler = LatexHandler()
        db = GraphDB()

        tex_in = tmp_path / "in.tex"
        tex_in.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            r"\section{Section One}" "\n"
            r"\subsection{Sub One}" "\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        root_id = handler.import_file(tex_in, db)

        tex_out = tmp_path / "out.tex"
        handler.export_file(db, root_id, tex_out)
        content = tex_out.read_text(encoding="utf-8")

        assert r"\section{Section One}" in content
        assert r"\subsection{Sub One}" in content

    def test_math_export(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        handler = LatexHandler()
        db = GraphDB()

        tex_in = tmp_path / "in.tex"
        tex_in.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            r"\begin{equation}" "\n"
            r"a + b = c" "\n"
            r"\end{equation}" "\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        root_id = handler.import_file(tex_in, db)

        tex_out = tmp_path / "out.tex"
        handler.export_file(db, root_id, tex_out)
        content = tex_out.read_text(encoding="utf-8")

        assert r"\begin{equation}" in content
        assert "a + b = c" in content

    def test_code_export(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        handler = LatexHandler()
        db = GraphDB()

        tex_in = tmp_path / "in.tex"
        tex_in.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            r"\begin{verbatim}" "\n"
            "x = 1\n"
            r"\end{verbatim}" "\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        root_id = handler.import_file(tex_in, db)

        tex_out = tmp_path / "out.tex"
        handler.export_file(db, root_id, tex_out)
        content = tex_out.read_text(encoding="utf-8")

        assert r"\begin{verbatim}" in content or r"\begin{lstlisting}" in content
        assert "x = 1" in content

    def test_paragraph_separation(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexHandler

        handler = LatexHandler()
        db = GraphDB()

        tex_in = tmp_path / "in.tex"
        tex_in.write_text(
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            "First paragraph.\n\nSecond paragraph.\n"
            r"\end{document}" "\n",
            encoding="utf-8",
        )
        root_id = handler.import_file(tex_in, db)

        tex_out = tmp_path / "out.tex"
        handler.export_file(db, root_id, tex_out)
        content = tex_out.read_text(encoding="utf-8")

        assert "First paragraph" in content
        assert "Second paragraph" in content


# ---------------------------------------------------------------------------
# TestLatexComparator
# ---------------------------------------------------------------------------


class TestLatexComparator:
    """Verify LaTeX comparison logic."""

    def test_identical_files(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexComparator

        a = tmp_path / "a.tex"
        b = tmp_path / "b.tex"
        content = (
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            "Hello.\n"
            r"\end{document}" "\n"
        )
        a.write_text(content, encoding="utf-8")
        b.write_text(content, encoding="utf-8")

        result = LatexComparator().compare(a, b)
        assert result.is_equivalent
        assert result.similarity_score == 1.0

    def test_whitespace_tolerance(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexComparator

        a = tmp_path / "a.tex"
        b = tmp_path / "b.tex"
        a.write_text(
            r"\section{Hello}" "\n\n\n" "World.\n",
            encoding="utf-8",
        )
        b.write_text(
            r"\section{Hello}" "\n\n" "World.\n",
            encoding="utf-8",
        )

        result = LatexComparator().compare(a, b)
        assert result.is_equivalent

    def test_comment_stripping(self, tmp_path: Path) -> None:
        from uaf.app.formats.latex import LatexComparator

        a = tmp_path / "a.tex"
        b = tmp_path / "b.tex"
        a.write_text(
            "% A comment\n" r"\section{Hello}" "\n",
            encoding="utf-8",
        )
        b.write_text(
            r"\section{Hello}" "\n",
            encoding="utf-8",
        )

        result = LatexComparator().compare(a, b)
        assert result.is_equivalent
