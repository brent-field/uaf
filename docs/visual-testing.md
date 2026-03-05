# Visual Fidelity Testing with Playwright

Visual fidelity tests use a headless browser (Chromium via Playwright) to render
layout HTML and verify computed CSS properties. They catch rendering regressions
that HTML string assertions miss — font styling, spacing, positioning, and CSS
interactions.

## Setup

```bash
uv sync                        # installs playwright Python package (dev dep)
playwright install chromium     # downloads headless Chromium (~150MB, one-time)
```

## Running visual tests

| Command | What it runs |
|---------|-------------|
| `make test` | All tests. Playwright tests **auto-skip** if Chromium is not installed. |
| `make test-visual` | Only Playwright tests (`pytest -m playwright -v`). |

Developers working on rendering should install Playwright and run
`make test-visual` before pushing. In CI environments without a browser,
`make test` works normally — Playwright tests are silently skipped via
`pytest.importorskip`.

## Writing new visual tests

All visual tests live in `tests/uaf/app/` and follow this pattern:

```python
import pytest
pw = pytest.importorskip("playwright.sync_api")  # auto-skip if not installed

from playwright.sync_api import sync_playwright
from tests.uaf.app._pdf_fidelity_helpers import _import_pdf

@pytest.mark.playwright
class TestMyVisualFeature:
    @pytest.fixture(autouse=True)
    def _setup(self):
        # 1. Import the PDF fixture (no auth, no server)
        db, root_id, _children = _import_pdf("my_fixture.pdf")

        # 2. Render layout HTML directly
        from uaf.app.lenses.doc_lens import DocLens
        from uaf.security.auth import LocalAuthProvider
        from uaf.security.secure_graph_db import SecureGraphDB

        auth = LocalAuthProvider()
        sdb = SecureGraphDB(db, auth)
        session = sdb.system_session()
        lens = DocLens()
        view = lens.render_layout(sdb, session, root_id)

        # 3. Wrap in full HTML document with style.css
        css = Path("src/uaf/app/static/style.css").read_text()
        html = f"<!DOCTYPE html><html><head><style>{css}</style></head>"
        html += f"<body><div class='layout-view'>{view.content}</div></body></html>"

        # 4. Load in headless browser
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content(html, wait_until="load")

            # 5. Query computed styles via page.evaluate()
            self.result = page.evaluate("() => { ... }")

            browser.close()

    def test_something(self):
        assert self.result["property"] == "expected"
```

Key points:

- **No auth, no server needed.** Call `DocLens().render_layout()` directly with
  `LocalAuthProvider()` and `system_session()`.
- **Use `page.set_content()`** to load HTML — no dev server required.
- **Assert on inline style declarations** (`element.style.fontFamily`), not
  resolved fonts from `getComputedStyle().fontFamily`. This avoids failures from
  cross-platform font availability differences.
- **Use `_find_block(children, "substring")`** to locate target blocks by
  distinctive text content.
- **Mark with `@pytest.mark.playwright`** so `make test-visual` picks them up.

## When to write visual tests

Write a Playwright test when:

- Font styling or font-family correctness matters (inline math, special symbols)
- Positioning or layout CSS interactions need verification
- A rendering bug was found visually that HTML string assertions couldn't catch

Don't use Playwright for:

- Pure data extraction tests (text content, node structure)
- Tests that can be expressed as HTML regex/string assertions
- Unit tests for individual functions

## CI guidance

If CI runs `make test`, Playwright tests skip harmlessly (no Chromium installed).
To enable them in CI, add to the setup step:

```yaml
- run: playwright install --with-deps chromium
```
