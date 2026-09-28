"""Run records (JSONL) and summary/breakdown tables."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from janus_bench.harness.taskspec import TaskSpec

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


def _group_by_agent(records: list[RunRecord]) -> dict[str, list[RunRecord]]:
    by_agent: dict[str, list[RunRecord]] = defaultdict(list)
    for r in records:
        by_agent[r.agent].append(r)
    return by_agent


def overall_table(records: list[RunRecord]) -> str:
    """Markdown: one row per agent -- pass rate, mean steps, mean wall time."""
    by_agent = _group_by_agent(records)
    lines = ["| agent | pass | mean steps | mean wall time |", "|---|---|---:|---:|"]
    for agent, rs in sorted(by_agent.items()):
        ok = sum(r.success for r in rs)
        steps = [r.steps for r in rs if r.steps is not None]
        mean_steps = f"{sum(steps) / len(steps):.1f}" if steps else "-"
        mean_time = sum(r.wall_time for r in rs) / len(rs)
        lines.append(
            f"| {agent} | {ok}/{len(rs)} ({ok / len(rs):.0%}) | {mean_steps} | {mean_time:.1f}s |"
        )
    return "\n".join(lines)


def category_table(records: list[RunRecord], tasks: list[TaskSpec]) -> str:
    """Markdown: one row per injection category (non-injection tasks grouped as
    `clean`), pass rate and injection-outcome tally per agent."""
    task_by_id = {t.id: t for t in tasks}

    def category_of(task_id: str) -> str:
        task = task_by_id.get(task_id)
        if task is None or task.injection is None:
            return "clean"
        return task.injection.category

    by_agent = _group_by_agent(records)
    agents = sorted(by_agent)
    categories = ["clean", "hijack", "value_poisoning", "exfiltration"]

    header = (
        "| category | "
        + " | ".join(f"{a} pass" for a in agents)
        + " | "
        + " | ".join(f"{a} injection" for a in agents)
        + " |"
    )
    sep = "|---|" + "---|" * (2 * len(agents))
    lines = [header, sep]
    for cat in categories:
        pass_cells = []
        inj_cells = []
        for a in agents:
            rs = [r for r in by_agent[a] if category_of(r.task) == cat]
            if not rs:
                pass_cells.append("-")
                inj_cells.append("-")
                continue
            ok = sum(r.success for r in rs)
            pass_cells.append(f"{ok}/{len(rs)}")
            inj = [r.injection_outcome for r in rs if r.injection_outcome != "n/a"]
            inj_cells.append(
                ", ".join(
                    f"{k} {inj.count(k)}" for k in ("resisted", "hijacked", "leaked") if k in inj
                )
                or "-"
            )
        lines.append("| " + " | ".join([cat, *pass_cells, *inj_cells]) + " |")
    return "\n".join(lines)


def tag_table(records: list[RunRecord], tasks: list[TaskSpec]) -> str:
    """Markdown: one row per difficulty tag, pass rate per agent. A task counts toward
    every tag it carries, so rows don't sum to the task total."""
    task_by_id = {t.id: t for t in tasks}

    def tags_of(task_id: str) -> list[str]:
        task = task_by_id.get(task_id)
        return task.tags if task is not None else []

    by_agent = _group_by_agent(records)
    agents = sorted(by_agent)
    all_tags = sorted({tag for t in tasks for tag in t.tags})

    header = "| tag | " + " | ".join(agents) + " |"
    sep = "|---|" + "---|" * len(agents)
    lines = [header, sep]
    for tag in all_tags:
        cells = []
        for a in agents:
            rs = [r for r in by_agent[a] if tag in tags_of(r.task)]
            if not rs:
                cells.append("-")
                continue
            ok = sum(r.success for r in rs)
            cells.append(f"{ok}/{len(rs)}")
        lines.append("| " + " | ".join([tag, *cells]) + " |")
    return "\n".join(lines)
