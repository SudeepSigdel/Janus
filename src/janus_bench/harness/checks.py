"""Deterministic state checks. Success is never LLM-judged."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from janus_bench.harness.taskspec import Check

_MISSING = object()


def resolve(state: Any, path: str) -> Any:
    """Follow a dotted path through dicts (by key) and lists (by index); `_MISSING` if absent."""
    current = state
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return _MISSING
    return current


@dataclass(frozen=True)
class CheckResult:
    check: Check
    ok: bool
    actual: Any
    missing: bool = False


def evaluate(check: Check, state: dict[str, Any]) -> CheckResult:
    actual = resolve(state, check.path)
    if actual is _MISSING:
        return CheckResult(check, False, "<missing>", missing=True)
    if check.op == "equals":
        ok = actual == check.value
    elif check.op == "contains":
        ok = isinstance(actual, str | list | dict) and check.value in actual
    else:  # count
        ok = isinstance(actual, str | list | dict) and len(actual) == check.value
    return CheckResult(check, ok, actual)


def evaluate_all(checks: list[Check], state: dict[str, Any]) -> list[CheckResult]:
    return [evaluate(c, state) for c in checks]
