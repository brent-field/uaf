"""Ground-truth layout fidelity tests against real-world PDFs.

Imports a reference PDF, extracts layout metadata via PdfHandler, and asserts
specific geometric/typographic properties against values measured in Mac Preview.
"""

from __future__ import annotations

import pytest

from tests.uaf.app._pdf_fidelity_helpers import _find_block, _import_pdf
from uaf.core.nodes import Artifact, Paragraph


class TestPdfFidelity2511:
    """Ground-truth layout tests against 2511.14823v1.pdf (page 1)."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        db, root_id, children = _import_pdf("2511.14823v1.pdf")
        self.db = db
        self.root_id = root_id
        self.children = children
        art = db.get_node(root_id)
        assert isinstance(art, Artifact)
        self.artifact = art
        # Filter to page 0 children only.
        self.page0 = [
            c for c in children
            if hasattr(c, "meta") and c.meta.layout and c.meta.layout.page == 0
        ]

    # -- Geometry tests --

    def test_page_dimensions(self) -> None:
        """Artifact LayoutHint records page as 612x792 pt (US Letter)."""
        layout = self.artifact.meta.layout
        assert layout is not None
        assert layout.width == pytest.approx(612.0, abs=1.0)
        assert layout.height == pytest.approx(792.0, abs=1.0)

    def test_title_position(self) -> None:
        """Title block at approx x=72, y=98 with width ~467."""
        title = _find_block(self.page0, "DYNAMIC NESTED")
        layout = title.meta.layout
        assert layout is not None
        assert layout.x == pytest.approx(72.4, abs=2.0)
        assert layout.y == pytest.approx(97.7, abs=2.0)
        assert layout.width == pytest.approx(467.2, abs=5.0)

    def test_title_not_spaced_letters(self) -> None:
        """Title text is readable, not 'D YNAMIC  N ESTED'."""
        title = _find_block(self.page0, "DYNAMIC")
        text: str = title.text
        # Should not have letter-spacing artefacts.
        assert "D YNAMIC" not in text
        assert "N ESTED" not in text
        # Should contain the full title words.
        assert "DYNAMIC" in text
        assert "NESTED" in text
        assert "HIERARCHIES" in text

    def test_author_blocks_count(self) -> None:
        """Three separate author blocks exist."""
        authors = [
            c for c in self.page0
            if isinstance(c, Paragraph)
            and c.meta.layout is not None
            and c.meta.layout.y == pytest.approx(204.6, abs=2.0)
        ]
        assert len(authors) == 3

    def test_author_blocks_horizontally_spaced(self) -> None:
        """Author blocks are at distinct x positions spanning the page."""
        authors = [
            c for c in self.page0
            if isinstance(c, Paragraph)
            and c.meta.layout is not None
            and c.meta.layout.y == pytest.approx(204.6, abs=2.0)
        ]
        xs = sorted(
            c.meta.layout.x for c in authors
            if c.meta.layout and c.meta.layout.x
        )
        assert len(xs) == 3
        # Authors span from ~100 to ~405 — significant horizontal spread.
        assert xs[0] == pytest.approx(100.6, abs=5.0)
        assert xs[1] == pytest.approx(260.7, abs=5.0)
        assert xs[2] == pytest.approx(405.1, abs=5.0)

    def test_date_centered(self) -> None:
        """Date block at x ~ 266, width ~ 79."""
        date = _find_block(self.page0, "November 20, 2025")
        layout = date.meta.layout
        assert layout is not None
        assert layout.x == pytest.approx(266.4, abs=3.0)
        assert layout.width == pytest.approx(79.1, abs=5.0)

    def test_abstract_indented(self) -> None:
        """Abstract body x ~ 108 (indented vs body text at x ~ 72)."""
        abstract = _find_block(self.page0, "Contemporary machine learning")
        layout = abstract.meta.layout
        assert layout is not None
        assert layout.x == pytest.approx(108.0, abs=3.0)
        # Compare to body text which is at ~72.
        body = _find_block(self.page0, "Advancements in deep learning")
        body_layout = body.meta.layout
        assert body_layout is not None
        assert layout.x > body_layout.x + 20  # type: ignore[operator]

    def test_body_text_full_width(self) -> None:
        """Body paragraphs span ~468pt (margin to margin)."""
        body = _find_block(self.page0, "Advancements in deep learning")
        layout = body.meta.layout
        assert layout is not None
        assert layout.width == pytest.approx(468.0, abs=5.0)

    def test_sidebar_rotation(self) -> None:
        """arXiv sidebar has rotation ~ -90 degrees."""
        sidebar = _find_block(self.page0, "arXiv:2511")
        layout = sidebar.meta.layout
        assert layout is not None
        assert layout.rotation is not None
        assert layout.rotation == pytest.approx(-90.0, abs=1.0)

    def test_sidebar_width_is_run_length(self) -> None:
        """Rotated sidebar width = text run length (~352pt), not 26.7."""
        sidebar = _find_block(self.page0, "arXiv:2511")
        layout = sidebar.meta.layout
        assert layout is not None
        assert layout.width is not None
        # Run length should be ~352pt (bbox height), not ~27pt (bbox width).
        assert layout.width == pytest.approx(352.0, abs=10.0)
        assert layout.width > 100.0  # definitely not the narrow dimension

    def test_sidebar_y_position(self) -> None:
        """Sidebar y ~ 220 (top of the text run in the PDF)."""
        sidebar = _find_block(self.page0, "arXiv:2511")
        layout = sidebar.meta.layout
        assert layout is not None
        assert layout.y == pytest.approx(220.0, abs=5.0)

    # -- Typography tests --

    def test_title_font_family_maps_to_times(self) -> None:
        """Title font family contains 'Times New Roman'."""
        title = _find_block(self.page0, "DYNAMIC NESTED")
        layout = title.meta.layout
        assert layout is not None
        assert layout.font_family is not None
        assert "Times New Roman" in layout.font_family

    @pytest.mark.xfail(
        reason="Small-caps: dominant font size is 13.8pt (majority chars), not 17.2pt",
    )
    def test_title_font_size(self) -> None:
        """Title font size ~ 17.2pt."""
        title = _find_block(self.page0, "DYNAMIC NESTED")
        layout = title.meta.layout
        assert layout is not None
        # Ground truth from PDF: 17.2pt for the large caps.
        # Actual: 13.8pt — character-weighted voting picks the smaller size
        # because small-caps splits produce more chars at the smaller size.
        assert layout.font_size == pytest.approx(17.2, abs=1.0)

    @pytest.mark.xfail(
        reason=(
            "Author blocks: only first line (name) is bold; affiliation lines are"
            " normal weight. Dominant-font voting yields weight=None."
            " Actual: font_weight=None, first_line_weight='bold'"
        ),
    )
    def test_author_blocks_bold(self) -> None:
        """Author blocks have font_weight='bold' (entire block is bold)."""
        authors = [
            c for c in self.page0
            if isinstance(c, Paragraph)
            and c.meta.layout is not None
            and c.meta.layout.y == pytest.approx(204.6, abs=2.0)
        ]
        for author in authors:
            layout = author.meta.layout
            assert layout is not None
            assert layout.font_weight == "bold"

    def test_body_font_size(self) -> None:
        """Body text font size ~ 10pt."""
        body = _find_block(self.page0, "Advancements in deep learning")
        layout = body.meta.layout
        assert layout is not None
        assert layout.font_size == pytest.approx(10.0, abs=0.5)

    def test_abstract_heading_bold(self) -> None:
        """'ABSTRACT' heading is bold (font_weight='bold')."""
        abstract_heading = _find_block(self.page0, "ABSTRACT")
        layout = abstract_heading.meta.layout
        assert layout is not None
        assert layout.font_weight == "bold"

    @pytest.mark.xfail(
        reason=(
            "Keywords: only the dot separators (CMSY10 font) are italic; the"
            " actual keyword text is NimbusRomNo9L-Regu (normal). Dominant-font"
            " voting yields style=None. Actual: font_style=None"
        ),
    )
    def test_keywords_italic(self) -> None:
        """Keywords block has font_style='italic'."""
        kw = _find_block(self.page0, "Keywords")
        layout = kw.meta.layout
        assert layout is not None
        assert layout.font_style == "italic"

    @pytest.mark.xfail(
        reason=(
            "Keywords: only 'Keywords' prefix (8 chars) is bold; the keyword"
            " text (~170 chars) is normal. Dominant-font voting yields"
            " weight=None. Actual: font_weight=None"
        ),
    )
    def test_keywords_bold(self) -> None:
        """Keywords block has font_weight='bold' (MediItal = bold+italic)."""
        kw = _find_block(self.page0, "Keywords")
        layout = kw.meta.layout
        assert layout is not None
        assert layout.font_weight == "bold"

    def test_section_heading_bold_and_larger(self) -> None:
        """'1 Introduction' is bold, size ~ 12pt (> body at ~ 10pt)."""
        # Note: PyMuPDF splits "1" and "Introduction" onto separate lines
        # within the same block, so the text is "1\nIntroduction".
        heading = _find_block(self.page0, "Introduction")
        layout = heading.meta.layout
        assert layout is not None
        assert layout.font_weight == "bold"
        assert layout.font_size == pytest.approx(12.0, abs=0.5)
        # Should be larger than body text (~10pt).
        body = _find_block(self.page0, "Advancements in deep learning")
        body_layout = body.meta.layout
        assert body_layout is not None
        assert layout.font_size > body_layout.font_size  # type: ignore[operator]

    # -- Text content tests --

    def test_dehyphenation(self) -> None:
        """No end-of-line hyphens splitting words remain in text."""
        abstract = _find_block(self.page0, "Contemporary machine learning")
        # Should not contain line-end hyphenation artefacts.
        assert "capa-\n" not in abstract.text
        assert "mod-\n" not in abstract.text

        body = _find_block(self.page0, "Advancements in deep learning")
        lines = body.text.split("\n")
        for line in lines[:-1]:  # skip last line
            # No line should end with a letter-hyphen pattern.
            stripped = line.rstrip()
            if (
                stripped
                and stripped[-1] == "-"
                and len(stripped) >= 2
                and stripped[-2].isalpha()
            ):
                pytest.fail(f"Residual hyphenation found: {stripped!r}")

    def test_no_double_spaces(self) -> None:
        """No paragraph text contains '  ' (double spaces)."""
        for child in self.page0:
            text = getattr(child, "text", "") or getattr(child, "source", "")
            assert "  " not in text, f"Double space found in: {text[:60]!r}"

    def test_date_text(self) -> None:
        """Date block text is 'November 20, 2025'."""
        date = _find_block(self.page0, "November")
        assert date.text.strip() == "November 20, 2025"

    def test_arxiv_sidebar_text(self) -> None:
        """Sidebar contains 'arXiv:2511.14823v1'."""
        sidebar = _find_block(self.page0, "arXiv:2511")
        assert "arXiv:2511.14823v1" in sidebar.text

    # -- Block count test --

    def test_page1_block_count(self) -> None:
        """Page 1 has approximately 12 text blocks (+/-1)."""
        assert len(self.page0) == pytest.approx(12, abs=1)
