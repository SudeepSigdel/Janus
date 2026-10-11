"""What Janus's observer sees on a forum post page, plus the raw vote/comment controls
(docs/PLAN.md B4 diagnostics). Read-only apart from page views."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from janus.config import Settings
from janus.observer.extract import extract_snapshot

URL = "http://127.0.0.1:9999/f/personalfinance/130944"
HEADER = {"X-Postmill-Auto-Login": "MarvelsGrantMan136:test1234"}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_context(extra_http_headers=HEADER).new_page()
    page.goto(URL, timeout=180000)
    snap = extract_snapshot(page, Settings())
    for e in snap.elements:
        print(f"{e.ref:<4} {e.role:<10} {e.tag:<8} name_attr={e.name_attr!s:<20} {e.accessible_name!r}")
    print("---- raw vote controls")
    for el in page.query_selector_all(".submission__vote *, .vote *, [class*=vote]")[:25]:
        print(el.evaluate("e => e.outerHTML.slice(0, 260)"))
    browser.close()
