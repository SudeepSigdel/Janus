from __future__ import annotations

from pathlib import Path

import pytest

from janus.config import Settings
from janus.llm import NonLocalEndpointError
from janus.validator.policy import origin_of
from janus_bench.agents.janus_agent import MAX_STEPS, JanusAgent, _policy_for
from janus_bench.harness.taskspec import load_task

TASKS = Path(__file__).resolve().parents[3] / "tasks" / "nagarpalika"


def test_rejects_non_local_endpoint() -> None:
    with pytest.raises(NonLocalEndpointError):
        JanusAgent(Settings(ollama_base_url="https://api.example.com"))


def test_local_agent_uses_planner_model() -> None:
    agent = JanusAgent()
    assert agent.settings.planner_model == "janus-planner"
    assert agent.steps is None
    assert agent.status is None
    assert agent.gate_block is None
    assert agent.false_block is None
    assert agent.trace_dir is None


def test_trace_dir_is_stored_but_not_created_until_a_run(tmp_path: Path) -> None:
    trace_dir = tmp_path / "traces"
    agent = JanusAgent(trace_dir=trace_dir)
    assert agent.trace_dir == trace_dir
    assert not trace_dir.exists()


def test_policy_scopes_to_task_start_origin() -> None:
    task = load_task(TASKS / "t01.yaml")
    policy = _policy_for(task)
    assert policy.allowed_origins == frozenset({origin_of(task.start_url)})
    assert policy.max_steps == MAX_STEPS
    assert "SUBMIT" in policy.allowed_ops


def test_policy_excludes_navigate_by_default() -> None:
    # t01.yaml doesn't set allow_navigate (docs/PLAN.md P3: no dev task needs one).
    task = load_task(TASKS / "t01.yaml")
    policy = _policy_for(task)
    assert "NAVIGATE" not in policy.allowed_ops


def test_policy_includes_navigate_when_task_opts_in() -> None:
    task = load_task(TASKS / "t01.yaml").model_copy(update={"allow_navigate": True})
    policy = _policy_for(task)
    assert "NAVIGATE" in policy.allowed_ops
