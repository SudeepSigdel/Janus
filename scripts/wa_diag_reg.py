"""Read-only diagnostics for the forum's registration page and header login (PLAN.md B4)."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:9999"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(f"{BASE}/", timeout=120000)  # warm-up
    page.goto(f"{BASE}/registration", wait_until="networkidle", timeout=60000)
    print("registration url:", page.url, "title:", page.title())
    for b in page.query_selector_all("button, input[type=submit]"):
        print(
            "  button:",
            repr((b.inner_text() or b.get_attribute("value") or "")[:40]),
            "visible:",
            b.is_visible(),
            "box:",
            b.bounding_box(),
        )
    form = page.query_selector("form")
    print("form html:", (form.inner_html() if form else page.content())[:1500])
    for header in ("MarvelsGrantMan136:test1234", "MarvelsGrantMan136:wrongpass"):
        ctx = browser.new_context(extra_http_headers={"X-Postmill-Auto-Login": header})
        pg = ctx.new_page()
        pg.goto(f"{BASE}/", timeout=60000)
        logged = "Log out" in pg.content() or "MarvelsGrantMan136" in pg.content()
        print(f"header {header.split(':')[1]!r}: logged in = {logged}")
        ctx.close()
    browser.close()
