"""Visual fidelity tests for inline math rendering using Playwright.

These tests render the layout HTML in a headless browser and verify that
computed CSS properties on inline math elements are correct.  They require
Playwright with Chromium installed::

    uv sync
    playwright install chromium

Tests auto-skip if Playwright is not installed.  Run explicitly with::

    make test-visual
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pw = pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, sync_playwright

from tests.uaf.app._pdf_fidelity_helpers import _import_pdf

_STYLE_CSS = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "src" / "uaf" / "app" / "static" / "style.css"
)

# A distinctive substring from a section 2.3 paragraph that contains inline
# math (Greek letters in Computer Modern fonts).
_INLINE_MATH_SUBSTRING = "meta-loss exceeds a threshold"


def _build_layout_html() -> str:
    """Import the reference PDF and render its layout view as a full HTML document."""
    from uaf.app.lenses.doc_lens import DocLens
    from uaf.security.auth import LocalAuthProvider
    from uaf.security.secure_graph_db import SecureGraphDB

    db, root_id, _children = _import_pdf("2511.14823v1.pdf")
    auth = LocalAuthProvider()
    sdb = SecureGraphDB(db, auth)
    session = sdb.system_session()
    lens = DocLens()
    view = lens.render_layout(sdb, session, root_id)

    css = _STYLE_CSS.read_text() if _STYLE_CSS.exists() else ""
    return (
        "<!DOCTYPE html>\n"
        "<html><head><meta charset='utf-8'>\n"
        f"<style>{css}</style></head>\n"
        f"<body><div class='layout-view'>{view.content}</div></body></html>"
    )


def _find_inline_math_block(page: Page) -> Any:
    """Find the layout-block div containing the inline math substring."""
    return page.evaluate(
        """(substring) => {
            const blocks = document.querySelectorAll('.layout-block');
            for (const block of blocks) {
                if (block.textContent && block.textContent.includes(substring)) {
                    return {
                        found: true,
                        textContent: block.textContent.substring(0, 200),
                        hasSpans: block.querySelectorAll('span').length > 0,
                        spanCount: block.querySelectorAll('span').length,
                    };
                }
            }
            return { found: false };
        }""",
        _INLINE_MATH_SUBSTRING,
    )


def _launch_browser_or_skip() -> Any:
    """Launch Chromium, skipping the test if the binary isn't installed."""
    p = sync_playwright().start()
    try:
        browser = p.chromium.launch()
    except Exception as exc:
        p.stop()
        if "Executable doesn't exist" in str(exc):
            pytest.skip(
                "Chromium not installed — run 'playwright install chromium'"
            )
        raise
    return p, browser


@pytest.mark.playwright
class TestInlineMathComputedStyles:
    """Playwright-based tests for inline math computed CSS properties.

    These tests verify that inline math characters in paragraphs get
    distinct font-family styling while preserving normal text flow
    (no absolute positioning).
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:  # type: ignore[override]
        html = _build_layout_html()
        p, browser = _launch_browser_or_skip()
        page = browser.new_page()
        page.set_content(html, wait_until="load")

        self.block_info = _find_inline_math_block(page)

        # Get detailed span info for the target block.
        self.span_details: list[dict[str, Any]] = page.evaluate(
            """(substring) => {
                const blocks = document.querySelectorAll('.layout-block');
                for (const block of blocks) {
                    if (!block.textContent ||
                        !block.textContent.includes(substring)) continue;
                    const spans = block.querySelectorAll('span');
                    const parentStyle = block.style.fontFamily || '';
                    const results = [];
                    for (const span of spans) {
                        results.push({
                            text: span.textContent,
                            inlineFontFamily: span.style.fontFamily,
                            inlineFontStyle: span.style.fontStyle,
                            computedPosition: getComputedStyle(span).position,
                            computedDisplay: getComputedStyle(span).display,
                            hasLeft: span.style.left !== '',
                            hasTop: span.style.top !== '',
                        });
                    }
                    return results;
                }
                return [];
            }""",
            _INLINE_MATH_SUBSTRING,
        )

        browser.close()
        p.stop()

    def test_block_found(self) -> None:
        """The target paragraph with inline math exists in the rendered HTML."""
        assert self.block_info["found"], (
            f"Could not find layout-block containing {_INLINE_MATH_SUBSTRING!r}"
        )

    def test_inline_math_has_span_elements(self) -> None:
        """The paragraph should contain <span> elements for inline math styling."""
        assert self.block_info["hasSpans"], (
            "Paragraph with inline math should contain <span> elements "
            "for math character font styling"
        )

    def test_inline_math_has_distinct_font_family(self) -> None:
        """At least one span should have a font-family style declaration."""
        spans_with_font = [
            s for s in self.span_details if s["inlineFontFamily"]
        ]
        assert len(spans_with_font) > 0, (
            "Expected at least one inline math span with a distinct "
            f"font-family declaration. Spans: {self.span_details}"
        )

    def test_inline_math_spans_are_not_absolute(self) -> None:
        """Inline math spans must NOT use absolute positioning."""
        for span in self.span_details:
            assert span["computedPosition"] != "absolute", (
                f"Inline math span {span['text']!r} should not be "
                f"position: absolute — this breaks text flow"
            )
            assert not span["hasLeft"], (
                f"Inline math span {span['text']!r} should not have "
                f"CSS 'left' property"
            )
            assert not span["hasTop"], (
                f"Inline math span {span['text']!r} should not have "
                f"CSS 'top' property"
            )

    def test_inline_math_spans_are_inline(self) -> None:
        """Inline math spans should have inline display (normal text flow)."""
        for span in self.span_details:
            assert span["computedDisplay"] == "inline", (
                f"Inline math span {span['text']!r} should be "
                f"display: inline, got {span['computedDisplay']!r}"
            )

    def test_surrounding_text_preserves_spacing(self) -> None:
        """The paragraph text should not have collapsed whitespace."""
        text = self.block_info.get("textContent", "")
        # The phrase should appear with normal word spacing.
        assert "meta-loss exceeds a threshold" in text, (
            f"Expected intact text around inline math, got: {text!r}"
        )
        # No double spaces (sign of broken span boundaries).
        assert "  " not in text.replace("\n", " "), (
            f"Double spaces found in paragraph text: {text!r}"
        )


@pytest.mark.playwright
class TestLineCountPreservation:
    """Verify that rendered HTML preserves the PDF's visual line count.

    This is the critical regression test: V1's inline-span approach broke
    line counts because PyMuPDF reports subscripts/superscripts as separate
    "lines".  Without same-baseline merging, a 3-line paragraph produced
    12+ <br> tags, causing massive text overflow and overlapping.

    The test compares <br> counts in rendered HTML blocks against the
    PDF's visual line count (after same-baseline merging).
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:  # type: ignore[override]
        html = _build_layout_html()
        p, browser = _launch_browser_or_skip()
        page = browser.new_page()
        page.set_content(html, wait_until="load")

        # Collect line counts from ALL rendered blocks on pages 2-3
        # (section 2.3 territory).
        self.block_line_counts: list[dict[str, Any]] = page.evaluate(
            """() => {
                const blocks = document.querySelectorAll('.layout-block');
                const results = [];
                for (const block of blocks) {
                    const page = block.getAttribute('data-page');
                    if (page !== '2' && page !== '3') continue;
                    const text = block.textContent || '';
                    if (text.trim().length < 10) continue;
                    const html = block.innerHTML;
                    const brCount = (html.match(/<br>/g) || []).length;
                    const htmlLines = brCount + 1;
                    results.push({
                        ident: text.substring(0, 30).trim(),
                        htmlLines: htmlLines,
                        textLength: text.length,
                    });
                }
                return results;
            }"""
        )

        browser.close()
        p.stop()

        # Also get the PDF visual line counts via same-baseline merging.
        import fitz

        from uaf.app.formats.pdf_format import _merge_visual_lines

        pdf_path = (
            Path(__file__).resolve().parent.parent.parent
            / "fixtures" / "pdf" / "2511.14823v1.pdf"
        )
        doc = fitz.open(str(pdf_path))
        self.pdf_line_counts: dict[str, int] = {}
        for page_num in (2, 3):
            if page_num >= len(doc):
                continue
            page_data: dict[str, Any] = doc[page_num].get_text("dict")
            for block in page_data.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                lines = block.get("lines", [])
                if not lines:
                    continue
                first_text = "".join(
                    s.get("text", "") for s in lines[0].get("spans", [])
                )
                ident = first_text[:30].strip()
                if not ident or len(ident) < 10:
                    continue
                visual_lines = _merge_visual_lines(block)
                self.pdf_line_counts[ident] = len(visual_lines)
        doc.close()

    def test_html_line_count_matches_pdf(self) -> None:
        """Every block's HTML <br> count must match PDF visual line count."""
        mismatches: list[str] = []
        matched = 0
        for block in self.block_line_counts:
            ident = block["ident"]
            # Find matching PDF block by prefix.
            pdf_count = self.pdf_line_counts.get(ident)
            if pdf_count is None:
                continue
            matched += 1
            if block["htmlLines"] != pdf_count:
                mismatches.append(
                    f"  {ident!r}: PDF={pdf_count}, "
                    f"HTML={block['htmlLines']}"
                )

        assert matched > 0, (
            "No blocks matched between HTML and PDF — test is broken"
        )
        assert not mismatches, (
            f"{len(mismatches)} of {matched} block(s) have wrong line "
            f"count in rendered HTML:\n" + "\n".join(mismatches)
        )

    def test_no_excessive_line_breaks(self) -> None:
        """No block should have more than 2x the expected PDF line count.

        This catches the V1 regression where subscript "lines" weren't
        merged, producing 12+ <br> tags for a 3-line paragraph.
        """
        excessive: list[str] = []
        for block in self.block_line_counts:
            ident = block["ident"]
            pdf_count = self.pdf_line_counts.get(ident)
            if pdf_count is None:
                continue
            if block["htmlLines"] > pdf_count * 2:
                excessive.append(
                    f"  {ident!r}: PDF={pdf_count}, "
                    f"HTML={block['htmlLines']} "
                    f"(>{pdf_count * 2}x expected)"
                )

        assert not excessive, (
            "Blocks with excessive <br> tags (likely broken "
            "same-baseline merging):\n" + "\n".join(excessive)
        )
