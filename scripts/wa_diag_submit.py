"""Diagnostics for the forum's submit page as a newly registered user (PLAN.md B4).
Registers a throwaway user on the (benchmark) forum and inspects /submit. Changes only the
forum container's data, which every task resets anyway."""

from __future__ import annotations

import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:9999"
USER = f"diag_user_{int(time.time())}"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(f"{BASE}/registration", wait_until="networkidle", timeout=120000)
    page.fill("input#user_username", USER)
    page.fill("input#user_password_first", "test12345")
    page.fill("input#user_password_second", "test12345")
    with page.expect_navigation(timeout=60000):
        page.click('button:has-text("Sign up")')
    print("after sign-up:", page.url)
    ctx = browser.new_context(extra_http_headers={"X-Postmill-Auto-Login": f"{USER}:test12345"})
    pg = ctx.new_page()
    pg.goto(f"{BASE}/submit", wait_until="networkidle", timeout=60000)
    print("submit url:", pg.url, "title:", pg.title())
    for b in pg.query_selector_all("button"):
        txt = (b.inner_text() or "").strip()[:40]
        if txt:
            print(
                f"  button {txt!r} visible={b.is_visible()} enabled={b.is_enabled()} "
                f"box={b.bounding_box()}"
            )
    for sel in ("#submission_title", "#submission_body", "#submission_forum", "#submission_url"):
        el = pg.query_selector(sel)
        print(f"  {sel}: present={el is not None} visible={el.is_visible() if el else None}")
    notice = pg.query_selector(".flash, .alert, .notice")
    print("notice:", notice.inner_text()[:300] if notice else None)
    browser.close()
