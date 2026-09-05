"""Playwright browser setup. Identical behaviour on macOS and Windows."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import Settings

log = logging.getLogger("implementer.browser")

VIEWPORT = {"width": 1600, "height": 1000}


@contextmanager
def open_page(settings: Settings, profile_dir: Path | None = None):
    """Yield a ready-to-drive Page.

    With `profile_dir` the browser keeps cookies between runs, so a session
    that is still valid skips the login step.
    """
    with sync_playwright() as playwright:
        launch_args = {
            "headless": settings.headless,
            "slow_mo": settings.slow_mo_ms,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if profile_dir:
            profile_dir.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir), viewport=VIEWPORT, **launch_args
            )
            browser = None
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = playwright.chromium.launch(**launch_args)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()

        context.set_default_timeout(settings.timeout_ms)
        _wire_diagnostics(page)

        try:
            yield page
        finally:
            try:
                context.close()
            finally:
                if browser:
                    browser.close()


def _wire_diagnostics(page) -> None:
    # The CPPT 'Ubah' button asks for confirmation with a native confirm().
    page.on("dialog", _accept_dialog)
    page.on("console", _log_console)
    page.on("pageerror", lambda exc: log.debug("page error: %s", exc))


def _accept_dialog(dialog) -> None:
    log.info("browser dialog (%s): %s -> OK", dialog.type, dialog.message)
    try:
        dialog.accept()
    except Exception:  # the page may have navigated away already
        log.debug("dialog could not be accepted", exc_info=True)


def _log_console(message) -> None:
    if message.type in ("error", "warning"):
        log.debug("console %s: %s", message.type, message.text)
