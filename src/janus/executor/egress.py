"""Route-level egress allowlist (security invariant 6, CLAUDE.md/docs/PLAN.md).

`validate_plan` already checks a NAVIGATE step's target against the origin allowlist
at plan-commit time. This is the runtime backstop: every request the page itself
makes -- a redirect, a form post to a different origin, a script's fetch/XHR -- is
intercepted and aborted unless its origin is on the same allowlist, so a step the
validator never modeled can't exfiltrate anything either.
"""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import Page, Route

from janus.validator.policy import origin_of


def install_egress_guard(
    page: Page,
    allowed_origins: frozenset[str],
    on_block: Callable[[str], None] | None = None,
) -> None:
    def handle(route: Route) -> None:
        if origin_of(route.request.url) in allowed_origins:
            route.continue_()
            return
        if on_block is not None:
            on_block(route.request.url)
        route.abort()

    page.route("**/*", handle)
