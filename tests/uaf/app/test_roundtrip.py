"""Round-trip format fidelity tests — import → UAF graph → export → compare."""

from __future__ import annotations

from pathlib import Path

import pytest

from uaf.app.formats.csv_format import CsvComparator, CsvHandler
from uaf.app.formats.latex import LatexComparator, LatexHandler
from uaf.app.formats.markdown import MarkdownComparator, MarkdownHandler
from uaf.app.formats.plaintext import PlainTextComparator, PlainTextHandler
from uaf.db.graph_db import GraphDB

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"

# ---------------------------------------------------------------------------
# Markdown fixtures
# ---------------------------------------------------------------------------

_markdown_fixtures = sorted((FIXTURES / "markdown").glob("*.md"))


@pytest.mark.parametrize(
    "fixture",
    _markdown_fixtures,
    ids=[p.stem for p in _markdown_fixtures],
)
def test_markdown_roundtrip(fixture: Path, tmp_path: Path) -> None:
    """Import markdown → UAF graph → export markdown → compare."""
    db = GraphDB()
    handler = MarkdownHandler()
    root_id = handler.import_file(fixture, db)
    output = tmp_path / fixture.name
    handler.export_file(db, root_id, output)
    result = MarkdownComparator().compare(fixture, output)
    assert result.is_equivalent, f"Differences: {result.differences}"
    assert result.similarity_score >= 0.95


# ---------------------------------------------------------------------------
# CSV fixtures
# ---------------------------------------------------------------------------

_csv_fixtures = sorted((FIXTURES / "csv").glob("*.csv"))


@pytest.mark.parametrize(
    "fixture",
    _csv_fixtures,
    ids=[p.stem for p in _csv_fixtures],
)
def test_csv_roundtrip(fixture: Path, tmp_path: Path) -> None:
    """Import CSV → UAF graph → export CSV → compare."""
    db = GraphDB()
    handler = CsvHandler()
    root_id = handler.import_file(fixture, db)
    output = tmp_path / fixture.name
    handler.export_file(db, root_id, output)
    result = CsvComparator().compare(fixture, output)
    assert result.is_equivalent, f"Differences: {result.differences}"
    assert result.similarity_score >= 0.95


# ---------------------------------------------------------------------------
# Plain text fixtures
# ---------------------------------------------------------------------------

_txt_fixtures = sorted((FIXTURES / "txt").glob("*.txt"))


@pytest.mark.parametrize(
    "fixture",
    _txt_fixtures,
    ids=[p.stem for p in _txt_fixtures],
)
def test_plaintext_roundtrip(fixture: Path, tmp_path: Path) -> None:
    """Import plain text → UAF graph → export plain text → compare."""
    db = GraphDB()
    handler = PlainTextHandler()
    root_id = handler.import_file(fixture, db)
    output = tmp_path / fixture.name
    handler.export_file(db, root_id, output)
    result = PlainTextComparator().compare(fixture, output)
    assert result.is_equivalent, f"Differences: {result.differences}"
    assert result.similarity_score >= 0.95


# ---------------------------------------------------------------------------
# LaTeX fixtures
# ---------------------------------------------------------------------------

_latex_fixtures = sorted((FIXTURES / "latex").glob("*.tex"))


@pytest.mark.parametrize(
    "fixture",
    _latex_fixtures,
    ids=[p.stem for p in _latex_fixtures],
)
def test_latex_roundtrip(fixture: Path, tmp_path: Path) -> None:
    """Import LaTeX → UAF graph → export LaTeX → compare."""
    db = GraphDB()
    handler = LatexHandler()
    root_id = handler.import_file(fixture, db)
    output = tmp_path / fixture.name
    handler.export_file(db, root_id, output)
    result = LatexComparator().compare(fixture, output)
    assert result.similarity_score >= 0.90, f"Differences: {result.differences}"


# ---------------------------------------------------------------------------
# Graph structure verification tests
# ---------------------------------------------------------------------------


class TestMarkdownGraphStructure:
    """Verify the graph structure produced by Markdown import."""

    def test_heading_levels_preserved(self, tmp_path: Path) -> None:
        md_file = tmp_path / "headings.md"
        md_file.write_text("# H1\n\n## H2\n\n### H3\n", encoding="utf-8")

        db = GraphDB()
        root_id = MarkdownHandler().import_file(md_file, db)
        children = db.get_children(root_id)

        assert len(children) == 3
        from uaf.core.nodes import Heading

        for i, expected_level in enumerate([1, 2, 3]):
            assert isinstance(children[i], Heading)
            assert children[i].level == expected_level

    def test_code_block_language(self, tmp_path: Path) -> None:
        md_file = tmp_path / "code.md"
        md_file.write_text("```python\nx = 1\n```\n", encoding="utf-8")

        db = GraphDB()
        root_id = MarkdownHandler().import_file(md_file, db)
        children = db.get_children(root_id)

        assert len(children) == 1
        from uaf.core.nodes import CodeBlock

        assert isinstance(children[0], CodeBlock)
        assert children[0].language == "python"
        assert children[0].source == "x = 1"

    def test_inline_formatting_preserved(self, tmp_path: Path) -> None:
        md_file = tmp_path / "inline.md"
        md_file.write_text("Text with **bold** and *italic*.\n", encoding="utf-8")

        db = GraphDB()
        root_id = MarkdownHandler().import_file(md_file, db)
        children = db.get_children(root_id)

        assert len(children) == 1
        from uaf.core.nodes import Paragraph

        assert isinstance(children[0], Paragraph)
        assert "**bold**" in children[0].text
        assert "*italic*" in children[0].text

    def test_list_stored_as_text_block(self, tmp_path: Path) -> None:
        md_file = tmp_path / "list.md"
        md_file.write_text("- item one\n- item two\n", encoding="utf-8")

        db = GraphDB()
        root_id = MarkdownHandler().import_file(md_file, db)
        children = db.get_children(root_id)

        assert len(children) == 1
        from uaf.core.nodes import TextBlock

        assert isinstance(children[0], TextBlock)
        assert children[0].format == "markdown"
        assert "item one" in children[0].text


class TestCsvGraphStructure:
    """Verify the graph structure produced by CSV import."""

    def test_sheet_dimensions(self) -> None:
        db = GraphDB()
        root_id = CsvHandler().import_file(FIXTURES / "csv" / "simple.csv", db)
        children = db.get_children(root_id)

        assert len(children) == 1
        from uaf.core.nodes import Sheet

        sheet = children[0]
        assert isinstance(sheet, Sheet)
        assert sheet.rows == 4
        assert sheet.cols == 3

    def test_cell_count(self) -> None:
        db = GraphDB()
        root_id = CsvHandler().import_file(FIXTURES / "csv" / "simple.csv", db)
        children = db.get_children(root_id)
        sheet = children[0]
        cells = db.get_children(sheet.meta.id)
        assert len(cells) == 12  # 4 rows x 3 cols

    def test_large_csv_performance(self) -> None:
        """Ensure the large CSV fixture imports without issues."""
        db = GraphDB()
        root_id = CsvHandler().import_file(FIXTURES / "csv" / "large.csv", db)
        children = db.get_children(root_id)
        sheet = children[0]
        from uaf.core.nodes import Sheet

        assert isinstance(sheet, Sheet)
        assert sheet.rows == 1001  # header + 1000 data rows


class TestPlainTextGraphStructure:
    """Verify the graph structure produced by plain text import."""

    def test_paragraph_count(self) -> None:
        db = GraphDB()
        root_id = PlainTextHandler().import_file(FIXTURES / "txt" / "multiline.txt", db)
        children = db.get_children(root_id)
        # 3 paragraphs separated by blank lines
        assert len(children) == 3

    def test_paragraph_content(self) -> None:
        db = GraphDB()
        root_id = PlainTextHandler().import_file(FIXTURES / "txt" / "simple.txt", db)
        children = db.get_children(root_id)
        # simple.txt has no blank lines, so it's one paragraph
        assert len(children) == 1
        from uaf.core.nodes import Paragraph

        assert isinstance(children[0], Paragraph)
        assert "Hello, world!" in children[0].text


# ---------------------------------------------------------------------------
# Comparator edge cases
# ---------------------------------------------------------------------------


class TestComparators:
    def test_markdown_comparator_identical(self, tmp_path: Path) -> None:
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("# Hello\n\nWorld.\n", encoding="utf-8")
        b.write_text("# Hello\n\nWorld.\n", encoding="utf-8")
        result = MarkdownComparator().compare(a, b)
        assert result.is_equivalent
        assert result.similarity_score == 1.0

    def test_csv_comparator_identical(self, tmp_path: Path) -> None:
        a = tmp_path / "a.csv"
        b = tmp_path / "b.csv"
        a.write_text("a,b\n1,2\n", encoding="utf-8")
        b.write_text("a,b\n1,2\n", encoding="utf-8")
        result = CsvComparator().compare(a, b)
        assert result.is_equivalent
        assert result.similarity_score == 1.0

    def test_plaintext_comparator_trailing_whitespace(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("Hello  \nWorld\n", encoding="utf-8")
        b.write_text("Hello\nWorld\n", encoding="utf-8")
        result = PlainTextComparator().compare(a, b)
        assert result.is_equivalent
        assert "trailing whitespace" in result.ignored[0]
