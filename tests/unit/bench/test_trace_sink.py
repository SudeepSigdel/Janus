from __future__ import annotations

from pathlib import Path

from janus_bench.harness.trace_sink import load_trace, make_trace_sink


def test_sink_appends_events_in_order(tmp_path: Path) -> None:
    path = tmp_path / "traces" / "nag-01-1.json"
    sink = make_trace_sink(path)
    sink("snapshot", {"leg": 0})
    sink("run_result", {"status": "completed"})
    events = load_trace(path)
    assert [e["stage"] for e in events] == ["snapshot", "run_result"]
    assert events[0]["data"] == {"leg": 0}
    assert events[1]["data"] == {"status": "completed"}


def test_sink_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deep" / "x.json"
    make_trace_sink(path)("stage", {})
    assert path.exists()


def test_sink_handles_non_json_native_values(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    sink = make_trace_sink(path)
    sink("plan_attempt", {"capabilities": frozenset({("CLICK", "http://x", None)})})
    events = load_trace(path)
    assert events[0]["data"]["capabilities"] == str(frozenset({("CLICK", "http://x", None)}))
