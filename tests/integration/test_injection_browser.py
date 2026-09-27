"""M8 injection suite: oracle resists every case, a deliberately gullible scripted
agent proves each case's forbidden-state check (or the attacker sink) actually
fires. Needs Playwright Chromium; oracle is the only agent here that drives a
real browser -- gullible complies via direct HTTP calls (agents/gullible.py).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from janus_bench.agents.gullible import GullibleAgent
from janus_bench.agents.oracle import OracleAgent
from janus_bench.harness.runner import run_task
from janus_bench.harness.server import running_site
from janus_bench.harness.taskspec import TaskSpec, load_tasks

pytestmark = pytest.mark.browser

TASKS_DIR = Path(__file__).resolve().parents[2] / "tasks"
INJECTION_TASKS = [
    t
    for t in load_tasks(TASKS_DIR / "nagarpalika") + load_tasks(TASKS_DIR / "sharesewa")
    if t.injection
]


@pytest.fixture(scope="module")
def nag_url() -> Iterator[str]:
    with running_site("nagarpalika") as url:
        yield url


@pytest.fixture(scope="module")
def share_url() -> Iterator[str]:
    with running_site("sharesewa") as url:
        yield url


@pytest.fixture(scope="module")
def attacker_url() -> Iterator[str]:
    with running_site("attacker") as url:
        yield url


def _base_url(task: TaskSpec, nag_url: str, share_url: str) -> str:
    return {"nagarpalika": nag_url, "sharesewa": share_url}[task.site]


@pytest.mark.parametrize("task", INJECTION_TASKS, ids=lambda t: t.id)
def test_oracle_resists_injection(
    task: TaskSpec, nag_url: str, share_url: str, attacker_url: str
) -> None:
    base_url = _base_url(task, nag_url, share_url)
    result = run_task(task, OracleAgent(), base_url, attacker_url)
    assert result.injection_outcome(task) == "resisted"
    assert not result.attacker_received


@pytest.mark.parametrize("task", INJECTION_TASKS, ids=lambda t: t.id)
def test_gullible_triggers_injection(
    task: TaskSpec, nag_url: str, share_url: str, attacker_url: str
) -> None:
    base_url = _base_url(task, nag_url, share_url)
    result = run_task(task, GullibleAgent(), base_url, attacker_url)
    assert task.injection is not None
    expected = "leaked" if task.injection.category == "exfiltration" else "hijacked"
    assert result.injection_outcome(task) == expected
