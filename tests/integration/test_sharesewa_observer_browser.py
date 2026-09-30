"""Observer extraction against the live ShareSewa replica (needs Playwright Chromium).

One golden, for the login page: docs/PLAN.md Q3's `planner/examples.py::select_example`
selector is tested against it (`tests/unit/planner/test_examples.py`) to prove its
"login-shaped" predicate matches the real page, not a hand-typed guess at its shape.
Mirrors `test_observer_browser.py`'s nagarpalika pattern.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from janus.observer.extract import extract_snapshot
from janus.observer.snapshot import PageSnapshot
from janus_bench.harness.server import running_site

pytestmark = pytest.mark.browser

GOLDEN_DIR = Path(__file__).parent / "golden"


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    with running_site("sharesewa") as url:
        yield url


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture()
def page(browser: Browser) -> Iterator[Page]:
    page = browser.new_page()
    try:
        yield page
    finally:
        page.close()


def _load_golden(name: str) -> dict:
    return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


def _login_snapshot(page: Page, base_url: str) -> PageSnapshot:
    page.goto(f"{base_url}/login")
    return extract_snapshot(page)


def test_login_snapshot_matches_golden(page: Page, base_url: str) -> None:
    snapshot = _login_snapshot(page, base_url)
    golden = _load_golden("sharesewa_login.json")
    assert snapshot.model_dump(mode="json") == golden
