"""render_report: a run's trace becomes a standalone page that escapes everything."""

from __future__ import annotations

from janus.report import render_report

EVENTS = [
    {"stage": "snapshot", "data": {"url": "http://x/a", "title": "<b>Form</b>", "elements": 4}},
    {
        "stage": "plan_attempt",
        "data": {"ok": False, "errors": ["step 0: unknown element ref 'e9'"]},
    },
    {
        "stage": "authorize",
        "data": {"op": "CLICK", "label": "Next", "consequential": False, "allowed": True},
    },
    {"stage": "execute", "data": {"op": "CLICK", "label": "Next", "ok": True, "blocked": False}},
    {"stage": "verify", "data": {"op": "CLICK", "ok": True, "reason": ""}},
    {
        "stage": "authorize",
        "data": {
            "op": "CLICK",
            "label": "Cancel all",
            "consequential": True,
            "allowed": False,
            "reason": "CLICK is consequential and matches no remaining approval",
        },
    },
    {"stage": "run_result", "data": {"status": "partial"}},
]


def test_report_shows_decisions_and_escapes_page_strings() -> None:
    html = render_report(EVENTS, title="t", subtitle="<script>x</script>")
    assert "<script>" not in html and "&lt;b&gt;Form&lt;/b&gt;" in html
    assert "done and verified" in html
    assert "refused" in html and "matches no remaining approval" in html
    assert "rejected by the validator" in html
    assert "partial" in html


def test_empty_trace_still_renders() -> None:
    assert "No steps were recorded" in render_report([], title="t")
