# 007 — Layout Fidelity: Ground-Truth Test Suite & Rendering Perfection

## Goal

Build an automated test suite that imports real-world PDFs, extracts their
layout metadata, and asserts specific geometric / typographic properties
against ground-truth values measured from a reference PDF viewer (Mac
Preview). Use these tests to drive rendering fixes until the UAF Layout
View is visually indistinguishable from the original PDF.

## Scope

**Phase 1 (this prompt):** Write the test infrastructure and all ground-
truth tests. Run them. Record which pass and which fail. For tests that
fail, add a comment with the actual value and a `pytest.mark.xfail` with
a reason string so the suite stays green.

**Phase 2 (stretch):** If time allows and failures are straightforward,
fix the extraction/rendering code to make failing tests pass. But do NOT
break existing tests (449 currently passing). Run `make check` after every
change.

## Project Conventions (important)

Read `CLAUDE.md` in the project root for full conventions. Key points:
- Python 3.13+, managed by UV
- `make check` runs ruff + mypy (strict) + pytest — **all must pass**
- Type annotations on all function signatures
- Line length: 99 characters
- Tests mirror `src/uaf/` structure under `tests/`
- No `conftest.py` exists — tests are self-contained

## Existing Test Patterns

Tests for PDF import use `GraphDB()` directly (not `SecureGraphDB`).
See `tests/uaf/app/test_format_handlers.py::TestPdfHandler` for examples:

```python
import fitz
from uaf.app.formats.pdf_format import PdfHandler
from uaf.core.nodes import Paragraph, Heading
from uaf.db.graph_db import GraphDB

def test_example(self, tmp_path: Path) -> None:
    db = GraphDB()
    handler = PdfHandler()
    root_id = handler.import_file(pdf_path, db)
    children = db.get_children(root_id)
    paragraphs = [c for c in children if isinstance(c, (Paragraph, Heading))]
    # Assert on paragraphs[i].meta.layout.x, .font_size, etc.
```

Existing fixtures live in `tests/fixtures/` with subdirs `csv/`,
`markdown/`, `txt/`. There is **no** `tests/fixtures/pdf/` directory yet.

## Test Library Architecture

### Directory structure

```
tests/fixtures/pdf/              # PDF fixtures (new dir — create it)
  2511.14823v1.pdf               # arXiv academic paper (first reference)
tests/uaf/app/test_pdf_fidelity.py  # Ground-truth assertion tests
```

### Helper pattern

Create a module-level helper in the test file:

```python
from pathlib import Path
from uaf.app.formats.pdf_format import PdfHandler
from uaf.core.nodes import Artifact, Heading, Paragraph
from uaf.db.graph_db import GraphDB

_FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "pdf"

def _import_pdf(filename: str) -> tuple[GraphDB, object, list[object]]:
    """Import a fixture PDF and return (db, root_id, children)."""
    db = GraphDB()
    handler = PdfHandler()
    root_id = handler.import_file(_FIXTURES / filename, db)
    children = db.get_children(root_id)
    return db, root_id, children

def _find_block(children: list[object], substring: str) -> object:
    """Find the first child whose text contains the given substring."""
    for child in children:
        text = getattr(child, "text", "") or getattr(child, "source", "")
        if substring in text:
            return child
    msg = f"No block containing {substring!r}"
    raise ValueError(msg)
```

### Reference PDF #1: `2511.14823v1.pdf`

**Copy from:** `/Users/brentfield/Library/CloudStorage/OneDrive-Personal/Free-Range-Intelligence/papers/2511.14823v1.pdf`
**Copy to:** `tests/fixtures/pdf/2511.14823v1.pdf`

Page 1 properties (612.0 x 792.0 pt, US Letter):

| Block | Content (preview)                           | bbox (x0,y0,x1,y1)            | w × h        | Font                     | Size  | Flags | Dir     |
|-------|---------------------------------------------|-------------------------------|-------------|--------------------------|-------|-------|---------|
| 0     | Title (DYNAMIC NESTED HIERARCHIES...)       | (72.4, 97.7, 539.6, 154.8)   | 467.2×57.1  | NimbusRomNo9L-Regu       | 17.2  | 4     | (1,0)   |
| 1     | Author 1 (Akbar Anbar Jafari + affil)      | (100.6, 204.6, 226.1, 258.5) | 125.5×53.9  | NimbusRomNo9L-Medi       | 9.96  | 20    | (1,0)   |
| 2     | Author 2 (Cagri Ozcinar + affil)           | (260.7, 204.6, 370.5, 258.5) | 109.8×53.9  | NimbusRomNo9L-Medi       | 9.96  | 20    | (1,0)   |
| 3     | Author 3 (Gholamreza Anbarjafari + affil)  | (405.1, 204.6, 511.4, 248.4) | 106.4×43.8  | NimbusRomNo9L-Medi       | 9.96  | 20    | (1,0)   |
| 4     | Date: November 20, 2025                     | (266.4, 288.1, 345.6, 298.1) | 79.1×10.0   | NimbusRomNo9L-Regu       | 9.96  | 4     | (1,0)   |
| 5     | Section heading: ABSTRACT                   | (277.3, 313.3, 334.7, 325.2) | 57.4×12.0   | NimbusRomNo9L-Medi       | 11.96 | 20    | (1,0)   |
| 6     | Abstract body (14 lines)                    | (107.9, 336.8, 505.8, 488.5) | 397.9×151.8 | NimbusRomNo9L-Regu       | 9.95  | 4     | (1,0)   |
| 7     | Keywords (2 lines, italic)                  | (71.7, 501.2, 534.2, 522.4)  | 462.6×21.2  | NimbusRomNo9L-MediItal   | 10.06 | 22    | (1,0)   |
| 8     | Section heading: 1 Introduction             | (72.0, 540.0, 154.8, 551.9)  | 82.8×12.0   | NimbusRomNo9L-Medi       | 11.96 | 20    | (1,0)   |
| 9     | Body paragraph (8 lines)                    | (71.6, 565.1, 540.0, 651.5)  | 468.4×86.4  | NimbusRomNo9L-Regu       | 10.06 | 4     | (1,0)   |
| 10    | Body paragraph (6 lines)                    | (72.0, 657.9, 541.7, 722.4)  | 469.7×64.6  | NimbusRomNo9L-Regu       | 10.0  | 4     | (1,0)   |
| 11    | arXiv sidebar (rotated)                     | (10.9, 219.9, 37.6, 572.1)   | 26.7×352.2  | Times-Roman              | 20.0  | 4     | (0,-1)  |

Font flag reference: bit 2 (4) = serif, bit 4 (16) = bold, bit 1 (2) = italic.
NimbusRomNo9L-Medi = bold variant (flags 20 = bold+serif).
NimbusRomNo9L-MediItal = bold+italic variant (flags 22).

**Important note on author blocks:** All three author blocks (1-3) have
flags=20 on ALL spans (the entire block uses the Medi/bold font variant).
Our `_extract_dominant_font` correctly detects this as `weight="bold"`.
The `first_line_weight` field would be `None` because it only differs
when first-line weight ≠ block weight.

### Key source files to read before writing tests

- `src/uaf/core/nodes.py` — `LayoutHint` dataclass (all fields)
- `src/uaf/app/formats/pdf_format.py` — `PdfHandler.import_file()`,
  `_extract_dominant_font()`, `_extract_first_line_font()`,
  `_map_font_family()`, `_extract_rotation()`, `_extract_block_text()`
- `src/uaf/app/lenses/doc_lens.py` — `DocLens.render_layout()`,
  `_render_layout_node()`, `_format_layout_text()`, `_font_style_parts()`
- `tests/uaf/app/test_format_handlers.py` — existing PDF test patterns
- `tests/uaf/app/test_doc_lens.py` — existing layout rendering tests

### Ground-truth test cases to implement

Each test imports the PDF via the helper, finds specific blocks by
content substring, and asserts LayoutHint properties within tolerance.
Use `pytest.approx(expected, abs=tolerance)` for float comparisons.

```python
class TestPdfFidelity2511:
    """Ground-truth layout tests against 2511.14823v1.pdf (page 1)."""

    # -- Geometry tests --

    def test_page_dimensions(self):
        """Artifact LayoutHint records page as 612×792 pt (US Letter)."""

    def test_title_position(self):
        """Title block at approx x=72, y=98 with width ≈467."""

    def test_title_not_spaced_letters(self):
        """Title text is readable, not 'D YNAMIC  N ESTED'.
        NOTE: this will likely fail (xfail) — small-caps reconstruction
        is not yet implemented."""

    def test_author_blocks_count(self):
        """Three separate author blocks exist."""

    def test_author_blocks_horizontally_spaced(self):
        """Author blocks are at distinct x positions spanning the page."""

    def test_date_centered(self):
        """Date block at x ≈ 266, width ≈ 79."""

    def test_abstract_indented(self):
        """Abstract body x ≈ 108 (indented vs body text at x ≈ 72)."""

    def test_body_text_full_width(self):
        """Body paragraphs span ≈468pt (margin to margin)."""

    def test_sidebar_rotation(self):
        """arXiv sidebar has rotation ≈ -90°."""

    def test_sidebar_width_is_run_length(self):
        """Rotated sidebar width = text run length (≈352pt), not 26.7."""

    def test_sidebar_y_position(self):
        """Sidebar y ≈ 572 (bbox bottom — anchor for -90° CSS rotation).
        The y is adjusted at extraction time so CSS rotate(-90deg) with
        transform-origin: top left places the text in the correct span."""

    # -- Typography tests --

    def test_title_font_family_maps_to_times(self):
        """Title font family contains 'Times New Roman'."""

    def test_title_font_size(self):
        """Title font size ≈ 17.2pt."""

    def test_author_blocks_bold(self):
        """Author blocks have font_weight='bold' (entire block is bold)."""

    def test_body_font_size(self):
        """Body text font size ≈ 10pt."""

    def test_abstract_heading_bold(self):
        """'ABSTRACT' heading is bold (font_weight='bold')."""

    def test_keywords_italic(self):
        """Keywords block has font_style='italic'."""

    def test_keywords_bold(self):
        """Keywords block has font_weight='bold' (MediItal = bold+italic)."""

    def test_section_heading_bold_and_larger(self):
        """'1 Introduction' is bold, size ≈ 12pt (> body at ≈ 10pt)."""

    # -- Text content tests --

    def test_dehyphenation(self):
        """No end-of-line hyphens splitting words remain in text.
        Check abstract and body paragraphs for patterns like 'capa-\\n'."""

    def test_no_double_spaces(self):
        """No paragraph text contains '  ' (double spaces)."""

    def test_date_text(self):
        """Date block text is 'November 20, 2025'."""

    def test_arxiv_sidebar_text(self):
        """Sidebar contains 'arXiv:2511.14823v1'."""

    # -- Block count test --

    def test_page1_block_count(self):
        """Page 1 has approximately 12 text blocks (±1 depending on
        how PyMuPDF groups lines)."""
```

## Known Issues (context for xfail annotations)

### Wrapping fidelity
Line breaks won't match the PDF exactly. Even with font mapping (PDF →
web-safe CSS), we don't extract character/word spacing. The `width` of
each block is correct, but the text within it wraps differently because
browser fonts have different metrics than the original PDF fonts.

### Rotated text positioning — FIXED
The sidebar bbox top-left is `(10.9, 219.9)`. CSS `rotate(-90deg)` with
`transform-origin: top left` swings the text upward from the anchor.
The extractor now places the anchor at the bbox bottom (`y1 ≈ 572`) for
-90° rotation so the text fills the correct vertical span (220–572 pt).
Verified by `test_sidebar_rotation_renders_within_page`.

### Small-caps title
PyMuPDF reports the title as `D YNAMIC  N ESTED...` with alternating
font sizes per letter. Our `_extract_block_text()` concatenates these
literally. Reconstruction is not yet implemented.

## Implementation Plan

1. Create `tests/fixtures/pdf/` directory
2. Copy `2511.14823v1.pdf` from the source path to `tests/fixtures/pdf/`
3. Create `tests/uaf/app/test_pdf_fidelity.py` with helper functions
4. Implement all test cases above
5. Run `make check` — all 449 existing tests must still pass
6. For new tests that fail: add `@pytest.mark.xfail(reason="...")` and
   a comment showing the actual value
7. Commit the test file, fixture, and any fixes

## Commands

```bash
make check                  # Run full suite (lint + mypy + test)
make test                   # Run just pytest
uv run pytest tests/uaf/app/test_pdf_fidelity.py -v  # Run fidelity tests only
```

## Files to create

- `tests/fixtures/pdf/2511.14823v1.pdf` — copy of reference PDF
- `tests/uaf/app/test_pdf_fidelity.py` — new ground-truth test file

## Files to read (do not modify unless fixing a failing test)

- `src/uaf/core/nodes.py` — LayoutHint fields
- `src/uaf/core/serialization.py` — LayoutHint serialization
- `src/uaf/app/formats/pdf_format.py` — PDF import logic
- `src/uaf/app/lenses/doc_lens.py` — layout rendering
- `tests/uaf/app/test_format_handlers.py` — existing test patterns
- `tests/uaf/app/test_doc_lens.py` — existing layout test patterns
