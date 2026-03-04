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
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                if "Executable doesn't exist" in str(exc):
                    pytest.skip(
                        "Chromium not installed — "
                        "run 'playwright install chromium'"
                    )
                raise
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
