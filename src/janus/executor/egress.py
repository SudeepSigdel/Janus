"""Context-level egress allowlist (security invariant 6, CLAUDE.md/docs/PLAN.md).

`validate_plan` already checks a NAVIGATE step's target against the origin allowlist
at plan-commit time. This is the runtime backstop: every request the browser makes
for this page's context -- a redirect, a form post to a different origin, a script's
fetch/XHR, a popup or `target=_blank` tab, a WebSocket -- is intercepted and refused
unless its origin is on the same allowlist (docs/AUDIT.md S2). Paths under
`DENIED_PATH_PREFIXES` (the benchmark's `/__bench/*` oracle endpoints, S7) are
refused even on an allowed origin.

Service workers cannot be intercepted from a context route, so contexts the runtime
creates should also pass `service_workers="block"`; as defense in depth this guard
additionally makes `navigator.serviceWorker.register` reject.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, WebSocketRoute

from janus.validator.policy import DENIED_PATH_PREFIXES, is_denied_path

_BLOCK_SERVICE_WORKERS = """
(() => {
  try {
    if (navigator.serviceWorker) {
      navigator.serviceWorker.register = () =>
        Promise.reject(new DOMException('blocked by egress guard', 'SecurityError'));
    }
  } catch (e) {}
})();
"""


def is_url_allowed(
    url: str,
    allowed_origins: frozenset[str],
    denied_path_prefixes: tuple[str, ...] = DENIED_PATH_PREFIXES,
) -> bool:
    """WebSocket URLs are compared by their http(s) equivalent (ws -> http, wss -> https)."""
    parsed = urlparse(url)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    if f"{scheme}://{parsed.netloc}" not in allowed_origins:
        return False
    return not is_denied_path(parsed.path, denied_path_prefixes)


def install_egress_guard(
    page: Page,
    allowed_origins: frozenset[str],
    on_block: Callable[[str], None] | None = None,
) -> None:
    context = page.context

    def handle(route: Route) -> None:
        if is_url_allowed(route.request.url, allowed_origins):
            route.continue_()
            return
        if on_block is not None:
            on_block(route.request.url)
        route.abort()

    def is_blocked_ws(url: str) -> bool:
        return not is_url_allowed(url, allowed_origins)

    def handle_ws(ws: WebSocketRoute) -> None:
        # Never calling `connect_to_server` keeps the socket fully mocked: the server is
        # never contacted. (A sync `ws.close()` here deadlocks Playwright's event loop.)
        if on_block is not None:
            on_block(ws.url)

    context.route("**/*", handle)
    context.route_web_socket(is_blocked_ws, handle_ws)
    context.add_init_script(_BLOCK_SERVICE_WORKERS)
