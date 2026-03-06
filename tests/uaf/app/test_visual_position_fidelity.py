"""Visual position fidelity tests — compare rendered HTML against PyMuPDF ground truth.

These tests verify that our Layout view positions text lines where the PDF
standard says they should be.  They compare:

1. Block-level positions: CSS top/left vs PDF bbox y0/x0
2. Per-line positions: each text line's y-position in the browser vs
   PyMuPDF's visual line y_top (after same-baseline merging)

The tests require Playwright with Chromium installed::

    uv sync
    playwright install chromium

Run explicitly with::

    make test-visual
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pw = pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, sync_playwright  # noqa: E402

from tests.uaf.app._pdf_fidelity_helpers import _import_pdf  # noqa: E402

_STYLE_CSS = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "src" / "uaf" / "app" / "static" / "style.css"
)

_PDF_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "fixtures" / "pdf" / "2511.14823v1.pdf"
)

# CSS px → pt conversion: 1pt = 96/72 px, so pt = px * 72/96
_PX_TO_PT = 72.0 / 96.0

# Tolerance in points for block-level position comparison.
_BLOCK_TOLERANCE_PT = 2.0

# Tolerance in points for per-line y-position comparison.
_LINE_TOLERANCE_PT = 3.0

# Pages to test (0-indexed).
_TEST_PAGES = (1, 2)


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


def _get_pdf_block_data(
    pages: tuple[int, ...],
) -> list[dict[str, Any]]:
    """Extract per-block and per-line position data from PyMuPDF.

    Returns a list of dicts, one per text block, with:
    - page: int
    - bbox: (x0, y0, x1, y1) in pt
    - visual_line_tops: list of y-top positions (pt) for each visual line
    - first_text: first 30 chars of the block's first visual line
    """
    import fitz

    from uaf.app.formats.pdf_format import (
        _merge_visual_lines,
        _same_baseline,
    )

    doc = fitz.open(str(_PDF_PATH))
    blocks: list[dict[str, Any]] = []

    for page_num in pages:
        if page_num >= len(doc):
            continue
        page = doc[page_num]
        raw: dict[str, Any] = page.get_text("dict")

        for block in raw.get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            lines = block.get("lines", [])
            if not lines:
                continue

            bbox = block["bbox"]
            visual_lines = _merge_visual_lines(block)
            if not visual_lines:
                continue

            # Compute per-visual-line y-tops using same baseline logic.
            raw_bb = lines[0].get("bbox", (0.0, 0.0, 0.0, 0.0))
            prev_bb = (
                float(raw_bb[0]), float(raw_bb[1]),
                float(raw_bb[2]), float(raw_bb[3]),
            )
            line_tops: list[float] = [prev_bb[1]]

            for line in lines[1:]:
                lb = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
                cur_bb = (
                    float(lb[0]), float(lb[1]),
                    float(lb[2]), float(lb[3]),
                )
                if not _same_baseline(prev_bb, cur_bb):
                    line_tops.append(cur_bb[1])
                prev_bb = cur_bb

            blocks.append({
                "page": page_num,
                "bbox": (
                    float(bbox[0]), float(bbox[1]),
                    float(bbox[2]), float(bbox[3]),
                ),
                "visual_line_tops": line_tops,
                "first_text": visual_lines[0][:30].strip(),
                "visual_line_count": len(visual_lines),
            })

    doc.close()
    return blocks


def _get_html_block_positions(
    page: Page,
    test_pages: tuple[int, ...],
) -> list[dict[str, Any]]:
    """Extract block and line positions from the rendered HTML via Playwright.

    For each .layout-block on the specified pages, returns:
    - page: int
    - rect: {top, left, width, height} in CSS px relative to page container
    - first_text: first 30 chars
    - line_tops: list of per-line y-positions (px) relative to page container
    """
    return page.evaluate(
        """(testPages) => {
            const results = [];
            const pageContainers = document.querySelectorAll(
                '.layout-page'
            );
            for (let pi = 0; pi < pageContainers.length; pi++) {
                if (!testPages.includes(pi)) continue;
                const pageEl = pageContainers[pi];
                const pageRect = pageEl.getBoundingClientRect();
                const blocks = pageEl.querySelectorAll('.layout-block');

                for (const block of blocks) {
                    const text = (block.textContent || '').trim();
                    if (text.length < 5) continue;
                    const blockRect = block.getBoundingClientRect();

                    // Get per-line positions.
                    // Strategy: find all .layout-line spans, or
                    // fall back to measuring text lines via Range API.
                    const lineSpans = block.querySelectorAll(
                        '.layout-line'
                    );
                    let lineTops = [];

                    if (lineSpans.length > 0) {
                        // Per-line spans exist.
                        for (const span of lineSpans) {
                            const r = span.getBoundingClientRect();
                            lineTops.push(r.top - pageRect.top);
                        }
                    } else {
                        // Measure line boxes via Range API.
                        // Walk text nodes and find distinct y-positions.
                        const walker = document.createTreeWalker(
                            block, NodeFilter.SHOW_TEXT
                        );
                        const seenYs = new Set();
                        let node;
                        while ((node = walker.nextNode())) {
                            if (!node.textContent.trim()) continue;
                            const range = document.createRange();
                            range.selectNodeContents(node);
                            const rects = range.getClientRects();
                            for (const r of rects) {
                                // Round to 0.5px to merge near-identical
                                const roundedY = Math.round(
                                    (r.top - pageRect.top) * 2
                                ) / 2;
                                if (!seenYs.has(roundedY) && r.width > 1) {
                                    seenYs.add(roundedY);
                                    lineTops.push(r.top - pageRect.top);
                                }
                            }
                        }
                        lineTops.sort((a, b) => a - b);
                        // Deduplicate within 1px tolerance.
                        const deduped = [];
                        for (const y of lineTops) {
                            if (deduped.length === 0 ||
                                y - deduped[deduped.length - 1] > 1) {
                                deduped.push(y);
                            }
                        }
                        lineTops = deduped;
                    }

                    results.push({
                        page: pi,
                        rect: {
                            top: blockRect.top - pageRect.top,
                            left: blockRect.left - pageRect.left,
                            width: blockRect.width,
                            height: blockRect.height,
                        },
                        first_text: text.substring(0, 30).trim(),
                        lineTops: lineTops,
                    });
                }
            }
            return results;
        }""",
        list(test_pages),
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


def _match_blocks(
    pdf_blocks: list[dict[str, Any]],
    html_blocks: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Match PDF blocks to HTML blocks by page and text prefix."""
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for pdf_b in pdf_blocks:
        prefix = pdf_b["first_text"][:15]
        if not prefix or len(prefix) < 5:
            continue
        for html_b in html_blocks:
            if html_b["page"] != pdf_b["page"]:
                continue
            if prefix in html_b["first_text"][:30]:
                pairs.append((pdf_b, html_b))
                break
    return pairs


@pytest.mark.playwright
class TestVisualPositionFidelity:
    """Compare rendered HTML text positions against PyMuPDF ground truth.

    This test class measures whether our Layout view places text where
    the PDF says it should be.  It compares block-level and per-line
    y-positions and fails when they diverge by more than tolerance.
    """

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:  # type: ignore[override]
        html = _build_layout_html()
        p, browser = _launch_browser_or_skip()
        page = browser.new_page()
        page.set_content(html, wait_until="load")

        self.pdf_blocks = _get_pdf_block_data(_TEST_PAGES)
        self.html_blocks = _get_html_block_positions(page, _TEST_PAGES)
        self.pairs = _match_blocks(self.pdf_blocks, self.html_blocks)

        browser.close()
        p.stop()

    def test_enough_blocks_matched(self) -> None:
        """We should match a reasonable number of blocks."""
        assert len(self.pairs) >= 10, (
            f"Only matched {len(self.pairs)} blocks between PDF "
            f"and HTML on pages {_TEST_PAGES}. "
            f"PDF has {len(self.pdf_blocks)}, "
            f"HTML has {len(self.html_blocks)}."
        )

    def test_block_y_positions(self) -> None:
        """Block top positions in HTML should match PDF bbox y0.

        The CSS ``top`` value should place the block at the same
        y-position as the PDF bounding box top.
        """
        errors: list[str] = []
        for pdf_b, html_b in self.pairs:
            pdf_y = pdf_b["bbox"][1]
            html_y = html_b["rect"]["top"] * _PX_TO_PT
            diff = abs(html_y - pdf_y)
            if diff > _BLOCK_TOLERANCE_PT:
                errors.append(
                    f"  {pdf_b['first_text']!r}: "
                    f"PDF y={pdf_y:.1f}pt, "
                    f"HTML y={html_y:.1f}pt, "
                    f"diff={diff:.1f}pt"
                )

        assert not errors, (
            f"{len(errors)} block(s) have y-position errors "
            f"> {_BLOCK_TOLERANCE_PT}pt:\n"
            + "\n".join(errors)
        )

    def test_block_x_positions(self) -> None:
        """Block left positions in HTML should match PDF bbox x0."""
        errors: list[str] = []
        for pdf_b, html_b in self.pairs:
            pdf_x = pdf_b["bbox"][0]
            html_x = html_b["rect"]["left"] * _PX_TO_PT
            diff = abs(html_x - pdf_x)
            if diff > _BLOCK_TOLERANCE_PT:
                errors.append(
                    f"  {pdf_b['first_text']!r}: "
                    f"PDF x={pdf_x:.1f}pt, "
                    f"HTML x={html_x:.1f}pt, "
                    f"diff={diff:.1f}pt"
                )

        assert not errors, (
            f"{len(errors)} block(s) have x-position errors "
            f"> {_BLOCK_TOLERANCE_PT}pt:\n"
            + "\n".join(errors)
        )

    def test_per_line_y_positions(self) -> None:
        """Individual line y-positions should match PDF visual lines.

        For multi-line text blocks, each line's y-position in the
        browser should match the corresponding visual line's y_top
        from PyMuPDF within tolerance.
        """
        errors: list[str] = []
        tested = 0

        for pdf_b, html_b in self.pairs:
            pdf_lines = pdf_b["visual_line_tops"]
            html_lines = html_b["lineTops"]

            if len(pdf_lines) < 2:
                continue  # skip single-line blocks

            # We need at least as many HTML lines as PDF lines.
            n = min(len(pdf_lines), len(html_lines))
            if n < 2:
                continue

            tested += 1
            for i in range(n):
                pdf_line_y = pdf_lines[i]
                html_line_y = html_lines[i] * _PX_TO_PT
                diff = abs(html_line_y - pdf_line_y)
                if diff > _LINE_TOLERANCE_PT:
                    errors.append(
                        f"  {pdf_b['first_text']!r} "
                        f"line {i}: "
                        f"PDF y={pdf_line_y:.1f}pt, "
                        f"HTML y={html_line_y:.1f}pt, "
                        f"diff={diff:.1f}pt"
                    )

        assert tested > 0, (
            "No multi-line blocks found to compare"
        )
        assert not errors, (
            f"{len(errors)} line position error(s) "
            f"> {_LINE_TOLERANCE_PT}pt across "
            f"{tested} multi-line block(s):\n"
            + "\n".join(errors)
        )

    def test_multiline_block_line_count_matches(self) -> None:
        """HTML should render the same number of visual lines as PDF."""
        mismatches: list[str] = []
        tested = 0

        for pdf_b, html_b in self.pairs:
            pdf_count = pdf_b["visual_line_count"]
            html_count = len(html_b["lineTops"])

            if pdf_count < 2:
                continue  # skip single-line blocks

            tested += 1
            if html_count != pdf_count:
                mismatches.append(
                    f"  {pdf_b['first_text']!r}: "
                    f"PDF={pdf_count} lines, "
                    f"HTML={html_count} lines"
                )

        if tested == 0:
            pytest.skip("No multi-line blocks to compare")
        assert not mismatches, (
            f"{len(mismatches)} block(s) have wrong line count:\n"
            + "\n".join(mismatches)
        )
