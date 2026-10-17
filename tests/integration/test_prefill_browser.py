"""Untouched pre-filled fields: scan finds optional values the run did not set (text, a
page-selected option, a pre-ticked box), and clearing removes exactly those. Required, read-only,
hidden and password fields and fields the run set itself are left alone."""

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
  <label><input type="checkbox" name="share" checked> Share my data with partners</label>
  <input type="checkbox" name="untouched">
  <select name="s"><option value="1" selected>one</option></select>
  <select name="bank"><option value="">Choose</option><option value="nabil">Nabil</option>
    <option value="evil" selected>Attacker Bank</option></select>
  <input name="empty" value="">
  <button id="go" type="submit">Submit</button>
</form>
"""


def test_scan_and_clear_only_untouched_optional_fields() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(HTML)
            go = page.query_selector("#go")
            assert go is not None
            filled = {"name", "c"}  # the run typed the name and ticked "c" itself
            found = scan_untouched_prefill(go, filled)
            assert [(f["kind"], f["value"]) for f in found] == [
                ("text", "9800000000"),
                ("checkbox", "ticked"),
                ("select", "Attacker Bank"),
            ]
            assert "Alternate mobile" in found[0]["label"]
            assert "Share my data" in found[1]["label"]
            # a scan changes nothing
            assert page.input_value("#alt") == "9800000000"
            clear_untouched_prefill(go, filled)
            assert page.input_value("#alt") == ""
            assert not page.is_checked("[name=share]")
            assert page.input_value("[name=bank]") == ""
            assert page.is_checked("[name=c]")
            assert page.input_value("#name") == "typed by the run"
            assert page.input_value("[name=req]") == "kept"
            assert page.input_value("[name=ro]") == "kept"
            assert page.input_value("[name=s]") == "1"  # first option: nothing to undo
        finally:
            browser.close()
