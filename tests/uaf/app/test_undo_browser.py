"""Browser-level tests for undo/redo functionality.

Uses Playwright to verify undo/redo buttons, keyboard shortcuts,
and that undo correctly reverts multi-operation actions.
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

    config = uvicorn.Config(app, host="127.0.0.1", port=8790, log_level="warning")
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
    yield "http://127.0.0.1:8790"
    proc.kill()
    proc.join(timeout=5)


def _register_user(page, server_url: str) -> None:  # type: ignore[no-untyped-def]
    """Register a user and land on dashboard."""
    page.goto(f"{server_url}/register")
    page.fill('input[name="display_name"]', f"u_{time.monotonic_ns()}")
    page.fill('input[name="password"]', "testpass123")
    page.click('button[type="submit"]')
    page.wait_for_url(f"{server_url}/dashboard")


def _create_doc_and_add_block(page, server_url: str) -> None:  # type: ignore[no-untyped-def]
    """Register, create a new doc, and add an empty block."""
    _register_user(page, server_url)

    # Click "+ New Document" on the dashboard
    page.click('.dashboard-header form[action="/artifacts/create"] button')
    page.wait_for_selector("#doc-content", timeout=5000)

    # New docs auto-create an empty paragraph; if not present add one
    if page.locator(".block-body").count() == 0:
        add_btn = page.locator('button:has-text("Add a block")')
        add_btn.first.click()
    # Wait for the block to appear
    page.wait_for_selector(".block-body", timeout=5000)


class TestUndoTextEdit:
    """Verify undo/redo for text editing operations."""

    def test_undo_reverts_typed_text(self, server_url: str) -> None:
        """Type text, wait for autosave, click Undo, verify text gone."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _create_doc_and_add_block(page, server_url)

            block = page.locator(".block-body").first
            block.click()
            page.keyboard.type("Hello world")
            assert "Hello world" in block.inner_text()

            # Wait for debounced autosave
            page.wait_for_timeout(2000)

            # Click Undo button
            undo_btn = page.locator('button:has-text("Undo")')
            undo_btn.click()
            page.wait_for_timeout(1000)

            # Verify text was reverted
            body_text = page.locator(".block-body").first.inner_text()
            assert "Hello world" not in body_text
            browser.close()

    def test_undo_button_exists(self, server_url: str) -> None:
        """Verify the Undo button is present in the toolbar."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _create_doc_and_add_block(page, server_url)

            undo_btn = page.locator('button:has-text("Undo")')
            assert undo_btn.count() >= 1
            browser.close()

    def test_redo_restores_text(self, server_url: str) -> None:
        """Type text, undo, then redo, verify text is back."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _create_doc_and_add_block(page, server_url)

            block = page.locator(".block-body").first
            block.click()
            page.keyboard.type("Redo me")

            # Wait for autosave
            page.wait_for_timeout(2000)

            # Undo
            page.locator('button:has-text("Undo")').click()
            page.wait_for_timeout(1000)
            body_text = page.locator(".block-body").first.inner_text()
            assert "Redo me" not in body_text

            # Redo
            page.locator('button:has-text("Redo")').click()
            page.wait_for_timeout(1000)
            body_text = page.locator(".block-body").first.inner_text()
            assert "Redo me" in body_text
            browser.close()


class TestUndoBlockInsert:
    """Verify undo for block insertion."""

    def test_undo_removes_inserted_block(self, server_url: str) -> None:
        """Insert a new block, click Undo, verify block count decreased."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _create_doc_and_add_block(page, server_url)

            initial_count = page.locator(".block-body").count()

            # Insert another block by pressing Enter at the end of the first block
            block = page.locator(".block-body").first
            block.click()
            page.keyboard.press("End")
            page.keyboard.press("Enter")
            page.wait_for_timeout(1000)

            after_insert_count = page.locator(".block-body").count()
            assert after_insert_count > initial_count

            # Undo should remove the inserted block
            page.locator('button:has-text("Undo")').click()
            page.wait_for_timeout(1000)

            after_undo_count = page.locator(".block-body").count()
            assert after_undo_count < after_insert_count
            browser.close()


class TestUndoKeyboardShortcut:
    """Verify Ctrl+Z triggers undo."""

    def test_ctrl_z_triggers_undo(self, server_url: str) -> None:
        """Type text, wait for save, press Ctrl+Z, verify text reverted."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _create_doc_and_add_block(page, server_url)

            block = page.locator(".block-body").first
            block.click()
            page.keyboard.type("Shortcut test")

            # Wait for autosave
            page.wait_for_timeout(2000)

            # Press Ctrl+Z (or Meta+Z on Mac)
            page.keyboard.press("Control+z")
            page.wait_for_timeout(1000)

            body_text = page.locator(".block-body").first.inner_text()
            assert "Shortcut test" not in body_text
            browser.close()
