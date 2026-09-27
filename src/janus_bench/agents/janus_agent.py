"""Janus agent: the harness `Agent` adapter around `janus.agent.run_task`.

Wires a `TaskSpec`'s fields into `run_task`'s `Policy`/inputs/escalation shape, the
same way `janus.cli::run` does for a single interactive task. The one deliberate
difference is escalation: the benchmark's simulated user (docs/PLAN.md) approves
only consequential actions matching the task's declared `approvals` and denies
everything else -- no live callback needed, since `run_task` already blocks a step
whose authorization fails when no `escalate` callback is supplied. `janus.cli`'s
`cli_escalation` (a human at a prompt) is deliberately not used here.
"""

from __future__ import annotations

from typing import get_args

from playwright.sync_api import sync_playwright

from janus.agent import run_task
from janus.config import Settings, get_settings
from janus.executor.escalation import make_granted_ops
from janus.llm import LLMClient, assert_local_url
from janus.planner.ops import OpKind
from janus.validator.policy import Policy, origin_of
from janus_bench.harness.taskspec import TaskSpec

MAX_STEPS = 20


def _policy_for(task: TaskSpec) -> Policy:
    return Policy(
        allowed_origins=frozenset({origin_of(task.start_url)}),
        allowed_ops=frozenset(get_args(OpKind)),
        max_steps=MAX_STEPS,
    )


class JanusAgent:
    name = "janus"

    def __init__(self, settings: Settings | None = None, headless: bool = True) -> None:
        self.settings = settings or get_settings()
        assert_local_url(self.settings.ollama_base_url)
        self.headless = headless
        self.steps: int | None = None

    def run(self, task: TaskSpec) -> None:
        self.steps = None
        policy = _policy_for(task)
        llm = LLMClient(self.settings)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=self.headless)
                try:
                    page = browser.new_page()
                    result = run_task(
                        page,
                        task_id=task.id,
                        instruction=task.instruction.en,
                        start_url=task.start_url,
                        inputs=task.inputs,
                        policy=policy,
                        llm=llm,
                        settings=self.settings,
                        granted_ops=make_granted_ops(task.approvals),
                        escalate=None,
                    )
                finally:
                    browser.close()
        finally:
            llm.close()
        self.steps = result.steps_run
