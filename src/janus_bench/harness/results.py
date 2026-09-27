"""Run records (JSONL) and the summary table."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

InjectionOutcome = Literal["n/a", "resisted", "hijacked", "leaked"]


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    agent: str
    repeat: int
    success: bool
    steps: int | None
    wall_time: float
    injection_outcome: InjectionOutcome
    error: str | None = None


def append_record(path: Path, record: RunRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(record.model_dump_json() + "\n")


def load_records(path: Path) -> list[RunRecord]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [RunRecord.model_validate_json(line) for line in lines if line.strip()]


def summary_table(records: list[RunRecord]) -> str:
    """Per-task rows plus a total row: success rate, mean steps, mean wall time, injection tally."""
    by_task: dict[str, list[RunRecord]] = defaultdict(list)
    for r in records:
        by_task[r.task].append(r)

    def row(label: str, rs: list[RunRecord]) -> str:
        steps = [r.steps for r in rs if r.steps is not None]
        mean_steps = f"{sum(steps) / len(steps):.1f}" if steps else "-"
        mean_time = sum(r.wall_time for r in rs) / len(rs)
        inj = [r.injection_outcome for r in rs if r.injection_outcome != "n/a"]
        inj_text = (
            ", ".join(f"{k} {inj.count(k)}" for k in ("resisted", "hijacked", "leaked") if k in inj)
            or "-"
        )
        ok = sum(r.success for r in rs)
        passed = f"{ok}/{len(rs)}"
        return f"{label:<10} {passed:<7} {mean_steps:>10} {mean_time:>9.1f}s  {inj_text}"

    header = f"{'task':<10} {'pass':<7} {'mean steps':>10} {'mean time':>10}  injection"
    lines = [header, *(row(t, rs) for t, rs in sorted(by_task.items())), row("TOTAL", records)]
    return "\n".join(lines)
