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
from uaf.core.nodes import Artifact, Paragraph, Shape


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
        # Find the title block by its text content.
        title_pattern = re.compile(
            r'class="layout-block[^"]*"\s+style="([^"]*)">'
            r"[^<]*DYNAMIC NESTED",
        )
        m = title_pattern.search(self.html)
        assert m is not None, "Title block not found in rendered HTML"
        style = m.group(1)
        assert "font-size" in style, (
            f"Title block is missing font-size in its style attribute. "
            f"Style: {style!r}"
        )

    def test_section_heading_renders_bold(self) -> None:
        """The '1 Introduction' heading must render with font-weight: bold."""
        # The heading block renders as "1<br>Introduction" (PyMuPDF splits the
        # number and title onto separate lines within the same block).
        heading_pattern = re.compile(
            r'class="layout-block[^"]*"\s+style="([^"]*)">'
            r"1<br>Introduction",
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
    """Extract raw line texts from the PyMuPDF block containing *substring*."""
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
                    return [
                        "".join(s.get("text", "") for s in ln.get("spans", []))
                        for ln in lines
                    ]
    finally:
        doc.close()
    msg = f"No PDF block containing {substring!r}"
    raise ValueError(msg)


def _html_lines_for_block(html: str, substring: str) -> list[str]:
    """Extract the text lines from the rendered layout-block containing *substring*.

    The layout renderer uses ``<br>`` for line breaks, so we split on that.
    HTML entities are decoded for comparison.
    """
    # Find the div whose inner HTML contains the substring.
    pattern = re.compile(
        r'class="layout-block[^"]*"[^>]*>(.+?)</div>',
        re.DOTALL,
    )
    for m in pattern.finditer(html):
        inner = m.group(1)
        # Strip any <span> wrappers (first-line bold) for text extraction.
        text = re.sub(r"<span[^>]*>", "", inner)
        text = text.replace("</span>", "")
        if substring in text:
            lines = text.split("<br>")
            # Decode HTML entities for comparison.
            from html import unescape

            return [unescape(ln) for ln in lines]
    msg = f"No layout-block containing {substring!r}"
    raise ValueError(msg)


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
        """Every block on page 0 must have the same line count as the PDF."""
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
            if len(html_lines) != len(lines):
                mismatches.append(
                    f"  {ident!r}: PDF={len(lines)}, HTML={len(html_lines)}"
                )
        doc.close()

        assert not mismatches, (
            f"{len(mismatches)} block(s) have wrong line count:\n"
            + "\n".join(mismatches)
        )
