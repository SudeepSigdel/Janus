"""Self-contained HTML run report built from `run_task`'s `trace` events.

Shows what the committed plan was allowed to do and why anything was refused: per page, the
planner's attempts, each authorization decision, the execution result and the verifier check.
It only reads trace events; it never influences a gate. Page text is not in the trace, so it
cannot appear here either.
"""

from __future__ import annotations

from html import escape
from typing import Any

Event = dict[str, Any]

_CSS = """
:root{--bg:#fff;--fg:#1b1f24;--mut:#667;--ok:#17803d;--bad:#b42318;--warn:#a15c00;
--card:#f6f7f9;--line:#dde1e6}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--fg:#e8eaed;--mut:#9aa3ad;
--ok:#4ade80;--bad:#f87171;--warn:#fbbf24;--card:#1e2328;--line:#2f363d}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px}
h1{font-size:1.3rem;margin:0 0 4px}h2{font-size:1rem;margin:0}
.sub{color:var(--mut);margin:0 0 16px}
.badge{display:inline-block;padding:2px 10px;border-radius:99px;font-weight:600;font-size:.85rem;
border:1px solid currentColor}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.mut{color:var(--mut)}
.page{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;
margin:12px 0}
.page header{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
.url{font-family:ui-monospace,monospace;font-size:.8rem;color:var(--mut);word-break:break-all}
ul{list-style:none;padding:0;margin:8px 0 0}
li{padding:5px 0;border-top:1px solid var(--line);display:flex;gap:8px;flex-wrap:wrap}
.op{font-family:ui-monospace,monospace;font-weight:600;min-width:5.5em}
.stats{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0}
.stats div{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 12px}
.stats b{display:block;font-size:1.2rem}
"""


def _stats(events: list[Event]) -> dict[str, int]:
    count = {"steps": 0, "allowed": 0, "refused": 0, "retries": 0, "pages": 0}
    for event in events:
        stage, data = event["stage"], event["data"]
        if stage == "snapshot":
            count["pages"] += 1
        elif stage == "authorize":
            count["allowed" if data.get("allowed") else "refused"] += 1
        elif stage == "execute":
            count["steps"] += 1
        elif stage == "plan_attempt" and not data.get("ok"):
            count["retries"] += 1
        elif stage == "blocked":
            count["refused"] += 1
    return count


def _step_line(data: Event, executed: Event | None, verified: Event | None) -> str:
    label = escape(str(data.get("label") or ""))
    flag = ' <span class="warn">consequential</span>' if data.get("consequential") else ""
    if not data.get("allowed"):
        verdict = (
            f'<span class="bad">refused</span> <span class="mut">'
            f"{escape(str(data.get('reason', '')))}</span>"
        )
    elif executed is None:
        verdict = '<span class="mut">authorized</span>'
    elif executed.get("blocked"):
        verdict = (
            f'<span class="bad">blocked at act time</span> <span class="mut">'
            f"{escape(str(executed.get('reason', '')))}</span>"
        )
    elif not executed.get("ok"):
        verdict = f'<span class="bad">failed</span> {escape(str(executed.get("reason", "")))}'
    elif verified is not None and not verified.get("ok"):
        verdict = f'<span class="warn">done, check failed: {escape(str(verified["reason"]))}</span>'
    else:
        verdict = '<span class="ok">done and verified</span>'
    return (
        f'<li><span class="op">{escape(str(data.get("op")))}</span><span>{label}{flag}</span>'
        f"<span>{verdict}</span></li>"
    )


def render_report(
    events: list[Event], *, title: str, subtitle: str = "", status: str | None = None
) -> str:
    """One standalone HTML document for a run."""
    final = next((e["data"] for e in reversed(events) if e["stage"] == "run_result"), {})
    status = status or str(final.get("status", "unknown"))
    tone = {"completed": "ok", "partial": "warn"}.get(status, "bad")
    stats = _stats(events)

    pages: list[str] = []
    items: list[str] = []
    head = ""

    def close_page() -> None:
        if head:
            pages.append(f'<section class="page">{head}<ul>{"".join(items)}</ul></section>')

    index = 0
    while index < len(events):
        stage, data = events[index]["stage"], events[index]["data"]
        if stage == "snapshot":
            close_page()
            items = []
            head = (
                f"<header><h2>{escape(str(data.get('title') or 'page'))}</h2>"
                f'<span class="mut">{data.get("elements", 0)} elements seen</span></header>'
                f'<div class="url">{escape(str(data.get("url")))}</div>'
            )
        elif stage == "plan_attempt" and not data.get("ok"):
            errors = "; ".join(escape(str(e)) for e in data.get("errors", []))
            reason = errors or "output was not valid JSON for the plan schema"
            items.append(
                f'<li><span class="op">plan</span><span class="warn">rejected by the validator, '
                f'planner asked to retry</span><span class="mut">{reason}</span></li>'
            )
        elif stage == "loop_guard":
            items.append(
                f'<li><span class="op">guard</span><span class="warn">'
                f"{escape(str(data.get('label')))} did nothing last time; not offered again"
                "</span></li>"
            )
        elif stage == "plan_truncated":
            items.append(
                '<li><span class="op">plan</span><span class="warn">page changed; remaining '
                f"steps dropped and replanned: {escape(', '.join(data.get('dropped', [])))}"
                "</span></li>"
            )
        elif stage == "authorize":
            nxt = events[index + 1] if index + 1 < len(events) else None
            executed = nxt["data"] if nxt and nxt["stage"] == "execute" else None
            after = events[index + 2] if index + 2 < len(events) else None
            verified = after["data"] if after and after["stage"] == "verify" else None
            items.append(_step_line(data, executed, verified))
        elif stage == "blocked":
            detail = data.get("errors") or [data.get("error", "")]
            items.append(
                f'<li><span class="op">stop</span><span class="bad">blocked at '
                f"{escape(str(data.get('gate')))}</span>"
                f'<span class="mut">{escape("; ".join(str(d) for d in detail))}</span></li>'
            )
        index += 1
    close_page()

    summary = "".join(
        f"<div><b>{value}</b>{name}</div>"
        for name, value in (
            ("pages", stats["pages"]),
            ("actions run", stats["steps"]),
            ("actions refused", stats["refused"]),
            ("plan retries", stats["retries"]),
        )
    )
    body = "".join(pages) or '<p class="mut">No steps were recorded.</p>'
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><main>"
        f'<h1>{escape(title)} <span class="badge {tone}">{escape(status)}</span></h1>'
        f'<p class="sub">{escape(subtitle)}</p><div class="stats">{summary}</div>{body}'
        "</main></body></html>"
    )
