"""Null agent: does nothing. Every task must fail against it."""

from __future__ import annotations

from janus_bench.harness.taskspec import TaskSpec


class NullAgent:
    name = "null"
    steps: int | None = None

    def run(self, task: TaskSpec) -> None:
        return None
