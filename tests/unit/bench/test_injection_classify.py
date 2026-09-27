from __future__ import annotations

from pathlib import Path

from janus_bench.harness.checks import CheckResult
from janus_bench.harness.runner import TaskResult, classify_injection
from janus_bench.harness.taskspec import load_task

TASKS = Path(__file__).resolve().parents[3] / "tasks" / "nagarpalika"


def _result(violations: list[CheckResult]) -> TaskResult:
    return TaskResult("t", "a", False, forbidden_violations=violations)


def test_not_applicable_without_injection() -> None:
    assert classify_injection(load_task(TASKS / "t01.yaml"), _result([])) == "n/a"


def test_resisted_and_hijacked() -> None:
    task = load_task(TASKS / "t05.yaml")
    assert task.injection is not None
    assert classify_injection(task, _result([])) == "resisted"
    violated = CheckResult(task.injection.forbidden[0], False, "cancelled")
    assert classify_injection(task, _result([violated])) == "hijacked"
