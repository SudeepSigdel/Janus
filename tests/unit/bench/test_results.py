from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from janus_bench.harness.results import (
    RunRecord,
    append_record,
    category_table,
    load_records,
    overall_table,
    summary_table,
    tag_table,
)
from janus_bench.harness.taskspec import Check, Injection, Instruction, TaskSpec


def rec(
    task: str = "nag-01",
    ok: bool = True,
    inj: str = "n/a",
    steps: int | None = 4,
    agent: str = "a",
) -> RunRecord:
    return RunRecord(
        task=task,
        agent=agent,
        repeat=1,
        success=ok,
        steps=steps,
        wall_time=2.0,
        injection_outcome=inj,  # type: ignore[arg-type]
    )


def task(
    id: str = "nag-01", tags: list[str] | None = None, injection: Injection | None = None
) -> TaskSpec:
    return TaskSpec(
        id=id,
        site="nagarpalika",
        start_url="/",
        instruction=Instruction(en="x", ne="x"),
        tags=tags or [],
        success=[Check(path="a", op="equals", value=1)],
        injection=injection,
    )


def test_jsonl_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "r.jsonl"
    records = [rec(), rec("nag-05", False, "hijacked", None)]
    for r in records:
        append_record(path, r)
    assert load_records(path) == records


def test_unknown_field_and_bad_outcome_rejected() -> None:
    with pytest.raises(ValidationError):
        RunRecord.model_validate({**rec().model_dump(), "extra": 1})
    with pytest.raises(ValidationError):
        RunRecord.model_validate({**rec().model_dump(), "injection_outcome": "maybe"})


def test_summary_table_aggregates() -> None:
    table = summary_table(
        [rec(), rec(ok=False), rec("nag-05", False, "hijacked"), rec("nag-05", True, "resisted")]
    )
    lines = table.splitlines()
    assert any(line.startswith("nag-01") and "1/2" in line for line in lines)
    assert any(
        line.startswith("nag-05") and "hijacked 1" in line and "resisted 1" in line
        for line in lines
    )
    assert lines[-1].startswith("TOTAL") and "2/4" in lines[-1]


def test_overall_table_breaks_down_by_agent() -> None:
    records = [rec(agent="a", ok=True), rec(agent="b", ok=False)]
    lines = overall_table(records).splitlines()
    assert any(line.startswith("| a ") and "1/1" in line for line in lines)
    assert any(line.startswith("| b ") and "0/1" in line for line in lines)


def test_category_table_groups_clean_and_injection_by_agent() -> None:
    inj = Injection(variant="v", category="hijack", lang="ne", forbidden=[])
    tasks = [task("nag-01"), task("nag-05", injection=inj)]
    records = [
        rec(task="nag-01", agent="a", ok=True),
        rec(task="nag-05", agent="a", ok=True, inj="resisted"),
    ]
    lines = category_table(records, tasks).splitlines()
    assert any(line.startswith("| clean") and "1/1" in line for line in lines)
    assert any(line.startswith("| hijack") and "resisted 1" in line for line in lines)
    assert any(line.startswith("| value_poisoning") and line.count("-") >= 2 for line in lines)


def test_tag_table_counts_multi_tag_tasks_once_per_tag() -> None:
    tasks = [task("nag-01", tags=["bilingual", "numerals"]), task("nag-02", tags=["bilingual"])]
    records = [rec(task="nag-01", ok=True), rec(task="nag-02", ok=False)]
    lines = tag_table(records, tasks).splitlines()
    assert any(line.startswith("| bilingual") and "1/2" in line for line in lines)
    assert any(line.startswith("| numerals") and "1/1" in line for line in lines)
