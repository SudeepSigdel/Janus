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


def test_policy_scopes_to_task_start_origin() -> None:
    task = load_task(TASKS / "t01.yaml")
    policy = _policy_for(task)
    assert policy.allowed_origins == frozenset({origin_of(task.start_url)})
    assert policy.max_steps == MAX_STEPS
    assert "SUBMIT" in policy.allowed_ops
