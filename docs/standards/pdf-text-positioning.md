# PDF Text Positioning — ISO 32000 Reference

This document summarizes the PDF text positioning model from **ISO 32000-1** (PDF 1.7)
and **ISO 32000-2** (PDF 2.0) as it applies to UAF's PDF import and layout rendering.

---

## Why This Matters for UAF

UAF's Layout view reconstructs the visual appearance of imported PDFs using CSS positioning.
Accurate text placement requires understanding how PDFs specify line spacing, which is
fundamentally **per-line** — not uniform like CSS `line-height`. This document explains the
PDF text model and how PyMuPDF maps it to the span data UAF consumes.

---

## Text Positioning Operators (ISO 32000 §9.4.2)

PDF positions text using a **text matrix** (`Tm`) and related operators. Text is not laid
out in a flow model like HTML — each line (or even each character) can be placed at an
arbitrary position.

| Operator | Parameters | Effect |
|----------|-----------|--------|
| `Tm` | a, b, c, d, e, f | Set the text matrix directly (6-element affine transform). `e` is the x-position and `f` is the y-position of the text baseline. |
| `Td` | tx, ty | Move to the start of the next line, offset from the start of the current line by `(tx, ty)`. **Each `Td` can use a different `ty` — line spacing is per-line.** |
| `TD` | tx, ty | Same as `Td` but also sets the leading (`TL`) to `-ty`. |
| `T*` | — | Move to the start of the next line using the current leading value (`TL`). Equivalent to `0 -TL Td`. |
| `TL` | leading | Set the text leading (default line spacing for `T*`). Only applies when `T*` is used. |

### Key Insight: Per-Line Spacing

Unlike HTML/CSS where `line-height` applies uniformly to all lines in a block, PDF text
positioning uses **individual `Td` offsets** for each line. This means:

- Line 1 → Line 2 might have a 10.9pt gap
- Line 2 → Line 3 might have a 11.2pt gap
- Line 3 → Line 4 might have a 10.6pt gap

These variations come from the PDF authoring tool's layout engine (e.g., LaTeX, InDesign,
Word) and reflect the actual typographic decisions made during document creation.

---

## Baselines vs. Bounding Boxes

PDF text positioning is **baseline-relative**. The y-coordinate in `Tm`/`Td` specifies
where the text baseline sits — the line on which most characters rest.

```
  ┌─────────────────────────┐  ← ascender line (top of 'h', 'l', etc.)
  │  A quick brown fox      │
  │_________________________│  ← baseline (where 'A', 'q', 'b' sit)
  │  ↓ descender            │  ← descender line (bottom of 'g', 'p', 'y')
  └─────────────────────────┘  ← bounding box bottom
```

**Bounding boxes** (reported by PyMuPDF as `bbox`) include the full extent from ascender
to descender (and sometimes extra padding). For line spacing computation, baselines are
more accurate than bounding box tops because:

1. Subscripts extend the bounding box downward without moving the baseline
2. Superscripts extend upward without moving the baseline
3. Different font sizes on the same line produce different bounding box heights

---

## PyMuPDF Span Data

PyMuPDF (fitz) extracts text with per-span metadata via `page.get_text("dict")`. Each
span includes:

| Field | Description | PDF Source |
|-------|-------------|------------|
| `origin` | `(x, y)` — the first character's baseline position | Text matrix `Tm` position |
| `bbox` | `(x0, y0, x1, y1)` — bounding rectangle | Computed from font metrics |
| `size` | Font size in points | Text state `Tf` parameter |
| `font` | Font name string | Font resource reference |
| `flags` | Bitfield: italic, bold, serif, etc. | Font descriptor flags |

### `origin[1]` = Baseline Y-Coordinate

PyMuPDF documents `origin` as "the first character's origin point." The y-value
(`origin[1]`) corresponds to the PDF text matrix baseline — this is the value UAF uses
for `line_baselines` computation.

For line spacing, UAF uses `origin[1]` (baseline) rather than `bbox[1]` (top of bounding
box) because baselines are stable across mixed font sizes, subscripts, and superscripts.

---

## How UAF Uses This Data

### Extraction (`pdf_format.py`)

1. **Visual line grouping**: Lines with overlapping bounding boxes on the same baseline
   are merged (same-baseline merging handles cases like "1" and "Introduction" being
   separate PyMuPDF lines).

2. **Baseline computation**: For each visual line group, the median `origin[1]` of all
   spans gives the group's baseline y-coordinate.

3. **Relative offsets**: Baselines are converted to relative offsets from the first line:
   `(0.0, 10.9, 21.8, ...)` — stored as `LayoutHint.line_baselines`.

4. **Median line height**: The median of consecutive baseline gaps gives
   `LayoutHint.line_height` as a fallback.

### Rendering (`doc_lens.py`)

When `line_baselines` is available, each text line is rendered as a positioned span:

```html
<span class="layout-line" style="display: block; position: absolute;
      top: 0.0pt; white-space: nowrap">First line text</span>
<span class="layout-line" style="display: block; position: absolute;
      top: 10.9pt; white-space: nowrap">Second line text</span>
```

This replaces the CSS `line-height` approach, which can only produce uniform spacing.

---

## Font Metrics and Vertical Alignment

### Subscripts and Superscripts

PDF handles sub/superscripts by positioning them at different baseline y-offsets with
smaller font sizes. There is no semantic "subscript" operator — it's purely positional:

- A subscript is text at a **lower** baseline (higher y in PDF coordinates) with a
  smaller font size
- A superscript is text at a **higher** baseline (lower y) with a smaller font size

UAF captures this as `FontAnnotation.vertical_align` — the baseline offset in points
relative to the line's dominant baseline. CSS renders it using `vertical-align: Xpt`.

### Font Families

PDF embeds fonts by name. Common families in academic papers:

| PDF Font | Description | CSS Mapping |
|----------|-------------|-------------|
| NimbusRomNo9L / TimesNewRomanPSMT | Body text | `'Latin Modern Roman', serif` |
| CMMI* | Computer Modern Math Italic | `'Latin Modern Math', serif` |
| CMSY* | Computer Modern Math Symbols | `'Latin Modern Math', serif` |
| CMR* | Computer Modern Roman | `'Latin Modern Math', serif` |
| CMEX* | Computer Modern Math Extensions | `'Latin Modern Math', serif` |

---

## Specification References

- **ISO 32000-1:2008** (PDF 1.7) — Section 9: Text
  - §9.2: Organization and use of fonts
  - §9.3: Text state parameters and operators
  - §9.4: Text objects — `BT`/`ET` operators, text positioning (`Tm`, `Td`, `TD`, `T*`)
  - §9.4.2: Text-positioning operators (detailed semantics)
  - §9.4.4: Text-showing operators (`Tj`, `TJ`, `'`, `"`)

- **ISO 32000-2:2020** (PDF 2.0) — Same section structure with extensions

- **PyMuPDF Documentation**:
  - [`Page.get_text("dict")`](https://pymupdf.readthedocs.io/en/latest/page.html#Page.get_text)
  - [TextPage dict structure](https://pymupdf.readthedocs.io/en/latest/textpage.html)

---

## Roadmap: Additional Standards to Ingest

The following ISO 32000 areas should be documented as UAF's format handling matures:

| Area | ISO 32000 Section | UAF Relevance |
|------|-------------------|---------------|
| **Graphics model** | §8 (Graphics) | Coordinate systems, CTM, color spaces — needed for Shape rendering |
| **Font handling** | §9.5–9.10 | Font descriptors, encoding, CIDFonts, ToUnicode — font mapping accuracy |
| **Page structure** | §7.7 (Page Tree) | MediaBox/CropBox/BleedBox — accurate page dimensions and margins |
| **Image handling** | §8.9 (Images) | Inline images, XObjects — future image import |
| **Annotations** | §12.5 | Comments, highlights, form fields — future annotation import |
