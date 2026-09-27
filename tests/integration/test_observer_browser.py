"""Observer extraction against the live nagarpalika replica (needs Playwright Chromium)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from janus.config import get_settings
from janus.observer.extract import extract_snapshot
from janus.observer.snapshot import PageSnapshot
from janus_bench.harness.server import running_site
from janus_bench.sites.nagarpalika.seed import INJECTION_NOTICE_NE, INJECTION_NOTICE_VARIANT

pytestmark = pytest.mark.browser

GOLDEN_DIR = Path(__file__).parent / "golden"


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    with running_site("nagarpalika") as url:
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


def _reset(base_url: str, variant: str | None) -> None:
    httpx.post(f"{base_url}/__bench/reset", json={"variant": variant}, timeout=5).raise_for_status()


def _load_golden(name: str) -> dict:
    return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


def _services_snapshot(page: Page, base_url: str) -> PageSnapshot:
    _reset(base_url, INJECTION_NOTICE_VARIANT)
    page.goto(f"{base_url}/services")
    return extract_snapshot(page)


def _form_snapshot(page: Page, base_url: str) -> PageSnapshot:
    _reset(base_url, None)
    page.goto(f"{base_url}/apply/residence-recommendation")
    return extract_snapshot(page)


def test_services_snapshot_matches_golden(page: Page, base_url: str) -> None:
    snapshot = _services_snapshot(page, base_url)
    golden = _load_golden("nagarpalika_services.json")
    assert snapshot.model_dump(mode="json") == golden


def test_form_snapshot_matches_golden(page: Page, base_url: str) -> None:
    snapshot = _form_snapshot(page, base_url)
    golden = _load_golden("nagarpalika_form.json")
    assert snapshot.model_dump(mode="json") == golden


def test_injection_text_appears_only_in_untrusted_text(page: Page, base_url: str) -> None:
    snapshot = _services_snapshot(page, base_url)
    assert any(INJECTION_NOTICE_NE in block for block in snapshot.untrusted_text)
    for element in snapshot.elements:
        assert INJECTION_NOTICE_NE not in element.accessible_name
        assert element.name_attr is None or INJECTION_NOTICE_NE not in element.name_attr
        assert element.form_id is None or INJECTION_NOTICE_NE not in element.form_id


def _assert_under_caps(snapshot: PageSnapshot) -> None:
    settings = get_settings()
    assert not snapshot.elements_truncated
    assert not snapshot.untrusted_text_truncated
    assert len(snapshot.elements) <= settings.observer_max_elements
    assert (
        sum(len(block) for block in snapshot.untrusted_text)
        <= settings.observer_max_untrusted_chars
    )
    assert all(
        len(e.accessible_name) <= settings.observer_max_label_chars for e in snapshot.elements
    )


def test_services_snapshot_is_under_caps(page: Page, base_url: str) -> None:
    _assert_under_caps(_services_snapshot(page, base_url))


def test_form_snapshot_is_under_caps(page: Page, base_url: str) -> None:
    _assert_under_caps(_form_snapshot(page, base_url))
