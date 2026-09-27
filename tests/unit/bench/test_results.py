from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from janus_bench.harness.results import RunRecord, append_record, load_records, summary_table


def rec(
    task: str = "nag-01", ok: bool = True, inj: str = "n/a", steps: int | None = 4
) -> RunRecord:
    return RunRecord(
        task=task,
        agent="a",
        repeat=1,
        success=ok,
        steps=steps,
        wall_time=2.0,
        injection_outcome=inj,  # type: ignore[arg-type]
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
