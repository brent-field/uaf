"""Browser-level tests for document editor.

Uses Playwright to verify contenteditable editing, view switching, and save persistence.
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

    config = uvicorn.Config(app, host="127.0.0.1", port=8789, log_level="warning")
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
    yield "http://127.0.0.1:8789"
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

    # Click "+ Document"
    page.click('form[action="/artifacts/create"] button')
    page.wait_for_selector("#doc-content", timeout=5000)

    # Click "Add a block" (from the empty state) or "+ Block" (from toolbar)
    add_btn = page.locator('button:has-text("Add a block"), button:has-text("+ Block")')
    add_btn.first.click()
    # Wait for the block to appear
    page.wait_for_selector(".block-body", timeout=5000)


class TestDocEditorBasics:
    """Verify that the doc editor loads and blocks can be added."""

    def test_new_doc_shows_empty_state(self, server_url: str) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _register_user(page, server_url)

            page.click('form[action="/artifacts/create"] button')
            page.wait_for_selector("#doc-content", timeout=5000)

            assert page.locator(".empty-state").count() >= 1
            browser.close()

    def test_add_block_creates_contenteditable(self, server_url: str) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _create_doc_and_add_block(page, server_url)

            block = page.locator(".block-body")
            assert block.count() >= 1
            assert block.first.get_attribute("contenteditable") == "true"
            browser.close()


class TestContenteditableTyping:
    """Verify that typing in a contenteditable block actually works."""

    def test_typing_shows_text(self, server_url: str) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _create_doc_and_add_block(page, server_url)

            block = page.locator(".block-body").first
            block.click()
            page.keyboard.type("Hello world")

            assert "Hello world" in block.inner_text()
            browser.close()

    def test_typing_triggers_autosave(self, server_url: str) -> None:
        """Type text, wait for debounce, verify it was saved."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            network_reqs: list[str] = []
            page.on("request", lambda req: network_reqs.append(
                f"{req.method} {req.url}"
            ))

            _create_doc_and_add_block(page, server_url)

            block = page.locator(".block-body").first
            block.click()
            page.keyboard.type("Saved text")

            # Wait for debounce (500ms) + network round-trip
            page.wait_for_timeout(2000)

            # Check if update-text was called
            save_reqs = [r for r in network_reqs if "update-text" in r]
            assert len(save_reqs) >= 1, f"No update-text requests fired. All: {network_reqs}"

            # Reload to verify the text persisted on the server
            page.reload()
            page.wait_for_selector(".block-body", timeout=5000)

            body_text = page.locator(".block-body").first.inner_text()
            assert "Saved text" in body_text, f"Text not persisted! Got: {body_text!r}"
            browser.close()


class TestViewSwitchPersistence:
    """The core bug: text must survive Layout toggle → back to Semantic."""

    def test_text_survives_layout_roundtrip(self, server_url: str) -> None:
        """Type text, switch to Layout, switch back to Semantic — text must be there."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Capture console errors and network failures
            errors: list[str] = []
            page.on(
                "console",
                lambda msg: errors.append(msg.text) if msg.type == "error" else None,
            )

            _create_doc_and_add_block(page, server_url)

            # Type text into the block
            block = page.locator(".block-body").first
            block.click()
            page.keyboard.type("Persistent text")
            assert "Persistent text" in block.inner_text()

            # Click Layout view
            page.click("#btn-layout")
            page.wait_for_timeout(1000)

            # Click back to Semantic view
            page.click("#btn-semantic")
            page.wait_for_timeout(1000)

            # The text must be there
            block_after = page.locator(".block-body").first
            body_text = block_after.inner_text()

            # Dump diagnostics on failure
            if "Persistent text" not in body_text:
                print(f"BODY TEXT AFTER ROUNDTRIP: {body_text!r}")
                print(f"JS ERRORS: {errors}")
                print(f"PAGE HTML: {page.locator('#doc-content').inner_html()[:2000]}")

            assert "Persistent text" in body_text, (
                f"Text lost after view switch! Got: {body_text!r}, errors: {errors}"
            )
            browser.close()

    def test_text_survives_immediate_layout_switch(self, server_url: str) -> None:
        """Type text and IMMEDIATELY switch to Layout (no debounce time)."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            _create_doc_and_add_block(page, server_url)

            block = page.locator(".block-body").first
            block.click()
            page.keyboard.type("Quick switch")

            # Immediately switch — no waiting for debounce
            page.click("#btn-layout")
            page.wait_for_timeout(1000)

            page.click("#btn-semantic")
            page.wait_for_timeout(1000)

            block_after = page.locator(".block-body").first
            assert "Quick switch" in block_after.inner_text()
            browser.close()
