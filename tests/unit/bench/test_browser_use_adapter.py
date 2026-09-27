from __future__ import annotations

from pathlib import Path

import pytest

from janus.config import Settings
from janus.llm import NonLocalEndpointError
from janus_bench.agents.browser_use_agent import BrowserUseAgent, build_prompt
from janus_bench.harness.taskspec import load_task

TASKS = Path(__file__).resolve().parents[3] / "tasks" / "nagarpalika"


def test_rejects_non_local_endpoint() -> None:
    with pytest.raises(NonLocalEndpointError):
        BrowserUseAgent(Settings(ollama_base_url="https://api.example.com"))


def test_local_agent_uses_planner_model() -> None:
    agent = BrowserUseAgent()
    assert agent.settings.planner_model == "janus-planner"
    assert agent.steps is None


def test_prompt_contains_start_url_and_inputs() -> None:
    task = load_task(TASKS / "t03.yaml")
    prompt = build_prompt(task)
    assert task.start_url in prompt
    assert all(v in prompt for v in task.inputs.values())
