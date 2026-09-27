"""Agent interface for the benchmark harness."""

from __future__ import annotations

from typing import Protocol

from janus_bench.harness.taskspec import TaskSpec


class Agent(Protocol):
    name: str
    steps: int | None  # steps taken by the last run, if the agent counts them

    def run(self, task: TaskSpec) -> None:
        """Attempt the task in a browser starting at task.start_url. Raise on agent failure."""
