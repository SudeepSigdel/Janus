"""`read_text` carries the own text of divs and spans (where many pages put their content) for
the quarantined READ; the planner-facing `untrusted_text` and the dumps are unchanged."""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from janus.observer.extract import extract_snapshot

pytestmark = pytest.mark.browser

HTML = """
<div id="q">What is <span>2 + 3</span> ?</div>
<textarea>copy me</textarea>
<script>var hidden = 'not text';</script>
<div style="display:none">invisible</div>
<button>Go</button>
"""


def test_read_text_includes_div_and_span_text_but_not_dumps() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(HTML)
            snap = extract_snapshot(page)
            joined = " | ".join(snap.read_text)
            assert "2 + 3" in joined and "copy me" in joined and "What is" in joined
            assert "invisible" not in joined and "not text" not in joined
            assert "read_text" not in snap.model_dump()
        finally:
            browser.close()
