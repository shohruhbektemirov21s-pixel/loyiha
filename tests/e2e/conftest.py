"""Playwright fixtures for E2E tests.

Requires:
    pip install playwright pytest-playwright
    playwright install chromium

Environment:
    XRAY_E2E_BASE_URL    — base URL of the running console (default http://localhost:5173)
    XRAY_E2E_MOCK_MODE   — "true" to run console in VITE_MOCK=true mode (default true)
    XRAY_E2E_HEADLESS    — "false" to watch the browser (default true)
    XRAY_E2E_SLOW_MO_MS  — milliseconds to slow Playwright (default 0)

Run:
    # Mock mode (no API server required):
    pytest tests/e2e/ -v -m e2e

    # Against a live stack:
    XRAY_E2E_BASE_URL=https://192.168.10.100 XRAY_E2E_MOCK_MODE=false \
    pytest tests/e2e/ -v -m e2e
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

BASE_URL    = os.environ.get("XRAY_E2E_BASE_URL",   "http://localhost:5173")
MOCK_MODE   = os.environ.get("XRAY_E2E_MOCK_MODE",  "true").lower() == "true"
HEADLESS    = os.environ.get("XRAY_E2E_HEADLESS",   "true").lower() == "true"
SLOW_MO_MS  = int(os.environ.get("XRAY_E2E_SLOW_MO_MS", "0"))

E2E_AVAILABLE = os.environ.get("XRAY_E2E_ENABLED", "false").lower() == "true"

requires_e2e = pytest.mark.skipif(
    not E2E_AVAILABLE,
    reason="XRAY_E2E_ENABLED not set — skipping E2E browser tests",
)


@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance: Playwright) -> Browser:
    b = playwright_instance.chromium.launch(
        headless=HEADLESS,
        slow_mo=SLOW_MO_MS,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    yield b
    b.close()


@pytest.fixture
def context(browser: Browser) -> BrowserContext:
    ctx = browser.new_context(
        base_url=BASE_URL,
        ignore_https_errors=True,       # self-signed cert in on-prem deployment
        viewport={"width": 1440, "height": 900},
        locale="uz-Latn-UZ",
    )
    yield ctx
    ctx.close()


@pytest.fixture
def page(context: BrowserContext) -> Page:
    p = context.new_page()
    yield p
    p.close()


@pytest.fixture
def console_errors(page: Page) -> list[str]:
    """Collect browser console errors during a test."""
    errors: list[str] = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    return errors
