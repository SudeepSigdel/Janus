from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from janus_bench.harness.checks import evaluate, resolve
from janus_bench.harness.taskspec import Check, TaskSpec, load_tasks

TASKS = Path(__file__).resolve().parents[3] / "tasks" / "nagarpalika"


def test_all_pilot_tasks_load() -> None:
    tasks = load_tasks(TASKS)
    assert [t.id for t in tasks] == [f"nag-{i:02d}" for i in range(1, 11)]
    injected = [t for t in tasks if t.injection]
    assert [t.id for t in injected] == [f"nag-{i:02d}" for i in range(5, 11)]
    categories = {t.id: t.injection.category for t in injected}
    assert categories == {
        "nag-05": "hijack",
        "nag-06": "hijack",
        "nag-07": "value_poisoning",
        "nag-08": "value_poisoning",
        "nag-09": "exfiltration",
        "nag-10": "exfiltration",
    }


def test_all_sharesewa_tasks_load() -> None:
    tasks = load_tasks(TASKS.parent / "sharesewa")
    assert [t.id for t in tasks] == [f"share-{i:02d}" for i in range(1, 17)]
    injected = [t for t in tasks if t.injection]
    assert [t.id for t in injected] == [f"share-{i:02d}" for i in range(11, 17)]
    apply_tasks = [t for t in tasks if "apply_issue" in t.approvals]
    assert len(apply_tasks) == 11
    assert all(t.sensitive_field_names == ["pin"] for t in apply_tasks)


def test_taskspec_forbids_extra_fields() -> None:
    base = {
        "id": "x",
        "site": "s",
        "start_url": "http://x",
        "instruction": {"en": "a", "ne": "b"},
        "success": [{"path": "a", "op": "equals", "value": 1}],
    }
    TaskSpec.model_validate(base)
    with pytest.raises(ValidationError):
        TaskSpec.model_validate({**base, "surprise": 1})
    with pytest.raises(ValidationError):
        TaskSpec.model_validate({**base, "success": [{"path": "a", "op": "matches", "value": 1}]})


STATE = {"apps": {"045": {"status": "cancelled", "tags": ["a", "b"]}}, "rows": [{"n": 1}, {"n": 2}]}


def test_resolve_walks_dicts_and_lists() -> None:
    assert resolve(STATE, "apps.045.status") == "cancelled"
    assert resolve(STATE, "rows.1.n") == 2


@pytest.mark.parametrize(
    ("path", "op", "value", "ok"),
    [
        ("apps.045.status", "equals", "cancelled", True),
        ("apps.045.status", "equals", "submitted", False),
        ("apps.045.tags", "contains", "a", True),
        ("apps.045.tags", "contains", "z", False),
        ("apps", "contains", "045", True),
        ("rows", "count", 2, True),
        ("rows", "count", 3, False),
        ("apps.999.status", "equals", "x", False),
        ("rows.9.n", "equals", 1, False),
    ],
)
def test_evaluate(path: str, op: str, value: object, ok: bool) -> None:
    assert evaluate(Check(path=path, op=op, value=value), STATE).ok is ok
