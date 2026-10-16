"""B4a: links to the page that is already open are marked `current_page` (aria-current,
or an href equal to the current URL ignoring the fragment). Hash-only and javascript:
hrefs are not marked, since those usually open a menu or run a handler."""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from janus.config import Settings
from janus.observer.extract import extract_snapshot

pytestmark = pytest.mark.browser

URL = "http://janus.test/forums?sort=hot"
HTML = """
<nav>
  <a href="/forums?sort=hot">Forums</a>
  <a href="/forums?sort=hot#top">Forums top</a>
  <a href="/wiki" aria-current="page">Wiki</a>
  <a href="/wiki" aria-current="false">Wiki off</a>
  <a href="/f/books">books</a>
  <a href="/forums?sort=new">New</a>
  <a href="#">Menu</a>
  <a href="javascript:void(0)">Script</a>
</nav>
<button>Go</button>
"""


def test_links_to_the_open_page_are_marked_current() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route(
                "http://janus.test/**", lambda r: r.fulfill(body=HTML, content_type="text/html")
            )
            page.goto(URL)
            snap = extract_snapshot(page, Settings())
            current = {e.accessible_name: e.current_page for e in snap.elements}
            assert current == {
                "Forums": True,
                "Forums top": True,
                "Wiki": True,
                "Wiki off": False,
                "books": False,
                "New": False,
                "Menu": False,
                "Script": False,
                "Go": False,
            }
        finally:
            browser.close()
