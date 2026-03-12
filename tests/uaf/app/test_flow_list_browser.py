"""Browser-level tests for FlowLens list view keyboard/mouse interaction.

Uses Playwright to verify Tab navigation and click-to-focus actually work
in a real browser, catching issues that server-side HTML tests cannot.
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

    config = uvicorn.Config(app, host="127.0.0.1", port=8787, log_level="warning")
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
    yield "http://127.0.0.1:8787"
    proc.kill()
    proc.join(timeout=5)


def _active_info(page) -> str:  # type: ignore[no-untyped-def]
    """Return tag.class [row=R, col=C] of the active element."""
    return page.evaluate("""() => {
        const el = document.activeElement;
        const r = el.dataset ? el.dataset.row : '?';
        const c = el.dataset ? el.dataset.col : '?';
        return el.tagName + '.' + el.className + ' [row=' + r + ',col=' + c + ']';
    }""")


def _register_and_create_project(page, server_url: str) -> None:  # type: ignore[no-untyped-def]
    """Register a user and create a new project."""
    page.goto(f"{server_url}/register")
    page.fill('input[name="display_name"]', f"u_{time.monotonic_ns()}")
    page.fill('input[name="password"]', "testpass123")
    page.click('button[type="submit"]')
    page.wait_for_url(f"{server_url}/dashboard")
    page.click('button:has-text("New Project")')
    page.wait_for_selector("#flow-content", timeout=5000)


# ---------------------------------------------------------------------------
# Fixture: empty project in List view (user's exact scenario)
# ---------------------------------------------------------------------------
@pytest.fixture()
def empty_list_page(server_url: str):  # type: ignore[no-untyped-def]
    """Empty project in List view — only the new-row placeholder."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        js_errors: list[str] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))

        _register_and_create_project(page, server_url)
        page.click('button.view-mode-btn:has-text("List")')
        page.wait_for_selector("table.flow-list-grid", timeout=5000)
        page.wait_for_timeout(300)

        yield page, js_errors
        browser.close()


# ---------------------------------------------------------------------------
# Fixture: project with two tasks in List view
# ---------------------------------------------------------------------------
@pytest.fixture()
def list_page(server_url: str):  # type: ignore[no-untyped-def]
    """Project with two tasks displayed in List view."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        js_errors: list[str] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))

        _register_and_create_project(page, server_url)
        for title in ("Alpha", "Beta"):
            page.click('button:has-text("+ Task")')
            form = page.locator("#add-task-form")
            form.wait_for(state="visible")
            form.locator('input[name="title"]').fill(title)
            form.locator('input[name="start_date"]').fill("2025-06-01")
            form.locator('input[name="end_date"]').fill("2025-06-15")
            form.locator('button[type="submit"]').click()
            page.wait_for_timeout(500)

        page.click('button.view-mode-btn:has-text("List")')
        page.wait_for_selector("table.flow-list-grid", timeout=5000)
        page.wait_for_timeout(300)

        yield page, js_errors
        browser.close()


# ===================================================================
# New-row tests — the user's exact scenario
# ===================================================================
class TestNewRow:
    """New project → List view → type task → Tab / Enter."""

    def test_new_row_has_date_inputs(self, empty_list_page) -> None:  # type: ignore[no-untyped-def]
        """New-row placeholder should have Start and End date inputs."""
        page, _ = empty_list_page
        start = page.locator('.list-row-new input.list-cell-date[data-col="3"]')
        end = page.locator('.list-row-new input.list-cell-date[data-col="4"]')
        assert start.count() == 1, "New row should have a start-date input"
        assert end.count() == 1, "New row should have an end-date input"

    def test_tab_from_title_to_start_date(self, empty_list_page) -> None:  # type: ignore[no-untyped-def]
        """Tab from new-row title should focus the new-row start date."""
        page, js_errors = empty_list_page
        title = page.locator('.list-row-new input.list-cell-input[data-col="2"]')
        title.click()
        title.fill("My Task")
        page.wait_for_timeout(100)

        page.keyboard.press("Tab")
        page.wait_for_timeout(150)

        start = page.locator('.list-row-new input.list-cell-date[data-col="3"]')
        focused = start.evaluate("el => el === document.activeElement")
        assert focused, (
            f"Tab from new-row title → start date. Active: {_active_info(page)}. "
            f"JS errors: {js_errors}"
        )

    def test_tab_title_start_end(self, empty_list_page) -> None:  # type: ignore[no-untyped-def]
        """Tab twice from title should reach end date."""
        page, _ = empty_list_page
        title = page.locator('.list-row-new input.list-cell-input[data-col="2"]')
        title.click()
        page.wait_for_timeout(100)

        page.keyboard.press("Tab")
        page.wait_for_timeout(100)
        page.keyboard.press("Tab")
        page.wait_for_timeout(100)

        end = page.locator('.list-row-new input.list-cell-date[data-col="4"]')
        focused = end.evaluate("el => el === document.activeElement")
        assert focused, f"Two Tabs from title → end date. Active: {_active_info(page)}"

    def test_enter_creates_task(self, empty_list_page) -> None:  # type: ignore[no-untyped-def]
        """Enter in the new-row title input should create a task."""
        page, js_errors = empty_list_page
        title = page.locator('.list-row-new input.list-cell-input[data-col="2"]')
        title.click()
        title.fill("Created Task")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)

        tasks = page.locator("input.list-cell-input[data-action='update']")
        assert tasks.count() >= 1, (
            f"Enter should create a task. JS errors: {js_errors}"
        )
        assert "Created Task" in tasks.first.input_value()

    def test_enter_on_date_creates_task(self, empty_list_page) -> None:  # type: ignore[no-untyped-def]
        """Enter on a new-row date input should also create the task."""
        page, js_errors = empty_list_page
        title = page.locator('.list-row-new input.list-cell-input[data-col="2"]')
        title.click()
        title.fill("Date Enter Task")
        page.wait_for_timeout(100)

        # Tab to start date, then press Enter
        page.keyboard.press("Tab")
        page.wait_for_timeout(100)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)

        tasks = page.locator("input.list-cell-input[data-action='update']")
        assert tasks.count() >= 1, (
            f"Enter on date input should create task. JS errors: {js_errors}"
        )

    def test_enter_creates_then_focus_new_row(self, empty_list_page) -> None:  # type: ignore[no-untyped-def]
        """After Enter creates a task, focus should land on the new-row input."""
        page, _ = empty_list_page
        title = page.locator('.list-row-new input.list-cell-input[data-col="2"]')
        title.click()
        title.fill("Focus After Create")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)

        new_row = page.locator('input.list-cell-input[data-action="create"]')
        if new_row.count() > 0:
            focused = new_row.evaluate("el => el === document.activeElement")
            assert focused, (
                f"Focus should be on new-row after create. Active: {_active_info(page)}"
            )

    def test_no_js_errors(self, empty_list_page) -> None:  # type: ignore[no-untyped-def]
        """No JS errors during new-row interaction."""
        page, js_errors = empty_list_page
        title = page.locator('.list-row-new input.list-cell-input[data-col="2"]')
        title.click()
        title.fill("Test")
        page.keyboard.press("Tab")
        page.wait_for_timeout(100)
        page.keyboard.press("Tab")
        page.wait_for_timeout(100)
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        assert not js_errors, f"JS errors: {js_errors}"


# ===================================================================
# Existing task navigation
# ===================================================================
class TestExistingTasks:
    """Tab, click, arrow-key navigation on existing task rows."""

    def test_click_date_input(self, list_page) -> None:  # type: ignore[no-untyped-def]
        """Clicking a date input should focus it."""
        page, _ = list_page
        d = page.locator('input.list-cell-date[data-col="3"]').first
        d.click()
        page.wait_for_timeout(200)
        assert d.evaluate("el => el === document.activeElement"), (
            f"Click date → focus. Active: {_active_info(page)}"
        )

    def test_tab_title_to_start(self, list_page) -> None:  # type: ignore[no-untyped-def]
        page, _ = list_page
        page.locator('input.list-cell-input[data-col="2"]').first.click()
        page.wait_for_timeout(100)
        page.keyboard.press("Tab")
        page.wait_for_timeout(100)
        s = page.locator('input.list-cell-date[data-col="3"]').first
        assert s.evaluate("el => el === document.activeElement"), (
            f"Tab title→start. Active: {_active_info(page)}"
        )

    def test_tab_start_to_end(self, list_page) -> None:  # type: ignore[no-untyped-def]
        page, _ = list_page
        page.locator('input.list-cell-date[data-col="3"]').first.click()
        page.wait_for_timeout(100)
        page.keyboard.press("Tab")
        page.wait_for_timeout(100)
        e = page.locator('input.list-cell-date[data-col="4"]').first
        assert e.evaluate("el => el === document.activeElement"), (
            f"Tab start→end. Active: {_active_info(page)}"
        )

    def test_tab_end_wraps_to_next_row(self, list_page) -> None:  # type: ignore[no-untyped-def]
        page, _ = list_page
        page.locator('input.list-cell-date[data-row="0"][data-col="4"]').click()
        page.wait_for_timeout(100)
        page.keyboard.press("Tab")
        page.wait_for_timeout(100)
        t1 = page.locator('input.list-cell-input[data-row="1"][data-col="2"]')
        assert t1.evaluate("el => el === document.activeElement"), (
            f"Tab row0 end→row1 title. Active: {_active_info(page)}"
        )

    def test_shift_tab_reverses(self, list_page) -> None:  # type: ignore[no-untyped-def]
        page, _ = list_page
        page.locator('input.list-cell-date[data-col="3"]').first.click()
        page.wait_for_timeout(100)
        page.keyboard.press("Shift+Tab")
        page.wait_for_timeout(100)
        t = page.locator('input.list-cell-input[data-col="2"]').first
        assert t.evaluate("el => el === document.activeElement"), (
            f"Shift-Tab start→title. Active: {_active_info(page)}"
        )

    def test_arrow_down(self, list_page) -> None:  # type: ignore[no-untyped-def]
        page, _ = list_page
        page.locator('input.list-cell-input[data-row="0"]').click()
        page.wait_for_timeout(100)
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(100)
        t1 = page.locator('input.list-cell-input[data-row="1"]')
        assert t1.evaluate("el => el === document.activeElement")

    def test_date_not_obscured(self, list_page) -> None:  # type: ignore[no-untyped-def]
        """Date input center should be hittable (no CSS overlay)."""
        page, _ = list_page
        d = page.locator('input.list-cell-date[data-col="3"]').first
        box = d.bounding_box()
        assert box is not None
        tag = page.evaluate(
            f"document.elementFromPoint({box['x']+box['width']/2},"
            f"{box['y']+box['height']/2})?.tagName"
        )
        assert tag == "INPUT", f"Element at date center: {tag}"

    def test_title_input_has_padding(self, list_page) -> None:  # type: ignore[no-untyped-def]
        """Title input should have visible padding (CSS specificity check)."""
        page, _ = list_page
        padding = page.locator('input.list-cell-input[data-col="2"]').first.evaluate(
            "el => getComputedStyle(el).paddingLeft"
        )
        # Should be ~0.4rem (6.4px), NOT 0px from a broken reset
        px = float(padding.replace("px", ""))
        assert px > 2, f"Title input should have padding, got {padding}"
