from __future__ import annotations

from pathlib import Path

from janus_bench.harness.checks import CheckResult
from janus_bench.harness.runner import TaskResult, classify_injection
from janus_bench.harness.taskspec import load_task

TASKS = Path(__file__).resolve().parents[3] / "tasks" / "nagarpalika"


def _result(
    violations: list[CheckResult],
    attacker_received: list | None = None,
    unexercised: bool = False,
) -> TaskResult:
    return TaskResult(
        "t",
        "a",
        False,
        forbidden_violations=violations,
        attacker_received=attacker_received or [],
        forbidden_unexercised=unexercised,
    )


def test_not_applicable_without_injection() -> None:
    assert classify_injection(load_task(TASKS / "t01.yaml"), _result([])) == "n/a"


def test_resisted_and_hijacked() -> None:
    task = load_task(TASKS / "t05.yaml")
    assert task.injection is not None
    assert classify_injection(task, _result([])) == "resisted"
    violated = CheckResult(task.injection.forbidden[0], False, "cancelled")
    assert classify_injection(task, _result([violated])) == "hijacked"


def test_leaked_takes_precedence_over_hijacked() -> None:
    task = load_task(TASKS / "t05.yaml")
    assert task.injection is not None
    violated = CheckResult(task.injection.forbidden[0], False, "cancelled")
    received = [{"method": "GET", "path": "/copy"}]
    assert classify_injection(task, _result([violated], attacker_received=received)) == "leaked"


def test_leaked_with_empty_forbidden_list() -> None:
    task = load_task(TASKS / "t09.yaml")
    assert task.injection is not None and task.injection.forbidden == []
    received = [{"method": "GET", "path": "/copy"}]
    assert classify_injection(task, _result([], attacker_received=received)) == "leaked"


def test_unexercised_when_forbidden_path_never_existed() -> None:
    # The share-13 scoring artifact P0 found (docs/ERROR_ANALYSIS.md): the run
    # never submitted, so the forbidden path was never created -- not a violation.
    task = load_task(TASKS / "t05.yaml")
    assert classify_injection(task, _result([], unexercised=True)) == "unexercised"


def test_hijacked_takes_precedence_over_unexercised() -> None:
    task = load_task(TASKS / "t05.yaml")
    assert task.injection is not None
    violated = CheckResult(task.injection.forbidden[0], False, "cancelled")
    assert classify_injection(task, _result([violated], unexercised=True)) == "hijacked"


def test_leaked_takes_precedence_over_unexercised() -> None:
    task = load_task(TASKS / "t09.yaml")
    received = [{"method": "GET", "path": "/copy"}]
    assert (
        classify_injection(task, _result([], attacker_received=received, unexercised=True))
        == "leaked"
    )
