"""Untouched pre-filled fields: scan finds optional text defaults the run did not set, and
clearing empties exactly those. Required, read-only, hidden, select and checkbox fields and
fields the run filled are left alone."""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from janus.executor.prefill import clear_untouched_prefill, scan_untouched_prefill

pytestmark = pytest.mark.browser

HTML = """
<form id="f">
  <label for="name">Full name</label><input id="name" name="name" value="typed by the run">
  <label for="alt">Alternate mobile (optional)</label><input id="alt" name="alt" value="9800000000">
  <input name="req" required value="kept">
  <input name="ro" readonly value="kept">
  <input type="hidden" name="h" value="kept">
  <input type="password" name="pw" value="kept">
  <input type="checkbox" name="c" checked>
  <select name="s"><option value="1" selected>one</option></select>
  <input name="empty" value="">
  <button id="go" type="submit">Submit</button>
</form>
"""


def test_scan_and_clear_only_untouched_optional_text_fields() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(HTML)
            go = page.query_selector("#go")
            assert go is not None
            found = scan_untouched_prefill(go, {"name"})
            assert [f["value"] for f in found] == ["9800000000"]
            assert "Alternate mobile" in found[0]["label"]
            # a scan changes nothing
            assert page.input_value("#alt") == "9800000000"
            clear_untouched_prefill(go, {"name"})
            assert page.input_value("#alt") == ""
            assert page.input_value("#name") == "typed by the run"
            assert page.input_value("[name=req]") == "kept"
            assert page.input_value("[name=ro]") == "kept"
        finally:
            browser.close()
