"""Ground-truth layout fidelity tests against real-world PDFs.

Imports a reference PDF, extracts layout metadata via PdfHandler, and asserts
specific geometric/typographic properties against values measured in Mac Preview.

The TestPdfRenderedLayout class tests the *rendered HTML* output of
DocLens.render_layout() — verifying that CSS font properties survive into
valid HTML attributes and that the layout view faithfully reproduces the
original PDF's visual appearance.

The TestPdfLineBreakFidelity class tests that the layout view preserves the
exact line breaks from the original PDF, including end-of-line hyphenation.
"""

from __future__ import annotations

import re
from typing import Any

import fitz
import pytest

from tests.uaf.app._pdf_fidelity_helpers import _find_block, _import_pdf
from uaf.core.nodes import Artifact, MathBlock, Paragraph, Shape


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
        """Sidebar y ~ 572 (bbox bottom — anchor for -90° CSS rotation).

        CSS ``rotate(-90deg)`` with ``transform-origin: top left`` swings
        the text upward from the anchor.  The y position must be at the
        *bottom* of the original bbox so the rotated text fills the correct
        vertical span (approx 220-572 pt) on the page.
        """
        sidebar = _find_block(self.page0, "arXiv:2511")
        layout = sidebar.meta.layout
        assert layout is not None
        # y should be at bbox bottom (y0 + height ≈ 220 + 352 ≈ 572)
        assert layout.y == pytest.approx(572.0, abs=5.0)

    # -- Typography tests --

    def test_title_font_family_maps_to_times(self) -> None:
        """Title font family contains 'Times New Roman'."""
        title = _find_block(self.page0, "DYNAMIC NESTED")
        layout = title.meta.layout
        assert layout is not None
        assert layout.font_family is not None
        assert "Times New Roman" in layout.font_family

    @pytest.mark.xfail(
        reason=(
            "Small-caps title: dominant font is 13.8pt (the small-cap size) "
            "because it covers more characters than the 17.2pt initials."
        ),
    )
    def test_title_font_size(self) -> None:
        """Title font size ~ 17.2pt (the large initial-cap size)."""
        title = _find_block(self.page0, "DYNAMIC NESTED")
        layout = title.meta.layout
        assert layout is not None
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
        """Page 1 has approximately 14 nodes: 12 text blocks + 2 shapes."""
        assert len(self.page0) == pytest.approx(14, abs=1)


class TestPdfShapeExtraction:
    """Tests for horizontal rule / shape extraction from 2511.14823v1.pdf.

    The PDF has two thin filled rectangles (horizontal rules) on page 1:
    - Rule above the title at y ≈ 79pt (from x=72 to x=540, height ≈ 2pt)
    - Rule below the title at y ≈ 166pt (same dimensions)

    These are drawing commands (vector graphics) that should be extracted as
    Shape nodes so the Layout view matches Mac Preview / Adobe Acrobat.
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        db, root_id, children = _import_pdf("2511.14823v1.pdf")
        self.db = db
        self.root_id = root_id
        self.children = children
        self.shapes = [c for c in children if isinstance(c, Shape)]
        self.page0_shapes = [
            s for s in self.shapes
            if s.meta.layout is not None and s.meta.layout.page == 0
        ]

    def test_shapes_extracted(self) -> None:
        """PDF import must produce Shape nodes for the horizontal rules."""
        assert len(self.page0_shapes) >= 2, (
            f"Expected at least 2 Shape nodes on page 0 (horizontal rules above "
            f"and below title), got {len(self.page0_shapes)}"
        )

    def test_shape_type_is_hrule(self) -> None:
        """Both rules are horizontal (width >> height, height < 3pt)."""
        for s in self.page0_shapes:
            assert s.shape_type == "hrule", (
                f"Expected shape_type='hrule', got {s.shape_type!r}"
            )

    def test_rule_above_title_position(self) -> None:
        """Rule above the title at y ≈ 79, x ≈ 72, width ≈ 468."""
        above = [
            s for s in self.page0_shapes
            if s.y < 100.0
        ]
        assert len(above) == 1, f"Expected 1 rule above title, got {len(above)}"
        rule = above[0]
        assert rule.x == pytest.approx(72.0, abs=2.0)
        assert rule.y == pytest.approx(79.2, abs=2.0)
        assert rule.width == pytest.approx(468.0, abs=5.0)
        assert rule.height == pytest.approx(2.0, abs=1.0)

    def test_rule_below_title_position(self) -> None:
        """Rule below the title at y ≈ 166, x ≈ 72, width ≈ 468."""
        below = [
            s for s in self.page0_shapes
            if 150.0 < s.y < 200.0
        ]
        assert len(below) == 1, f"Expected 1 rule below title, got {len(below)}"
        rule = below[0]
        assert rule.x == pytest.approx(72.0, abs=2.0)
        assert rule.y == pytest.approx(166.2, abs=2.0)
        assert rule.width == pytest.approx(468.0, abs=5.0)
        assert rule.height == pytest.approx(2.0, abs=1.0)

    def test_shapes_have_layout_hint(self) -> None:
        """Shape nodes must have LayoutHint with page, x, y, width, height."""
        for s in self.page0_shapes:
            layout = s.meta.layout
            assert layout is not None, "Shape node missing LayoutHint"
            assert layout.page == 0
            assert layout.x is not None
            assert layout.y is not None
            assert layout.width is not None
            assert layout.height is not None

    def test_shapes_have_fill_color(self) -> None:
        """Shape LayoutHint records fill color (black = #000000)."""
        for s in self.page0_shapes:
            layout = s.meta.layout
            assert layout is not None
            assert layout.color is not None, "Shape should have color in LayoutHint"


class TestPdfShapeRendering:
    """Tests that Shape nodes render correctly in the Layout view HTML."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        from uaf.app.lenses.doc_lens import DocLens
        from uaf.security.auth import LocalAuthProvider
        from uaf.security.secure_graph_db import SecureGraphDB

        db, root_id, _children = _import_pdf("2511.14823v1.pdf")
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        session = sdb.system_session()
        lens = DocLens()
        self.view = lens.render_layout(sdb, session, root_id)
        self.html = self.view.content

    def test_layout_html_contains_shape_elements(self) -> None:
        """Layout view HTML must contain div elements for shapes."""
        assert "layout-shape" in self.html, (
            "No shape elements found in layout HTML — horizontal rules are missing"
        )

    def test_shape_elements_have_position(self) -> None:
        """Shape divs must be absolutely positioned."""
        shape_pattern = re.compile(
            r'class="layout-shape[^"]*"\s+style="([^"]*)"',
        )
        matches = list(shape_pattern.finditer(self.html))
        assert len(matches) >= 2, (
            f"Expected at least 2 shape elements, found {len(matches)}"
        )
        for m in matches:
            style = m.group(1)
            assert "position: absolute" in style
            assert "left:" in style
            assert "top:" in style

    def test_shape_above_title_precedes_title_in_layout(self) -> None:
        """The horizontal rule above the title should appear in the HTML."""
        # Rule above title is at y ≈ 79pt, title is at y ≈ 98pt
        shape_pattern = re.compile(
            r'class="layout-shape[^"]*"\s+style="([^"]*)"',
        )
        matches = list(shape_pattern.finditer(self.html))
        above_title = [
            m for m in matches
            if "top: 79" in m.group(1) or "top: 80" in m.group(1)
        ]
        assert len(above_title) >= 1, (
            "No shape element found near y=79pt (above-title rule)"
        )


class TestPdfRenderedLayout:
    """End-to-end tests: PDF import → DocLens.render_layout() → valid HTML.

    These test the *rendered HTML output*, not just the extracted LayoutHint
    metadata. They verify that CSS properties (font-family, font-size, etc.)
    survive into valid HTML style attributes and produce a visually faithful
    layout view.
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        from uaf.app.lenses.doc_lens import DocLens
        from uaf.security.auth import LocalAuthProvider
        from uaf.security.secure_graph_db import SecureGraphDB

        db, root_id, _children = _import_pdf("2511.14823v1.pdf")
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        session = sdb.system_session()
        lens = DocLens()
        self.view = lens.render_layout(sdb, session, root_id)
        self.html = self.view.content

    def _extract_block_styles(self) -> list[dict[str, str]]:
        """Parse all layout-block divs and extract their style attribute values."""
        # Match: class="layout-block" ... style="..."
        # The style attr must be a single unbroken quoted string.
        pattern = re.compile(
            r'class="layout-block[^"]*"\s+style="([^"]*)"',
        )
        results = []
        for m in pattern.finditer(self.html):
            style_str = m.group(1)
            # Parse individual CSS properties.
            props: dict[str, str] = {}
            for part in style_str.split(";"):
                part = part.strip()
                if ":" in part:
                    key, val = part.split(":", 1)
                    props[key.strip()] = val.strip()
            results.append(props)
        return results

    # -- Style attribute validity tests --

    def test_style_attributes_not_truncated_by_quotes(self) -> None:
        """Font-family with quotes must not break the style attribute.

        The font map produces values like '"Times New Roman", Times, serif'.
        If these double-quotes are not escaped or converted, they prematurely
        close the style="..." attribute, truncating font-size and all
        subsequent CSS properties.
        """
        blocks = self._extract_block_styles()
        assert len(blocks) > 0, "No layout-block divs found in rendered HTML"

        for i, props in enumerate(blocks):
            # Every block that has position:absolute should also have left/top
            # AND font-size (since every PDF block has a font size).
            if "position" in props and props["position"] == "absolute":
                assert "left" in props, f"Block {i}: 'left' missing from style"
                assert "top" in props, f"Block {i}: 'top' missing from style"
                assert "font-size" in props, (
                    f"Block {i}: 'font-size' missing — style attribute likely "
                    f"truncated by unescaped quotes in font-family. "
                    f"Got properties: {list(props.keys())}"
                )

    def test_font_family_and_font_size_coexist(self) -> None:
        """Blocks with font-family must also retain font-size in the same style."""
        blocks = self._extract_block_styles()
        blocks_with_family = [b for b in blocks if "font-family" in b]
        assert len(blocks_with_family) > 0, "No blocks have font-family"

        for i, props in enumerate(blocks_with_family):
            assert "font-size" in props, (
                f"Block {i}: has font-family={props['font-family']!r} but "
                f"font-size is missing. The style attribute was likely "
                f"truncated by unescaped double-quotes in font-family."
            )

    def test_title_renders_with_font_size(self) -> None:
        """The title block must have a visible font-size in the rendered HTML."""
        block_pattern = re.compile(
            r'class="layout-block[^"]*"\s+style="([^"]*)">'
            r".*?YNAMIC",
            re.DOTALL,
        )
        m = block_pattern.search(self.html)
        assert m is not None, "Title block not found in rendered HTML"
        style = m.group(1)
        assert "font-size" in style, (
            f"Title block is missing font-size in its style attribute. "
            f"Style: {style!r}"
        )

    def test_section_heading_renders_bold(self) -> None:
        """The '1 Introduction' heading must render with font-weight: bold."""
        # After same-baseline merging, "1" and "Introduction" are on one line.
        heading_pattern = re.compile(
            r'class="layout-block[^"]*"\s+style="([^"]*)">'
            r"1 Introduction",
        )
        m = heading_pattern.search(self.html)
        assert m is not None, "Introduction heading block not found in rendered HTML"
        style = m.group(1)
        assert "font-weight: bold" in style, (
            f"Section heading missing font-weight: bold. Style: {style!r}"
        )

    def test_body_text_renders_at_10pt(self) -> None:
        """Body paragraphs must render with ~10pt font-size."""
        body_pattern = re.compile(
            r'class="layout-block[^"]*"\s+style="([^"]*)">'
            r"[^<]*Advancements in deep learning",
        )
        m = body_pattern.search(self.html)
        assert m is not None, "Body text block not found in rendered HTML"
        style = m.group(1)
        # Extract font-size value.
        size_match = re.search(r"font-size:\s*([\d.]+)pt", style)
        assert size_match is not None, (
            f"Body block is missing font-size. Style: {style!r}"
        )
        size = float(size_match.group(1))
        assert size == pytest.approx(10.0, abs=0.5), (
            f"Body font-size should be ~10pt, got {size}pt"
        )

    def test_rendered_blocks_have_distinct_font_sizes(self) -> None:
        """Not all blocks should render at the same font-size.

        The PDF has title (~13-17pt), headings (~12pt), body (~10pt), and
        abstract heading (~9.6pt). If font-size is lost, all blocks default
        to the browser's default size.
        """
        blocks = self._extract_block_styles()
        sizes = set()
        for props in blocks:
            if "font-size" in props:
                size_match = re.search(r"([\d.]+)", props["font-size"])
                if size_match:
                    sizes.add(float(size_match.group(1)))

        assert len(sizes) >= 3, (
            f"Expected at least 3 distinct font sizes in rendered HTML "
            f"(title, heading, body), but found {len(sizes)}: {sorted(sizes)}"
        )

    def test_sidebar_rotation_renders_within_page(self) -> None:
        """Rotated arXiv sidebar must be visually positioned within the page.

        The sidebar has rotation ≈ -90° and CSS ``transform: rotate(-90deg)``
        with ``transform-origin: top left``.  After rotation the text extends
        *upward* from the CSS ``top`` position by ``width`` points.

        The visual top edge (``css_top - css_width``) must be >= 0 and the
        visual bottom edge (``css_top``) must be <= 792 (page height).

        Before the fix the anchor sat at y ~ 220, giving a visual top of
        220 - 352 = -132 -- completely off the page.  After the fix the
        anchor is at y ~ 572 (bbox bottom), so the visual range is 220-572.
        """
        sidebar_pattern = re.compile(
            r'class="layout-block[^"]*"\s+style="([^"]*)">'
            r"[^<]*arXiv:2511",
        )
        m = sidebar_pattern.search(self.html)
        assert m is not None, "Sidebar block not found in rendered HTML"
        style = m.group(1)

        top_match = re.search(r"top:\s*([\d.]+)pt", style)
        width_match = re.search(r"width:\s*([\d.]+)pt", style)
        assert top_match is not None, f"Missing 'top' in style: {style!r}"
        assert width_match is not None, f"Missing 'width' in style: {style!r}"

        css_top = float(top_match.group(1))
        css_width = float(width_match.group(1))

        # For -90° rotation with transform-origin: top left, the text
        # extends upward: visual top = css_top - css_width.
        visual_top = css_top - css_width
        assert visual_top >= 0.0, (
            f"Rotated sidebar extends above the page! "
            f"CSS top={css_top}pt, width={css_width}pt → "
            f"visual top={visual_top}pt (should be >= 0)"
        )
        assert css_top <= 792.0, (
            f"Rotated sidebar anchor below the page! "
            f"CSS top={css_top}pt (page height=792pt)"
        )

    def test_layout_blocks_use_nowrap(self) -> None:
        """Layout blocks must use white-space: nowrap to prevent double-wrapping.

        PDF line breaks are preserved via <br> tags in display_text.  These
        force line breaks regardless of the CSS white-space value.  Without
        nowrap, the browser ALSO wraps text at box boundaries when web font
        metrics differ from the PDF's embedded fonts — producing double
        line breaks (orphan words on their own lines).

        The nowrap + page-level overflow: hidden approach is correct:
        slight clipping on overflow is far less visible than systematic
        double-wrapping across every text block.
        """
        blocks = self._extract_block_styles()
        non_nowrap: list[int] = []
        for i, props in enumerate(blocks):
            if props.get("white-space") != "nowrap":
                non_nowrap.append(i)
        assert not non_nowrap, (
            f"{len(non_nowrap)} of {len(blocks)} layout blocks lack "
            f"'white-space: nowrap'.  All layout blocks need nowrap to "
            f"prevent double-wrapping from <br> tags + browser wrapping."
        )

    def test_no_raw_double_quotes_in_style_attribute(self) -> None:
        """Style attribute values must not contain unescaped double-quotes.

        Raw " inside a style="..." attribute breaks the HTML parser.
        Font-family values like '"Times New Roman"' must use single quotes
        or HTML entities instead.
        """
        # Find broken style attributes where font-family is followed by a
        # closing quote (meaning the double-quote in the font name terminated
        # the style attribute prematurely).
        # A broken style looks like: style="...font-family: "
        truncated = re.findall(
            r'style="[^"]*font-family:\s*"', self.html,
        )
        for hit in truncated:
            fam_match = re.search(r"font-family:\s*(.*)$", hit)
            if fam_match:
                fam_value = fam_match.group(1).strip().rstrip('"')
                assert len(fam_value) > 0, (
                    f"font-family value is empty/truncated: {hit!r}"
                )


# ---------------------------------------------------------------------------
# Helpers for line-break fidelity tests
# ---------------------------------------------------------------------------

_PDF_PATH = "tests/fixtures/pdf/2511.14823v1.pdf"


def _pdf_block_lines(substring: str) -> list[str]:
    """Extract visual-line texts from the PyMuPDF block containing *substring*.

    Same-baseline lines (lines sharing significant y-overlap) are merged
    with a space, matching the visual line merging applied during import.
    """
    from uaf.app.formats.pdf_format import _merge_visual_lines

    doc = fitz.open(_PDF_PATH)
    try:
        for page in doc:
            raw: dict[str, Any] = page.get_text("dict")
            for block in raw.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                lines = block.get("lines", [])
                full = "".join(
                    "".join(s.get("text", "") for s in ln.get("spans", []))
                    for ln in lines
                )
                if substring in full:
                    return _merge_visual_lines(block)
    finally:
        doc.close()
    msg = f"No PDF block containing {substring!r}"
    raise ValueError(msg)


def _html_lines_for_block(html: str, substring: str) -> list[str]:
    """Extract the text lines from the rendered layout-block containing *substring*.

    Handles two rendering modes:
    1. **Inline text** — the renderer uses ``<br>`` for line breaks.
    2. **Absolute-positioned spans** — each ``<span>`` has a ``top: Ypt``
       style.  Spans with similar y-offsets (within 4pt) are grouped into
       visual lines.
    """
    from html import unescape

    # Find the div whose inner HTML contains the substring.
    pattern = re.compile(
        r'class="layout-block[^"]*"[^>]*>(.+?)</div>',
        re.DOTALL,
    )
    for m in pattern.finditer(html):
        inner = m.group(1)

        # Check if this block uses absolute-positioned spans.
        span_pattern = re.compile(
            r'<span[^>]*style="[^"]*position:\s*absolute[^"]*top:\s*([\d.]+)pt[^"]*"[^>]*>'
            r"(.*?)</span>",
            re.DOTALL,
        )
        abs_spans = span_pattern.findall(inner)

        if abs_spans:
            # Group spans by y-offset (within 4pt tolerance to handle
            # small-caps and mixed font sizes on the same visual line).
            text_only = "".join(unescape(txt) for _, txt in abs_spans)
            if substring not in text_only:
                continue
            buckets: dict[float, list[str]] = {}
            for y_str, txt in abs_spans:
                y = float(y_str)
                # Find an existing bucket within 4pt.
                matched = False
                for key in buckets:
                    if abs(key - y) < 4.0:
                        buckets[key].append(unescape(txt))
                        matched = True
                        break
                if not matched:
                    buckets[y] = [unescape(txt)]
            return ["".join(parts) for _, parts in sorted(buckets.items())]

        # Inline text mode: strip any <span> wrappers and split on <br>.
        text = re.sub(r"<span[^>]*>", "", inner)
        text = text.replace("</span>", "")
        if substring in text:
            lines = text.split("<br>")
            return [unescape(ln) for ln in lines]

    msg = f"No layout-block containing {substring!r}"
    raise ValueError(msg)


class TestPdfSameBaselineMerging:
    """Verify that PyMuPDF lines sharing the same baseline are merged.

    PyMuPDF sometimes splits text that appears on the same visual line into
    separate "line" objects — e.g. section numbers and titles ("1" and
    "Introduction") or equation parts.  These same-baseline lines must be
    merged so the layout view matches PDF viewers (Adobe, Mac Preview) which
    render them on a single line.

    This is a *general* issue, not specific to headings — it affects section
    headings, sub-section headings, equations with equation numbers, and text
    with sub/superscripts.
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        from uaf.app.lenses.doc_lens import DocLens
        from uaf.security.auth import LocalAuthProvider
        from uaf.security.secure_graph_db import SecureGraphDB

        db, root_id, children = _import_pdf("2511.14823v1.pdf")
        self.children = children
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        session = sdb.system_session()
        lens = DocLens()
        self.view = lens.render_layout(sdb, session, root_id)
        self.html = self.view.content
        self.page0 = [
            c for c in children
            if hasattr(c, "meta") and c.meta.layout and c.meta.layout.page == 0
        ]

    def test_section_heading_single_line(self) -> None:
        """'1 Introduction' must render on a single line, not '1<br>Introduction'."""
        html_lines = _html_lines_for_block(self.html, "Introduction")
        assert len(html_lines) == 1, (
            f"Section heading should be 1 visual line, got {len(html_lines)}: "
            f"{html_lines}"
        )
        assert "1" in html_lines[0]
        assert "Introduction" in html_lines[0]

    def test_section_heading_display_text_single_line(self) -> None:
        """The stored display_text for '1 Introduction' must not contain '\\n'."""
        heading = _find_block(self.page0, "Introduction")
        layout = heading.meta.layout
        assert layout is not None
        # The display_text (or semantic text) should have "1" and "Introduction"
        # on the same line — no newline between them.
        text = layout.display_text if layout.display_text else heading.text
        assert "1" in text
        assert "Introduction" in text
        # They must be on the same line.
        for line in text.split("\n"):
            if "Introduction" in line:
                assert "1" in line, (
                    f"'1' and 'Introduction' should be on the same line but are "
                    f"split across lines: {text!r}"
                )
                break

    def test_subsection_heading_single_line(self) -> None:
        """Sub-section headings like '2.1 Limitations...' render on one line."""
        # Page 1 has "2.1" and "Limitations of Static Nested Learning" as
        # same-baseline lines in PyMuPDF — they must be merged.
        all_children = self.children
        page1 = [
            c for c in all_children
            if hasattr(c, "meta") and c.meta.layout and c.meta.layout.page == 1
        ]
        heading = _find_block(page1, "Limitations of Static")
        layout = heading.meta.layout
        assert layout is not None
        text = layout.display_text if layout.display_text else heading.text
        for line in text.split("\n"):
            if "Limitations" in line:
                assert "2.1" in line, (
                    f"'2.1' and 'Limitations' should be on the same line: {text!r}"
                )
                break


class TestPdfParagraphSpacing:
    """Verify that inter-paragraph spacing matches PDF viewers.

    Without explicit CSS ``line-height``, the browser uses its default
    (~1.2x font-size) which makes multi-line blocks taller than the PDF
    intended.  For 10pt body text the PDF uses ~10.9pt line spacing, but
    the browser default produces ~12pt — making each 8-line block ~10pt
    taller, which eats into (or overlaps) the gap with the next block.
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        from uaf.app.lenses.doc_lens import DocLens
        from uaf.security.auth import LocalAuthProvider
        from uaf.security.secure_graph_db import SecureGraphDB

        db, root_id, children = _import_pdf("2511.14823v1.pdf")
        self.children = children
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        session = sdb.system_session()
        lens = DocLens()
        self.view = lens.render_layout(sdb, session, root_id)
        self.html = self.view.content
        self.page0 = [
            c for c in children
            if hasattr(c, "meta") and c.meta.layout and c.meta.layout.page == 0
        ]

    def test_body_block_has_line_height_metadata(self) -> None:
        """Multi-line body block must have line_height in LayoutHint."""
        body = _find_block(self.page0, "Advancements in deep learning")
        layout = body.meta.layout
        assert layout is not None
        assert layout.line_height is not None, (
            "Multi-line body block should have line_height computed from PDF data"
        )
        # PDF body text has ~10.9pt line spacing (top-to-top).
        assert layout.line_height == pytest.approx(10.9, abs=0.5)

    def test_body_block_has_line_height_css(self) -> None:
        """Body paragraphs must include line-height in rendered CSS."""
        body_pattern = re.compile(
            r'class="layout-block[^"]*"\s+style="([^"]*)">'
            r"[^<]*Advancements in deep learning",
        )
        m = body_pattern.search(self.html)
        assert m is not None, "Body text block not found in rendered HTML"
        style = m.group(1)
        assert "line-height" in style, (
            f"Body block is missing line-height in CSS. Without explicit "
            f"line-height, browser default (~1.2) produces blocks taller than "
            f"the PDF, distorting inter-paragraph spacing. Style: {style!r}"
        )
        # Verify the value is close to 10.9pt.
        lh_match = re.search(r"line-height:\s*([\d.]+)pt", style)
        assert lh_match is not None, f"line-height has no pt value in: {style!r}"
        lh = float(lh_match.group(1))
        assert lh == pytest.approx(10.9, abs=0.5), (
            f"line-height should be ~10.9pt (PDF line spacing), got {lh}pt"
        )

    def test_abstract_block_has_line_height(self) -> None:
        """Abstract body (14 lines) must have line_height metadata."""
        abstract = _find_block(self.page0, "Contemporary machine learning")
        layout = abstract.meta.layout
        assert layout is not None
        assert layout.line_height is not None, (
            "Abstract block should have line_height (it has 14 lines)"
        )

    def test_single_line_block_no_line_height(self) -> None:
        """Single-line blocks (e.g. date) should not have line_height."""
        date = _find_block(self.page0, "November 20, 2025")
        layout = date.meta.layout
        assert layout is not None
        # Single-line blocks don't need line_height.
        assert layout.line_height is None


class TestPdfParagraphSpacingCss:
    """Verify that CSS does not distort inter-paragraph spacing.

    Layout blocks are absolutely positioned at exact PDF coordinates.
    Any CSS padding or margin on ``.layout-block`` expands the rendered
    box beyond the PDF bounding box, systematically shrinking the visual
    gap between consecutive blocks.

    For the reference PDF, the median inter-paragraph gap is ~6.4pt
    (~8.5px at 96 dpi).  Adding 1px top + 1px bottom padding reduces
    this gap by ~24%, making paragraphs appear more tightly packed than
    in Mac Preview or Adobe Acrobat.
    """

    def test_layout_block_css_no_padding(self) -> None:
        """The .layout-block CSS rule must not add padding."""
        from pathlib import Path

        css_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "src" / "uaf" / "app" / "static" / "style.css"
        )
        css_text = css_path.read_text()
        # Extract the .layout-block { ... } rule.
        match = re.search(r"\.layout-block\s*\{([^}]*)\}", css_text)
        assert match is not None, ".layout-block rule not found in style.css"
        rule_body = match.group(1)
        assert "padding" not in rule_body, (
            f".layout-block CSS adds padding which distorts inter-block "
            f"spacing for absolutely-positioned layout blocks.  Blocks are "
            f"positioned at exact PDF coordinates — any padding makes them "
            f"taller than the PDF bbox and shrinks the gap to the next block. "
            f"Rule: .layout-block {{{rule_body}}}"
        )

    def test_layout_block_css_no_default_line_height(self) -> None:
        """The .layout-block CSS must not set a fallback line-height.

        Individual blocks already have inline ``line-height`` CSS from the
        PDF's actual inter-line spacing.  A class-level fallback (e.g.
        ``line-height: 1.2``) would apply to single-line blocks and to
        any block where the inline value is missing, making them taller
        than the PDF intended and distorting spacing.
        """
        from pathlib import Path

        css_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "src" / "uaf" / "app" / "static" / "style.css"
        )
        css_text = css_path.read_text()
        match = re.search(r"\.layout-block\s*\{([^}]*)\}", css_text)
        assert match is not None, ".layout-block rule not found in style.css"
        rule_body = match.group(1)
        assert "line-height" not in rule_body, (
            f".layout-block CSS sets a default line-height which can "
            f"override or conflict with inline line-height values from "
            f"PDF import.  Remove it — blocks get their line-height from "
            f"inline styles. Rule: .layout-block {{{rule_body}}}"
        )


class TestPdfLineBreakFidelity:
    """Verify that the layout view preserves the PDF's exact line breaks.

    A PDF viewer (Mac Preview, Adobe Acrobat) displays text with the exact
    line breaks recorded in the PDF.  Our layout view should reproduce these
    same breaks — including end-of-line hyphenation — rather than allowing
    CSS to re-wrap the text at different positions.
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        from uaf.app.lenses.doc_lens import DocLens
        from uaf.security.auth import LocalAuthProvider
        from uaf.security.secure_graph_db import SecureGraphDB

        db, root_id, _children = _import_pdf("2511.14823v1.pdf")
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        session = sdb.system_session()
        lens = DocLens()
        self.view = lens.render_layout(sdb, session, root_id)
        self.html = self.view.content

    def test_abstract_line_count_matches_pdf(self) -> None:
        """The abstract must have the same number of lines as the PDF (14)."""
        pdf_lines = _pdf_block_lines("Contemporary machine learning")
        html_lines = _html_lines_for_block(
            self.html, "Contemporary machine learning",
        )
        assert len(html_lines) == len(pdf_lines), (
            f"Line count mismatch: PDF has {len(pdf_lines)} lines, "
            f"layout HTML has {len(html_lines)} lines.\n"
            f"PDF line 0 ends: ...{pdf_lines[0][-30:]!r}\n"
            f"HTML line 0 ends: ...{html_lines[0][-30:]!r}"
        )

    def test_abstract_preserves_hyphenation(self) -> None:
        """The abstract must show 'capa-' at end of line (not 'capabilities').

        In the PDF, 'capabilities' is hyphenated across lines 0-1 as
        'capa-' / 'bilities'.  The layout view must reproduce this so
        the text wraps at the same point as Mac Preview / Adobe Acrobat.
        """
        html_lines = _html_lines_for_block(
            self.html, "Contemporary machine learning",
        )
        # Line 0 should end with "capa-" (the hyphen is part of the display).
        assert html_lines[0].rstrip().endswith("capa-"), (
            f"First line should end with 'capa-' but ends with: "
            f"...{html_lines[0][-30:]!r}"
        )
        # Line 1 should start with "bilities".
        assert html_lines[1].lstrip().startswith("bilities"), (
            f"Second line should start with 'bilities' but starts with: "
            f"{html_lines[1][:30]!r}"
        )

    def test_abstract_line_endings_match_pdf(self) -> None:
        """Each line in the abstract should end at the same word as the PDF."""
        pdf_lines = _pdf_block_lines("Contemporary machine learning")
        html_lines = _html_lines_for_block(
            self.html, "Contemporary machine learning",
        )
        # Compare line-by-line (as many as we have).
        for i in range(min(len(pdf_lines), len(html_lines))):
            pdf_end = pdf_lines[i].rstrip()
            html_end = html_lines[i].rstrip()
            assert pdf_end == html_end, (
                f"Line {i} differs:\n"
                f"  PDF:  ...{pdf_end[-40:]!r}\n"
                f"  HTML: ...{html_end[-40:]!r}"
            )

    def test_body_text_line_count_matches_pdf(self) -> None:
        """Body text block line count must match the PDF."""
        pdf_lines = _pdf_block_lines("Advancements in deep learning")
        html_lines = _html_lines_for_block(
            self.html, "Advancements in deep learning",
        )
        assert len(html_lines) == len(pdf_lines), (
            f"Body block line count: PDF={len(pdf_lines)}, HTML={len(html_lines)}"
        )

    def test_all_page0_blocks_preserve_line_count(self) -> None:
        """Every block on page 0 must have the same visual line count as the PDF."""
        from uaf.app.formats.pdf_format import _merge_visual_lines

        doc = fitz.open(_PDF_PATH)
        page = doc[0]
        raw: dict[str, Any] = page.get_text("dict")
        mismatches: list[str] = []

        for block in raw.get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            lines = block.get("lines", [])
            if not lines:
                continue
            # Use first 20 chars as identifier.
            first_text = "".join(
                s.get("text", "") for s in lines[0].get("spans", [])
            )
            ident = first_text[:20].strip()
            if not ident:
                continue
            try:
                html_lines = _html_lines_for_block(self.html, ident)
            except ValueError:
                continue  # block might not be on page 0 or wasn't rendered
            # Compare against visual lines (after same-baseline merging).
            visual_lines = _merge_visual_lines(block)
            if len(html_lines) != len(visual_lines):
                mismatches.append(
                    f"  {ident!r}: PDF={len(visual_lines)}, HTML={len(html_lines)}"
                )
        doc.close()

        assert not mismatches, (
            f"{len(mismatches)} block(s) have wrong line count:\n"
            + "\n".join(mismatches)
        )


class TestPdfEquationFidelity:
    """Verify that math equations are classified as MathBlock with span data.

    The reference PDF (2511.14823v1.pdf) contains equations with Computer Modern
    math fonts (CMSY, CMMI, CMR, CMEX) and mixed font sizes (main text at ~10pt,
    subscripts at ~7pt, superscripts at ~5pt).  These should be extracted as
    MathBlock nodes with per-span font metadata in LayoutHint.spans.
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        db, root_id, children = _import_pdf("2511.14823v1.pdf")
        self.db = db
        self.root_id = root_id
        self.children = children
        # Page 2 (0-indexed) contains equations in section 2.3.
        self.page2 = [
            c for c in children
            if hasattr(c, "meta") and c.meta.layout and c.meta.layout.page == 2
        ]

    def test_math_blocks_exist(self) -> None:
        """At least one MathBlock node should be extracted from the PDF."""
        math_blocks = [c for c in self.children if isinstance(c, MathBlock)]
        assert len(math_blocks) > 0, (
            "No MathBlock nodes found — equations should be classified as MathBlock"
        )

    def test_equation_3_is_math_block(self) -> None:
        """The block containing equation 3 (with 'arg min') should be a MathBlock."""
        eq3 = _find_block(self.page2, "arg min")
        assert isinstance(eq3, MathBlock), (
            f"Equation 3 should be MathBlock, got {type(eq3).__name__}"
        )

    def test_equation_3_has_spans(self) -> None:
        """Equation 3 should have span data with font size variation."""
        eq3 = _find_block(self.page2, "arg min")
        layout = eq3.meta.layout
        assert layout is not None
        assert layout.spans is not None, (
            "Equation 3 should have spans with per-span font metadata"
        )
        assert len(layout.spans) > 1, (
            f"Equation 3 should have multiple spans, got {len(layout.spans)}"
        )

    def test_equation_3_has_font_size_variation(self) -> None:
        """Equation 3 spans should have at least 2 distinct font sizes."""
        eq3 = _find_block(self.page2, "arg min")
        layout = eq3.meta.layout
        assert layout is not None
        assert layout.spans is not None
        sizes = {s.font_size for s in layout.spans if s.font_size is not None}
        assert len(sizes) >= 2, (
            f"Expected at least 2 distinct font sizes in equation 3 spans, "
            f"got {sorted(sizes)}"
        )

    def test_math_block_base_font_size_not_subscript(self) -> None:
        """Math blocks should use the base (max) font size, not subscript size.

        Equations with dense subscripts/superscripts have more small-font
        characters than base-font characters.  The block-level font_size
        should reflect the base size (~10pt), not the subscript size (~7pt).
        """
        math_blocks = [
            c for c in self.page2
            if isinstance(c, MathBlock)
            and c.meta.layout
            and c.meta.layout.spans
        ]
        assert len(math_blocks) > 0, "No MathBlocks with spans on page 2"
        for mb in math_blocks:
            layout = mb.meta.layout
            assert layout is not None
            assert layout.font_size is not None
            assert layout.font_size >= 9.5, (
                f"MathBlock font_size={layout.font_size} is too small — "
                f"should be base (~10pt), not subscript (~7pt). "
                f"Source: {getattr(mb, 'source', '')[:50]}"
            )

    def test_spans_have_x_offset(self) -> None:
        """Equation 3 spans should include x_offset data for horizontal placement."""
        eq3 = _find_block(self.page2, "arg min")
        layout = eq3.meta.layout
        assert layout is not None
        assert layout.spans is not None
        x_offsets = [s.x_offset for s in layout.spans if s.x_offset is not None]
        assert len(x_offsets) > 1, (
            "Equation 3 spans should have x_offset values for absolute positioning"
        )
        # x_offsets should vary (not all the same).
        assert len(set(x_offsets)) > 1, (
            f"Expected varying x_offsets, got {x_offsets}"
        )

    def test_paragraph_inline_math_has_spans(self) -> None:
        """Paragraphs near section 2.3 with inline math should have spans.

        The paragraph starting with the update rule (containing 'm' and 'l'
        with subscripts) must preserve per-span metadata for inline math.
        """
        # Find a paragraph on page 2 that has spans (inline math).
        para_with_spans = [
            c for c in self.page2
            if isinstance(c, Paragraph)
            and c.meta.layout
            and c.meta.layout.spans
        ]
        assert len(para_with_spans) > 0, (
            "Expected at least one paragraph with inline math spans on page 2"
        )

    def test_equation_number_x_offset_near_right_margin(self) -> None:
        """The '(6)' equation number should have x_offset > 250pt (right margin)."""
        # Equation 6 contains 'θ' (or similar math symbols) near section 2.3.
        eq6_candidates = [
            c for c in self.page2
            if isinstance(c, MathBlock)
            and c.meta.layout
            and c.meta.layout.spans
        ]
        found_eq_num = False
        for mb in eq6_candidates:
            layout = mb.meta.layout
            assert layout is not None
            assert layout.spans is not None
            for span in layout.spans:
                text = span.text.strip()
                if text in ("(6)", "(5)", "(4)", "(3)"):
                    assert span.x_offset is not None
                    assert span.x_offset > 250.0, (
                        f"Equation number '{text}' x_offset={span.x_offset} "
                        f"should be > 250pt (right margin)"
                    )
                    found_eq_num = True
                    break
            if found_eq_num:
                break
        assert found_eq_num, (
            "No equation number span found with x_offset in right margin"
        )

    def test_topmost_span_near_block_top(self) -> None:
        """The topmost span in equation 6 should start near the block's top edge.

        PyMuPDF ``origin`` is the text baseline, but CSS ``top:`` positions
        from the glyph top.  When y_offset is computed from the origin the
        minimum y across spans is ~5.5pt (too far from the top).  Using the
        span bbox gives ~0pt — glyph flush with the block boundary.
        """
        eq6 = None
        for c in self.page2:
            if (
                isinstance(c, MathBlock)
                and c.meta.layout
                and c.meta.layout.spans
                and any("(6)" in s.text for s in c.meta.layout.spans)
            ):
                eq6 = c
                break
        assert eq6 is not None, "Equation 6 not found on page 2"
        layout = eq6.meta.layout
        assert layout is not None and layout.spans is not None
        min_y = min(
            s.y_offset for s in layout.spans if s.y_offset is not None
        )
        assert min_y < 2.0, (
            f"Topmost span y_offset={min_y} is too far from block top — "
            f"y_offset should be computed from glyph bbox, not baseline origin"
        )

    def test_body_text_same_line_consistent_y(self) -> None:
        """Body-text-sized spans on the same visual line should share a y_offset.

        When y_offset uses the baseline ``origin``, PyMuPDF sometimes assigns
        body text following a subscript to the subscript's line, shifting it
        down ~1.5pt.  Using the span ``bbox`` top avoids this because bbox is
        per-span and independent of PyMuPDF line grouping.
        """
        # The "where alpha controls plasticity..." paragraph is a single visual
        # line with inline math (subscripts E_{t+1}, L_t, etc.).
        para = _find_block(self.page2, "controls plasticity")
        layout = para.meta.layout
        assert layout is not None
        assert layout.spans is not None
        base_size = layout.font_size or 10.0
        # Collect y_offsets of body-text-sized spans (within 1.5pt of base).
        # Exclude whitespace-only spans whose bbox can be unreliable.
        body_y = [
            s.y_offset
            for s in layout.spans
            if s.font_size is not None
            and abs(s.font_size - base_size) < 1.5
            and s.y_offset is not None
            and s.text.strip()
        ]
        assert len(body_y) >= 5, f"Expected >=5 body-text spans, got {len(body_y)}"
        # On the first visual line, all body-text spans should align.
        # Group by proximity (within 3pt) and check the largest group.
        first_line_y = [y for y in body_y if abs(y - body_y[0]) < 3.0]
        spread = max(first_line_y) - min(first_line_y)
        assert spread < 1.0, (
            f"Body-text y_offset spread on first line is {spread:.1f}pt "
            f"(should be <1pt). Values: {first_line_y}"
        )

    def test_uniform_body_paragraph_no_spans(self) -> None:
        """A body paragraph with uniform font (no inline math) has no spans."""
        page0 = [
            c for c in self.children
            if hasattr(c, "meta") and c.meta.layout and c.meta.layout.page == 0
        ]
        body = _find_block(page0, "Advancements in deep learning")
        layout = body.meta.layout
        assert layout is not None
        assert layout.spans is None, (
            "Uniform body paragraph should not have spans"
        )


class TestSmallCapsSpanClearing:
    """Verify that small-caps text has spans cleared for uniform rendering.

    PDF small-caps renders the initial letter at a larger font size than the
    rest (e.g. "A" at 12pt + "BSTRACT" at 9.6pt, or "D" at 17pt +
    "YNAMIC" at 14pt in the title).  Per-span font-size rendering (absolute
    or inline) creates visible gaps because browser font metrics differ from
    the PDF's embedded fonts.

    The correct approach: detect small-caps and clear per-span data so the
    block renders at a uniform dominant font size — no gaps, clean text.
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        from uaf.app.lenses.doc_lens import DocLens
        from uaf.security.auth import LocalAuthProvider
        from uaf.security.secure_graph_db import SecureGraphDB

        db, root_id, children = _import_pdf("2511.14823v1.pdf")
        self.children = children
        self.page0 = [
            c for c in children
            if hasattr(c, "meta") and c.meta.layout and c.meta.layout.page == 0
        ]
        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        session = sdb.system_session()
        lens = DocLens()
        self.view = lens.render_layout(sdb, session, root_id)
        self.html = self.view.content

    def test_abstract_no_spans(self) -> None:
        """ABSTRACT must not have per-span data (uniform font-size rendering)."""
        abstract = _find_block(self.page0, "ABSTRACT")
        layout = abstract.meta.layout
        assert layout is not None
        assert layout.spans is None, (
            "Small-caps ABSTRACT should not have spans — "
            "clearing spans prevents mixed-size rendering gaps"
        )

    def test_title_no_spans(self) -> None:
        """Title must not have per-span data after small-caps detection."""
        title = _find_block(self.page0, "DYNAMIC NESTED")
        layout = title.meta.layout
        assert layout is not None
        assert layout.spans is None, (
            "Small-caps title should not have spans — "
            "clearing spans prevents mixed-size rendering gaps"
        )

    def test_title_html_no_per_span_positioning(self) -> None:
        """Title HTML must not contain inner spans with absolute positioning.

        Per-span font-size elements create visible gaps due to font metric
        mismatches between PDF fonts and browser fonts.
        """
        title = _find_block(self.page0, "DYNAMIC NESTED")
        nid = title.meta.id
        div_pattern = re.compile(
            rf'<div[^>]*data-node-id="{re.escape(str(nid))}"[^>]*>(.*?)</div>',
        )
        m = div_pattern.search(self.html)
        assert m is not None, "Title div not found in rendered HTML"
        inner = m.group(1)
        assert "position: absolute" not in inner, (
            "Title inner HTML must not use position: absolute spans"
        )

    def test_math_blocks_still_use_absolute_positioning(self) -> None:
        """Math blocks with sub/superscripts must keep absolute positioning.

        Small-caps detection only applies to non-math blocks.  Math blocks
        need absolute positioning for subscripts, superscripts, and
        equation numbers at different vertical positions.
        """
        page2 = [
            c for c in self.children
            if hasattr(c, "meta") and c.meta.layout and c.meta.layout.page == 2
        ]
        para = _find_block(page2, "each module")
        layout = para.meta.layout
        assert layout is not None
        assert layout.spans is not None, (
            "Inline math paragraph must have spans"
        )

        # Check that the rendered HTML for this block uses absolute spans
        nid = para.meta.id
        div_pattern = re.compile(
            rf'<div[^>]*data-node-id="{re.escape(str(nid))}"[^>]*>(.*?)</div>',
        )
        m = div_pattern.search(self.html)
        assert m is not None, "Inline math div not found in rendered HTML"
        inner = m.group(1)
        assert "position: absolute" in inner, (
            "Inline math paragraph must use absolute positioning for "
            "sub/superscript placement"
        )
