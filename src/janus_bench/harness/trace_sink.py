"""JSON trace sink for `janus.agent.run_task`'s optional `trace` callback.

One file per run (`<task_id>-<n>.json`), a JSON array of `{"stage": ..., "data": ...}`
events in emission order, rewritten after every event -- runs are short (single-digit
seconds) and the harness's own trace directories are gitignored, so this optimizes for
"never lose more than the current event on a crash" over write throughput.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


def make_trace_sink(path: Path) -> Callable[[str, dict[str, Any]], None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []

    def sink(stage: str, data: dict[str, Any]) -> None:
        events.append({"stage": stage, "data": data})
        text = json.dumps(events, ensure_ascii=False, default=str, indent=2)
        path.write_text(text, encoding="utf-8")

    return sink


def load_trace(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))
