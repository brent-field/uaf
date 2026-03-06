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
    """Import the reference PDF and render its layout view as a full HTML."""
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
        "<body><div class='layout-view'>"
        f"{view.content}</div></body></html>"
    )


def _find_inline_math_block(page: Page) -> Any:
    """Find the layout-block div containing the inline math substring."""
    return page.evaluate(
        """(substring) => {
            const blocks = document.querySelectorAll('.layout-block');
            for (const block of blocks) {
                if (block.textContent &&
                    block.textContent.includes(substring)) {
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
    """Launch Chromium, skipping if the binary isn't installed."""
    p = sync_playwright().start()
    try:
        browser = p.chromium.launch()
    except Exception as exc:
        p.stop()
        if "Executable doesn't exist" in str(exc):
            pytest.skip(
                "Chromium not installed — "
                "run 'playwright install chromium'"
            )
        raise
    return p, browser


@pytest.mark.playwright
class TestInlineMathComputedStyles:
    """Playwright tests for inline math computed CSS properties.

    Verifies that inline math characters get distinct font-family
    styling, correct font-size for sub/superscripts, and
    vertical-align CSS — while preserving normal text flow.
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
                const blocks = document.querySelectorAll(
                    '.layout-block'
                );
                for (const block of blocks) {
                    if (!block.textContent ||
                        !block.textContent.includes(substring))
                        continue;
                    const spans = block.querySelectorAll(
                        'span:not(.layout-line)'
                    );
                    const results = [];
                    for (const span of spans) {
                        const cs = getComputedStyle(span);
                        results.push({
                            text: span.textContent,
                            inlineFontFamily: span.style.fontFamily,
                            inlineFontStyle: span.style.fontStyle,
                            inlineFontSize: span.style.fontSize,
                            inlineFontWeight: span.style.fontWeight,
                            inlineVerticalAlign:
                                span.style.verticalAlign,
                            computedPosition: cs.position,
                            computedDisplay: cs.display,
                            computedFontSize: cs.fontSize,
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
        """The target paragraph with inline math exists."""
        assert self.block_info["found"], (
            "Could not find layout-block containing "
            f"{_INLINE_MATH_SUBSTRING!r}"
        )

    def test_inline_math_has_span_elements(self) -> None:
        """The paragraph should contain <span> elements."""
        assert self.block_info["hasSpans"], (
            "Paragraph with inline math should contain <span> "
            "elements for math character font styling"
        )

    def test_inline_math_has_many_spans(self) -> None:
        """Paragraph should have many spans, not just a few.

        Before the fix, only 3-5 of 44+ math spans were annotated.
        """
        assert self.block_info["spanCount"] >= 5, (
            f"Expected >= 5 spans for inline math, got "
            f"{self.block_info['spanCount']} — most math fonts "
            "are likely being skipped"
        )

    def test_inline_math_has_distinct_font_family(self) -> None:
        """At least one span should have a font-family declaration."""
        spans_with_font = [
            s for s in self.span_details if s["inlineFontFamily"]
        ]
        assert len(spans_with_font) > 0, (
            "Expected at least one inline math span with a "
            "distinct font-family declaration. "
            f"Spans: {self.span_details}"
        )

    def test_inline_math_spans_are_not_absolute(self) -> None:
        """Inline math spans must NOT use absolute positioning."""
        for span in self.span_details:
            assert span["computedPosition"] != "absolute", (
                f"Inline math span {span['text']!r} should not "
                "be position: absolute"
            )
            assert not span["hasLeft"], (
                f"Span {span['text']!r} should not have 'left'"
            )
            assert not span["hasTop"], (
                f"Span {span['text']!r} should not have 'top'"
            )

    def test_inline_math_spans_are_inline(self) -> None:
        """Inline math spans should have inline display."""
        for span in self.span_details:
            assert span["computedDisplay"] == "inline", (
                f"Span {span['text']!r} should be display: "
                f"inline, got {span['computedDisplay']!r}"
            )

    def test_some_spans_have_font_size(self) -> None:
        """Sub/superscript spans should have explicit font-size."""
        with_size = [
            s for s in self.span_details if s["inlineFontSize"]
        ]
        assert len(with_size) > 0, (
            "Expected at least one span with explicit font-size "
            "for subscript/superscript styling"
        )

    def test_subscripts_have_smaller_font(self) -> None:
        """Subscript spans should have smaller computed font-size."""
        # Get the parent block's font size from the inline style.
        # Subscript spans should compute to a smaller pixel size.
        sub_spans = [
            s for s in self.span_details
            if s.get("inlineVerticalAlign")
        ]
        if not sub_spans:
            pytest.skip("No vertically aligned spans found")
        for span in sub_spans:
            size_str = span.get("computedFontSize", "")
            if not size_str:
                continue
            size_px = float(size_str.replace("px", ""))
            # Subscripts should be < 14px (body text at 10pt ≈ 13.3px)
            assert size_px < 14.0, (
                f"Sub/super span {span['text']!r} has "
                f"computed font-size {size_str} — should be "
                "smaller than body text"
            )

    def test_some_spans_have_vertical_align(self) -> None:
        """Sub/superscript spans should have numeric vertical-align."""
        valign_spans = [
            s for s in self.span_details
            if s.get("inlineVerticalAlign")
            and "pt" in str(s.get("inlineVerticalAlign"))
        ]
        assert len(valign_spans) > 0, (
            "Expected sub/superscript spans with vertical-align "
            "pt values. Section 2.3 has subscripts/superscripts."
        )

    def test_surrounding_text_preserves_spacing(self) -> None:
        """The paragraph text should not have collapsed whitespace."""
        text = self.block_info.get("textContent", "")
        assert "meta-loss exceeds a threshold" in text, (
            f"Expected intact text around inline math, "
            f"got: {text!r}"
        )
        assert "  " not in text.replace("\n", " "), (
            f"Double spaces found in paragraph text: {text!r}"
        )


@pytest.mark.playwright
class TestLineCountPreservation:
    """Verify rendered HTML preserves the PDF's visual line count.

    The critical regression test: compares <br> counts in rendered
    HTML blocks against the PDF's visual line count (after
    same-baseline merging).
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:  # type: ignore[override]
        html = _build_layout_html()
        p, browser = _launch_browser_or_skip()
        page = browser.new_page()
        page.set_content(html, wait_until="load")

        self.block_line_counts: list[dict[str, Any]] = (
            page.evaluate(
                """() => {
                const blocks = document.querySelectorAll(
                    '.layout-block'
                );
                const results = [];
                for (const block of blocks) {
                    const page = block.getAttribute('data-page');
                    if (page !== '2' && page !== '3') continue;
                    const text = block.textContent || '';
                    if (text.trim().length < 10) continue;
                    const layoutLines =
                        block.querySelectorAll('.layout-line');
                    let htmlLines;
                    if (layoutLines.length > 0) {
                        htmlLines = layoutLines.length;
                    } else {
                        const html = block.innerHTML;
                        const brCount =
                            (html.match(/<br>/g) || []).length;
                        htmlLines = brCount + 1;
                    }
                    results.push({
                        ident: text.substring(0, 30).trim(),
                        htmlLines: htmlLines,
                        textLength: text.length,
                    });
                }
                return results;
            }"""
            )
        )

        browser.close()
        p.stop()

        # Also get PDF visual line counts via same-baseline merging.
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
            page_data: dict[str, Any] = (
                doc[page_num].get_text("dict")
            )
            for block in page_data.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                lines = block.get("lines", [])
                if not lines:
                    continue
                first_text = "".join(
                    s.get("text", "")
                    for s in lines[0].get("spans", [])
                )
                ident = first_text[:30].strip()
                if not ident or len(ident) < 10:
                    continue
                visual_lines = _merge_visual_lines(block)
                self.pdf_line_counts[ident] = len(visual_lines)
        doc.close()

    def test_html_line_count_matches_pdf(self) -> None:
        """Block HTML <br> count must match PDF visual line count."""
        mismatches: list[str] = []
        matched = 0
        for block in self.block_line_counts:
            ident = block["ident"]
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
            "No blocks matched between HTML and PDF"
        )
        assert not mismatches, (
            f"{len(mismatches)} of {matched} block(s) have wrong "
            "line count in rendered HTML:\n"
            + "\n".join(mismatches)
        )

    def test_no_excessive_line_breaks(self) -> None:
        """No block should have > 2x the expected PDF line count."""
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


@pytest.mark.playwright
class TestMathBlockLineHeight:
    """Verify line-height is reasonable for blocks with inline math."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:  # type: ignore[override]
        html = _build_layout_html()
        p, browser = _launch_browser_or_skip()
        page = browser.new_page()
        page.set_content(html, wait_until="load")

        self.block_heights: list[dict[str, Any]] = page.evaluate(
            """() => {
            const blocks = document.querySelectorAll(
                '.layout-block[data-page="2"]'
            );
            const results = [];
            for (const block of blocks) {
                const style = block.style;
                const fontSize = parseFloat(style.fontSize) || 0;
                const lineHeight =
                    parseFloat(style.lineHeight) || 0;
                if (lineHeight > 0 && fontSize > 0) {
                    results.push({
                        text: block.textContent.substring(0, 30),
                        fontSize: fontSize,
                        lineHeight: lineHeight,
                        ratio: lineHeight / fontSize,
                    });
                }
            }
            return results;
        }"""
        )

        browser.close()
        p.stop()

    def test_line_height_not_collapsed(self) -> None:
        """No block should have line-height < 80% of font-size."""
        collapsed = [
            b for b in self.block_heights if b["ratio"] < 0.8
        ]
        assert not collapsed, (
            "Blocks with collapsed line-height (< 0.8x font-size):"
            " " + ", ".join(
                f"{b['text']!r} (ratio={b['ratio']:.2f})"
                for b in collapsed
            )
        )


# Pages to check for overlap (0-indexed).  Page 2 covers section 2.x
# body text including the "Level Addition" paragraph with the inline
# fraction that triggered the envelope tracking fix.  Pages 0-1 are
# excluded because dense math subscripts and graph-notation formulas
# cause bounding-box overlaps unrelated to the fraction-split fix.
_OVERLAP_TEST_PAGES = [2]

# Conversion factor from CSS px to PDF pt.
_PX_TO_PT = 72.0 / 96.0


@pytest.mark.playwright
class TestNoLayoutLineOverlap:
    """Verify that adjacent layout-line spans do not visually overlap.

    When ``_same_baseline()`` incorrectly splits an inline fraction
    (numerator/denominator), the resulting visual lines can overlap because
    one line's content extends above or below its anchor position.

    This test measures the bounding boxes of adjacent ``.layout-line``
    spans within each block and asserts that no line's bottom extends
    past the next line's top by more than a small tolerance.
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        html = _build_layout_html()
        p = sync_playwright().start()
        try:
            browser = p.chromium.launch()
        except Exception:
            p.stop()
            pytest.skip("Chromium not installed — run: playwright install chromium")
            return  # unreachable but keeps mypy happy

        page = browser.new_page()
        page.set_content(html, wait_until="load")

        # For each block with layout-line spans on the test pages,
        # measure bounding boxes and check for overlap.
        self.overlap_data: list[dict[str, Any]] = page.evaluate(
            """(testPages) => {
                const results = [];
                const pageContainers = document.querySelectorAll(
                    '.layout-page'
                );
                for (let pi = 0; pi < pageContainers.length; pi++) {
                    if (!testPages.includes(pi)) continue;
                    const pageEl = pageContainers[pi];
                    const blocks = pageEl.querySelectorAll(
                        '.layout-block'
                    );
                    for (const block of blocks) {
                        const lineSpans = block.querySelectorAll(
                            '.layout-line'
                        );
                        if (lineSpans.length < 2) continue;

                        const text =
                            (block.textContent || '').trim();
                        const rects = [];
                        for (const span of lineSpans) {
                            const r = span.getBoundingClientRect();
                            rects.push({
                                top: r.top,
                                bottom: r.bottom,
                                height: r.height,
                                text: span.textContent
                                    .substring(0, 40),
                            });
                        }

                        // Check consecutive pairs for overlap.
                        // Tolerance: 25px (~19pt). Blocks with
                        // inline math sub/superscripts naturally
                        // have 12-20px bounding-box overlap from
                        // vertical-align CSS. The fraction-split
                        // bug caused 30+ px overlap.
                        const overlaps = [];
                        for (
                            let i = 0; i < rects.length - 1; i++
                        ) {
                            const overlapPx =
                                rects[i].bottom -
                                rects[i + 1].top;
                            if (overlapPx > 25.0) {
                                overlaps.push({
                                    lineA: i,
                                    lineB: i + 1,
                                    overlapPx: overlapPx,
                                    textA: rects[i].text,
                                    textB: rects[i + 1].text,
                                    bottomA: rects[i].bottom,
                                    topB: rects[i + 1].top,
                                });
                            }
                        }

                        if (overlaps.length > 0) {
                            results.push({
                                blockText: text.substring(0, 40),
                                lineCount: lineSpans.length,
                                overlaps: overlaps,
                            });
                        }
                    }
                }
                return results;
            }""",
            _OVERLAP_TEST_PAGES,
        )

        browser.close()
        p.stop()

    def test_no_overlapping_lines(self) -> None:
        """No adjacent layout-line spans should have severe overlapping bounds.

        Tolerance: 25px (~19pt).  Blocks with inline math sub/superscripts
        naturally have 12-20px bounding-box overlap from vertical-align CSS.
        The ``prev_bbox`` drift bug caused 30+ px visual overlap in the
        "Level Addition" block's formula line.
        """
        if not self.overlap_data:
            return  # No overlaps found — test passes

        errors: list[str] = []
        for block in self.overlap_data:
            for ov in block["overlaps"]:
                overlap_pt = ov["overlapPx"] * _PX_TO_PT
                errors.append(
                    f"  Block {block['blockText']!r}:\n"
                    f"    line {ov['lineA']} ({ov['textA']!r}) "
                    f"bottom={ov['bottomA']:.1f}px\n"
                    f"    line {ov['lineB']} ({ov['textB']!r}) "
                    f"top={ov['topB']:.1f}px\n"
                    f"    overlap={ov['overlapPx']:.1f}px "
                    f"({overlap_pt:.1f}pt)"
                )

        assert not errors, (
            f"{len(errors)} layout-line overlap(s) found "
            "(adjacent lines' bounding boxes intersect):\n"
            + "\n".join(errors)
        )
