# Shapes Support — Kickoff Prompt

**Date:** 2026-02-24
**Status:** Proposed
**Scope:** Extract and render visual shapes (rules, lines, rectangles) from imported PDFs

---

## Problem

When importing PDFs like the academic paper `2511.14823v1.pdf`, horizontal rules
(decorative lines above and below the title) are present in the original document but
missing from our Layout view. These are **drawing commands** (vector graphics), not text
blocks — PyMuPDF's `get_text("dict")` only returns text. The UAF data model already has
a `Shape` node type (`shape_type, x, y, width, height`) but the PDF import handler does
not extract shapes.

## Goal

1. Extract simple vector shapes (lines, rectangles, horizontal/vertical rules) from PDFs
   during import and store them as `Shape` nodes with `LayoutHint` metadata.
2. Render `Shape` nodes in the Layout view (`DocLens.render_layout`) as styled `<div>`
   or `<hr>` elements with appropriate positioning and dimensions.
3. Scope to "simple shapes" — lines and rectangles. Complex paths (curves, fills,
   gradients) are out of scope for V1.

## Context: What We Have

### Shape Node Type (already exists in `src/uaf/core/nodes.py`)

```python
@dataclass(frozen=True, slots=True)
class Shape:
    meta: NodeMetadata
    shape_type: str     # e.g. "line", "rect", "hrule"
    x: float
    y: float
    width: float
    height: float
```

Already registered in serialization (`_NODE_TYPE_NAME`, `node_to_dict`, `node_from_dict`).

### DocLens Supported Types

`DocLens._SUPPORTED` includes `NodeType.SHAPE` — but `_render_layout_node` and
`_render_node` do not handle `Shape` in their match arms yet.

### PyMuPDF Drawing Extraction

PyMuPDF provides `page.get_drawings()` which returns a list of drawing commands:

```python
for drawing in page.get_drawings():
    # drawing["rect"]  — bounding rectangle (x0, y0, x1, y1)
    # drawing["items"]  — list of (type, ...) tuples:
    #   ("l", p1, p2)        — line from p1 to p2
    #   ("re", rect)         — rectangle
    #   ("qu", quad)         — quad
    #   ("c", p1, p2, p3, p4) — cubic Bézier curve
    # drawing["color"]  — stroke color (r, g, b) or None
    # drawing["fill"]   — fill color (r, g, b) or None
    # drawing["width"]  — stroke width
```

## Implementation Plan

### Phase 1: Extract Shapes from PDF

**File: `src/uaf/app/formats/pdf_format.py`**

Add a new helper `_extract_shapes(page, page_num)` that:

1. Calls `page.get_drawings()`
2. Filters for simple shapes: lines (`"l"`) and rectangles (`"re"`)
3. Classifies each shape:
   - **Horizontal rule**: line or thin rectangle where `width >> height` and `height < 3pt`
   - **Vertical rule**: line or thin rectangle where `height >> width` and `width < 3pt`
   - **Rectangle**: everything else that's a `"re"` item
   - **Line**: everything else that's an `"l"` item
4. Creates `Shape` nodes with `LayoutHint` (page, x, y, width, height, color)
5. Creates `CONTAINS` edges from the Artifact to each Shape

In `import_file()`, after the text block loop, add:
```python
for page_num, page in enumerate(doc):
    shapes = _extract_shapes(page, page_num, block_index)
    for shape_node, edge in shapes:
        db.create_node(shape_node)
        db.create_edge(edge)
        block_index += 1
```

### Phase 2: Render Shapes in Layout View

**File: `src/uaf/app/lenses/doc_lens.py`**

Update `_render_layout_node` to handle `Shape` nodes:

```python
case Shape(meta=meta, shape_type=st, x=sx, y=sy, width=sw, height=sh):
    # Render as a positioned div with border/background
    ...
```

Shape rendering depends on type:
- `hrule` / `vrule`: `<div>` with solid border-bottom or border-left
- `rect`: `<div>` with border and optional background-fill
- `line`: `<div>` with appropriate CSS border

Also add `Shape` handling to `_get_text` (return `None` or empty — shapes have no text)
and `_get_node_id`.

**File: `src/uaf/app/static/style.css`**

Add `.layout-shape` class:
```css
.layout-shape {
    position: absolute;
    pointer-events: none;  /* Don't interfere with text selection */
}
.layout-shape-hrule {
    border-bottom: 1px solid;
}
```

### Phase 3: Tests

**`tests/uaf/app/test_format_handlers.py`** — `TestPdfHandler`:
- `test_extract_shapes_hrule`: Create PDF with horizontal line, verify Shape node
- `test_extract_shapes_rect`: Create PDF with rectangle, verify Shape node
- `test_shapes_have_layout_metadata`: Verify x/y/width/height on extracted shapes
- `test_shapes_skip_complex_paths`: Complex drawings (curves) are not imported

**`tests/uaf/app/test_doc_lens.py`** — `TestDocLensLayoutRender`:
- `test_render_layout_shape_hrule`: Shape with type="hrule" renders as styled div
- `test_render_layout_shape_rect`: Shape with type="rect" renders with border

**`tests/uaf/app/test_integration.py`** — `TestLayoutViewRoute`:
- `test_layout_view_includes_shapes`: Import PDF with line → layout HTML includes shape

### Phase 4: Verify with Real PDF

Import `2511.14823v1.pdf` and verify in Layout view:
- Horizontal rules above and below the title are visible
- Rules are positioned correctly relative to the title text
- Rules have appropriate color and thickness

## Scope Boundaries

**In scope:**
- Lines (horizontal and vertical rules)
- Rectangles (filled and stroked)
- Color extraction (stroke and fill)
- Stroke width

**Out of scope (future):**
- Complex paths (Bézier curves)
- Gradients and patterns
- Embedded vector graphics (SVG-like content)
- Text-on-path
- Clipping paths
- Transparency / blend modes

## Files to Modify

| File | Changes |
|------|---------|
| `src/uaf/app/formats/pdf_format.py` | Add `_extract_shapes()`, call from `import_file()` |
| `src/uaf/app/lenses/doc_lens.py` | Handle `Shape` in layout rendering + `_get_text`/`_get_node_id` |
| `src/uaf/app/static/style.css` | Add `.layout-shape` classes |
| `tests/uaf/app/test_format_handlers.py` | Add ~4 shape extraction tests |
| `tests/uaf/app/test_doc_lens.py` | Add ~2 shape rendering tests |
| `tests/uaf/app/test_integration.py` | Add ~1 integration test |

## Verification

```bash
make check   # All tests pass, zero lint/mypy errors
```

Then manually verify with the academic PDF in Layout view.
