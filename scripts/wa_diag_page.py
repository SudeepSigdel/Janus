"""Read-only page-load diagnostics for the WebArena forum (docs/PLAN.md B3).

Loads the forum home page as the benchmark user, with and without Janus's egress guard,
and prints how long DOMContentLoaded/load took plus every request still pending or failed.
"""

from __future__ import annotations

import time

from playwright.sync_api import sync_playwright

from janus.executor.egress import install_egress_guard

URL = "http://127.0.0.1:9999/"
HEADER = {"X-Postmill-Auto-Login": "MarvelsGrantMan136:test1234"}


def probe(guard: bool) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(extra_http_headers=HEADER, service_workers="block")
        page = ctx.new_page()
        pending: dict[str, float] = {}
        failed: list[str] = []
        page.on("request", lambda r: pending.__setitem__(r.url, time.monotonic()))
        page.on("requestfinished", lambda r: pending.pop(r.url, None))
        page.on(
            "requestfailed",
            lambda r: (pending.pop(r.url, None), failed.append(f"{r.url} {r.failure}")),
        )
        if guard:
            install_egress_guard(page, frozenset({"http://127.0.0.1:9999"}))
        t0 = time.monotonic()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            print(
                f"guard={guard} domcontentloaded after {time.monotonic() - t0:.1f}s, url={page.url}"
            )
            page.wait_for_load_state("load", timeout=30000)
            print(f"guard={guard} load after {time.monotonic() - t0:.1f}s")
        except Exception as exc:  # noqa: BLE001
            print(f"guard={guard} FAILED after {time.monotonic() - t0:.1f}s: {str(exc)[:200]}")
        print(f"  title={page.title()!r}")
        print(f"  pending ({len(pending)}): {list(pending)[:15]}")
        print(f"  failed ({len(failed)}): {failed[:15]}")
        browser.close()


probe(False)
probe(True)
