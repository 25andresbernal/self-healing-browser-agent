from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

SITE_DIR = Path(__file__).resolve().parent.parent / "examples" / "site"


def site_url(name: str) -> str:
    return SITE_DIR.joinpath(name).resolve().as_uri()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    p = browser.new_page()
    yield p
    p.close()
