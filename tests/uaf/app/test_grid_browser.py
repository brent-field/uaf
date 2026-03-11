"""Browser-level tests for spreadsheet mode.

Uses Playwright to verify dashboard creation, cell editing, and toolbar actions.
"""

from __future__ import annotations

import multiprocessing
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator


def _run_server(started: multiprocessing.Event) -> None:  # type: ignore[type-arg]
    """Run the demo server in a child process."""
    import uvicorn

    from uaf.app.api import create_app
    from uaf.app.lenses import LensRegistry
    from uaf.app.lenses.doc_lens import DocLens
    from uaf.app.lenses.flow_lens import FlowLens
    from uaf.app.lenses.grid_lens import GridLens
    from uaf.db.graph_db import GraphDB
    from uaf.security.auth import LocalAuthProvider
    from uaf.security.secure_graph_db import SecureGraphDB

    db = GraphDB()
    auth = LocalAuthProvider()
    sdb = SecureGraphDB(db, auth)
    registry = LensRegistry()
    registry.register(DocLens())
    registry.register(GridLens())
    registry.register(FlowLens())
    app = create_app(sdb, registry)

    config = uvicorn.Config(app, host="127.0.0.1", port=8788, log_level="warning")
    server = uvicorn.Server(config)

    _original_startup = server.startup

    async def _patched_startup(*a: object, **kw: object) -> None:
        await _original_startup(*a, **kw)  # type: ignore[arg-type]
        started.set()

    server.startup = _patched_startup  # type: ignore[assignment]
    server.run()


@pytest.fixture(scope="module")
def server_url() -> Generator[str]:
    """Start the demo server and yield its URL."""
    started = multiprocessing.Event()
    proc = multiprocessing.Process(target=_run_server, args=(started,), daemon=True)
    proc.start()
    if not started.wait(timeout=15):
        proc.kill()
        pytest.fail("Server did not start within 15 seconds")
    time.sleep(0.3)
    yield "http://127.0.0.1:8788"
    proc.kill()
    proc.join(timeout=5)


def _register_user(page, server_url: str) -> None:  # type: ignore[no-untyped-def]
    """Register a user and land on dashboard."""
    page.goto(f"{server_url}/register")
    page.fill('input[name="display_name"]', f"u_{time.monotonic_ns()}")
    page.fill('input[name="password"]', "testpass123")
    page.click('button[type="submit"]')
    page.wait_for_url(f"{server_url}/dashboard")


class TestCreateSpreadsheetFromDashboard:
    def test_create_spreadsheet_button_exists(self, server_url: str) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _register_user(page, server_url)

            btn = page.locator('button:has-text("New Spreadsheet")')
            assert btn.count() == 1
            browser.close()

    def test_create_spreadsheet_redirects_to_grid(self, server_url: str) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _register_user(page, server_url)

            page.click('button:has-text("New Spreadsheet")')
            page.wait_for_selector("#grid-content", timeout=5000)
            assert "/grid" in page.url
            browser.close()

    def test_grid_has_cells(self, server_url: str) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _register_user(page, server_url)

            page.click('button:has-text("New Spreadsheet")')
            page.wait_for_selector("#grid-content", timeout=5000)

            cells = page.locator("td[data-row][data-col]")
            assert cells.count() == 25  # 5x5 grid
            browser.close()


class TestCellClickInlineEdit:
    def test_click_cell_shows_inline_input(self, server_url: str) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _register_user(page, server_url)

            page.click('button:has-text("New Spreadsheet")')
            page.wait_for_selector("#grid-content", timeout=5000)

            # Click the first cell — should get selected (cell-selected class)
            page.locator("td[data-row][data-col]").first.click()
            page.wait_for_selector(".cell-inline-input", timeout=3000)

            inline_input = page.locator(".cell-inline-input")
            assert inline_input.count() == 1
            browser.close()


class TestCellNavigation:
    """After editing A1, user must be able to navigate to other cells."""

    def test_tab_from_cell_activates_next_cell(self, server_url: str) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _register_user(page, server_url)

            page.click('button:has-text("New Spreadsheet")')
            page.wait_for_selector("#grid-content", timeout=5000)

            # Click A1 (row=0, col=0) and type a value
            page.locator('td[data-row="0"][data-col="0"]').click()
            page.wait_for_selector(".cell-inline-input", timeout=3000)
            page.locator(".cell-inline-input").fill("hello")

            # Press Tab — should commit A1 and activate B1 (row=0, col=1)
            page.keyboard.press("Tab")
            page.wait_for_selector(
                'td[data-row="0"][data-col="1"] .cell-inline-input', timeout=5000,
            )
            active = page.locator(
                'td[data-row="0"][data-col="1"] .cell-inline-input',
            )
            assert active.count() == 1
            browser.close()

    def test_click_another_cell_after_editing(self, server_url: str) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _register_user(page, server_url)

            page.click('button:has-text("New Spreadsheet")')
            page.wait_for_selector("#grid-content", timeout=5000)

            # Click A1 and type a value
            page.locator('td[data-row="0"][data-col="0"]').click()
            page.wait_for_selector(".cell-inline-input", timeout=3000)
            page.locator(".cell-inline-input").fill("42")

            # Click B2 (row=1, col=1) — should commit A1 and activate B2
            page.locator('td[data-row="1"][data-col="1"]').click()
            page.wait_for_selector(
                'td[data-row="1"][data-col="1"] .cell-inline-input', timeout=5000,
            )
            active = page.locator(
                'td[data-row="1"][data-col="1"] .cell-inline-input',
            )
            assert active.count() == 1
            browser.close()


class TestAddAndDeleteRow:
    def test_add_row_increases_cells(self, server_url: str) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _register_user(page, server_url)

            page.click('button:has-text("New Spreadsheet")')
            page.wait_for_selector("#grid-content", timeout=5000)

            initial = page.locator("td[data-row][data-col]").count()

            page.click('button:has-text("+ Row")')
            page.wait_for_timeout(1000)

            after = page.locator("td[data-row][data-col]").count()
            assert after == initial + 5  # 5 cols per row
            browser.close()

    def test_grid_has_column_headers(self, server_url: str) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _register_user(page, server_url)

            page.click('button:has-text("New Spreadsheet")')
            page.wait_for_selector("#grid-content", timeout=5000)

            # Verify column headers (A, B, C...) exist
            headers = page.locator("thead th")
            assert headers.count() >= 2  # at least row-header + 1 column
            browser.close()
