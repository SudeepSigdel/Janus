"""B1 accessible-name fallbacks (observer/extract.py::labelFor). Each fallback applies only
to an element that would otherwise have no name; existing names are unchanged (goldens)."""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from janus.config import Settings
from janus.observer.extract import extract_snapshot

pytestmark = pytest.mark.browser

HTML = """
<label><input type="radio" name="r" id="r1"> Option A</label>
<label for="t1">Has for</label><input id="t1" placeholder="ignored placeholder">
<input aria-labelledby="lb1 lb2"><span id="lb1">First</span><span id="lb2">Last</span>
<div role="tab">Tab #2</div>
<input type="submit" value="Send it">
<span role="treeitem" title="unused">Folder X</span>
<input title="Search box">
<label><input type="checkbox"> <button>inner</button> Keep me</label>
<input name="user_name"><input id="emailAddress">
"""


def _names() -> list[tuple[str, str]]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(HTML)
            snap = extract_snapshot(page, Settings())
        finally:
            browser.close()
    return [(e.role, e.accessible_name) for e in snap.elements]


def test_fallback_names() -> None:
    names = _names()
    assert ("radio", "Option A") in names
    assert ("textbox", "Has for") in names  # label[for] still wins over placeholder
    assert ("textbox", "First Last") in names  # aria-labelledby
    assert ("tab", "Tab #2") in names  # name from content
    assert ("button", "Send it") in names  # input[type=submit] value
    assert ("treeitem", "Folder X") in names
    assert ("textbox", "Search box") in names  # title, last resort
    assert ("checkbox", "Keep me") in names  # wrapping label minus nested control text
    assert ("textbox", "user name") in names  # humanized name attribute
    assert ("textbox", "email Address") in names  # humanized id
