"""Failing browser tests that demonstrate docs/AUDIT.md findings (no model).

Two throwaway local servers: a "portal" origin serving canned pages and a second origin
that only records which paths it was asked for. Every test asserts the intended property
and is `xfail(strict=True)`.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from janus.executor.egress import install_egress_guard
from janus.executor.executor import execute_step
from janus.executor.resolve import resolve_element
from janus.observer.extract import extract_snapshot
from janus.planner.ops import ClickStep, SelectStep

pytestmark = pytest.mark.browser

PORTAL_PORT, OTHER_PORT = 8311, 8398
PORTAL, OTHER = f"http://127.0.0.1:{PORTAL_PORT}", f"http://127.0.0.1:{OTHER_PORT}"


class _Site:
    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, dict[str, str], bytes]] = {}
        self.log: list[tuple[str, str]] = []


def _serve(port: int) -> tuple[_Site, http.server.ThreadingHTTPServer]:
    site = _Site()

    class Handler(http.server.BaseHTTPRequestHandler):
        def _any(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            site.log.append((self.command, self.path))
            status, headers, body = site.pages.get(
                self.path.split("?", 1)[0], (200, {"Content-Type": "text/html"}, b"<p>ok</p>")
            )
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _any

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return site, server


@pytest.fixture(scope="module")
def sites() -> Iterator[tuple[_Site, _Site]]:
    portal, s1 = _serve(PORTAL_PORT)
    other, s2 = _serve(OTHER_PORT)
    try:
        yield portal, other
    finally:
        for server in (s1, s2):
            server.shutdown()
            server.server_close()


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture()
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(service_workers="block")
    try:
        yield context.new_page()
    finally:
        context.close()


def _html(body: str) -> tuple[int, dict[str, str], bytes]:
    doc = f"<!doctype html><meta charset=utf-8><title>t</title><body>{body}</body>"
    return 200, {"Content-Type": "text/html; charset=utf-8"}, doc.encode()


def _open(page: Page, portal: _Site, body: str) -> None:
    portal.pages["/t"] = _html(body)
    install_egress_guard(page, frozenset({PORTAL}))
    page.goto(f"{PORTAL}/t")


# --- S2: egress guard is page-scoped ------------------------------------------------------

_EGRESS_CASES = {
    "link_target_blank": f"<a id=go target=_blank href='{OTHER}/blank'>go</a>",
    "window_open": f"<button id=go onclick=\"window.open('{OTHER}/popup')\">go</button>",
    "form_target_blank": (
        f"<form target=_blank method=post action='{OTHER}/form'><button id=go>go</button></form>"
    ),
    "websocket": f"<script>try{{new WebSocket('ws://127.0.0.1:{OTHER_PORT}/ws')}}catch(e){{}}</script>",
}


@pytest.mark.parametrize("case", sorted(_EGRESS_CASES))
def test_s2_egress_guard_covers_popups_and_websockets(
    page: Page, sites: tuple[_Site, _Site], case: str
) -> None:
    portal, other = sites
    other.log.clear()
    _open(page, portal, _EGRESS_CASES[case])
    if page.locator("#go").count():
        page.click("#go")
    page.wait_for_timeout(1500)
    assert other.log == []


def test_s2_egress_guard_covers_service_workers(page: Page, sites: tuple[_Site, _Site]) -> None:
    portal, other = sites
    other.log.clear()
    script = f"self.addEventListener('message',()=>fetch('{OTHER}/sw').catch(()=>{{}}));".encode()
    portal.pages["/sw.js"] = (200, {"Content-Type": "application/javascript"}, script)
    _open(
        page,
        portal,
        "<script>navigator.serviceWorker.register('/sw.js')"
        ".then(()=>navigator.serviceWorker.ready).then(r=>r.active.postMessage('go'))</script>",
    )
    page.wait_for_timeout(2000)
    assert other.log == []


# --- S9: a "commit" is only a main-frame POST navigation after CLICK/SUBMIT ---------------


@pytest.mark.xfail(
    strict=True, reason="AUDIT.md S9: a fetch()-based submit is not seen as a commit"
)
def test_s9_fetch_submit_is_detected_as_a_commit(page: Page, sites: tuple[_Site, _Site]) -> None:
    portal, _ = sites
    _open(
        page,
        portal,
        "<button onclick=\"fetch('/api/submit',{method:'POST'})\">Submit application</button>",
    )
    snapshot = extract_snapshot(page)
    outcome = execute_step(page, ClickStep(ref="e0"), snapshot, {})
    page.wait_for_timeout(500)
    assert ("POST", "/api/submit") in portal.log
    assert outcome.caused_post


@pytest.mark.xfail(
    strict=True, reason="AUDIT.md S9: a SELECT whose onchange submits is not seen as a commit"
)
def test_s9_select_onchange_submit_is_detected_as_a_commit(
    page: Page, sites: tuple[_Site, _Site]
) -> None:
    portal, _ = sites
    _open(
        page,
        portal,
        "<form method=post action=/save><select name=ward "
        "onchange='this.form.submit()'><option value=1>1</option><option value=2>2</option>"
        "</select></form>",
    )
    snapshot = extract_snapshot(page)
    outcome = execute_step(page, SelectStep(ref="e0", value="2"), snapshot, {})
    page.wait_for_timeout(1000)  # the onchange submit lands after execute_step returns
    assert ("POST", "/save") in portal.log
    assert outcome.caused_post


# --- S10: fingerprints -------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="AUDIT.md S10: rows 045 and ०४५ get identical fingerprints")
def test_s10_distinct_rows_never_share_a_fingerprint(
    page: Page, sites: tuple[_Site, _Site]
) -> None:
    portal, _ = sites
    _open(
        page,
        portal,
        "<table><tr><td>045</td><td><a href='/a/1'>Cancel</a></td></tr>"
        "<tr><td>०४५</td><td><a href='/a/2'>Cancel</a></td></tr></table>",
    )
    links = [e for e in extract_snapshot(page).elements if e.role == "link"]
    assert links[0].fingerprint != links[1].fingerprint


@pytest.mark.xfail(
    strict=True, reason="AUDIT.md S10: re-resolution picks an inserted same-fingerprint element"
)
def test_s10_inserted_lookalike_is_not_resolved_in_place_of_the_target(
    page: Page, sites: tuple[_Site, _Site]
) -> None:
    portal, _ = sites
    _open(page, portal, "<input name=q><button id=real>Next</button>")
    target = next(e for e in extract_snapshot(page).elements if e.role == "button")
    page.evaluate("""() => { const b = document.createElement('button');
        b.id = 'lookalike'; b.textContent = 'Next'; document.body.prepend(b); }""")
    handle = resolve_element(page, target)
    assert handle.get_attribute("id") == "real"


# --- S12: visibility filter ----------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT.md S12: invisible (opacity 0 / off-screen) controls reach the outline",
)
def test_s12_invisible_controls_are_not_in_the_outline(
    page: Page, sites: tuple[_Site, _Site]
) -> None:
    portal, _ = sites
    _open(
        page,
        portal,
        "<a href='/x' style='opacity:0'>hidden one</a>"
        "<a href='/y' style='position:absolute;left:-9999px'>hidden two</a><a href='/z'>shown</a>",
    )
    names = [e.accessible_name for e in extract_snapshot(page).elements]
    assert names == ["shown"]


# --- R1: an error response still counts as a commit -------------------------------------


@pytest.mark.xfail(
    strict=True, reason="AUDIT.md R1: a POST answered with HTTP 500 counts as a commit"
)
def test_r1_error_response_is_not_a_commit(page: Page, sites: tuple[_Site, _Site]) -> None:
    portal, _ = sites
    portal.pages["/boom"] = (500, {"Content-Type": "text/html"}, b"<h1>Internal error</h1>")
    _open(page, portal, "<form method=post action=/boom><button>Submit application</button></form>")
    snapshot = extract_snapshot(page)
    outcome = execute_step(page, ClickStep(ref="e0"), snapshot, {})
    assert ("POST", "/boom") in portal.log
    assert not outcome.caused_post
