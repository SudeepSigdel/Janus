"""Oracle and null agents against the live ShareSewa replica (needs Playwright Chromium).

No injection cases here -- M7's scope is the clean 10-task set; M8 adds the injection
suite across both sites.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from janus_bench.agents.null import NullAgent
from janus_bench.agents.oracle import OracleAgent
from janus_bench.harness.runner import run_task
from janus_bench.harness.server import running_site
from janus_bench.harness.taskspec import TaskSpec, load_tasks

pytestmark = pytest.mark.browser

TASKS = load_tasks(Path(__file__).resolve().parents[2] / "tasks" / "sharesewa")


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    with running_site("sharesewa") as url:
        yield url


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_oracle_solves(task: TaskSpec, base_url: str) -> None:
    result = run_task(task, OracleAgent(), base_url)
    assert result.success, (result.error, [(r.check.path, r.actual) for r in result.failed_checks])
    assert not result.forbidden_violations


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_null_fails(task: TaskSpec, base_url: str) -> None:
    assert not run_task(task, NullAgent(), base_url).success
