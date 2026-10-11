"""B1c (docs/AUDIT.md S12): controls inside a hidden ancestor or with opacity 0 are not
observed, and act-time re-resolution walks the same filtered list."""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from janus.config import Settings
from janus.executor.resolve import resolve_element
from janus.observer.extract import extract_snapshot

pytestmark = pytest.mark.browser

HTML = """
<button>Visible</button>
<div class="menu" style="display:none"><button>Log out</button><a href="/x">Hidden link</a></div>
<div style="visibility:hidden"><button>Invisible</button></div>
<button style="opacity:0">Transparent</button>
<div style="opacity:0"><a href="/y">Inside transparent</a></div>
<a href="/z">Also visible</a>
"""


def test_hidden_ancestors_and_opacity_are_not_observed() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(HTML)
            snap = extract_snapshot(page, Settings())
            names = [e.accessible_name for e in snap.elements]
            assert names == ["Visible", "Also visible"]
            # re-resolution uses the same filtered ordering, so refs still resolve
            for element in snap.elements:
                assert resolve_element(page, element).inner_text() == element.accessible_name
        finally:
            browser.close()
