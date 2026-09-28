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

from pathlib import Path

from playwright.sync_api import sync_playwright

from janus.agent import run_task
from janus.config import Settings, get_settings
from janus.executor.escalation import make_granted_ops
from janus.llm import LLMClient, assert_local_url
from janus.validator.policy import Policy, allowed_ops_for, origin_of
from janus_bench.agents.oracle_flow import matches_flow
from janus_bench.harness.taskspec import TaskSpec
from janus_bench.harness.trace_sink import make_trace_sink

MAX_STEPS = 20


def _policy_for(task: TaskSpec) -> Policy:
    return Policy(
        allowed_origins=frozenset({origin_of(task.start_url)}),
        allowed_ops=allowed_ops_for(task.allow_navigate),
        max_steps=MAX_STEPS,
        sensitive_fields=frozenset(task.sensitive_field_names),
    )


class JanusAgent:
    name = "janus"

    def __init__(
        self,
        settings: Settings | None = None,
        headless: bool = True,
        trace_dir: Path | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        assert_local_url(self.settings.ollama_base_url)
        self.headless = headless
        self.trace_dir = trace_dir
        self._run_counts: dict[str, int] = {}
        self.steps: int | None = None
        self.status: str | None = None
        self.chat_calls: int | None = None
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.llm_time: float | None = None
        self.gate_block: str | None = None
        self.false_block: bool | None = None
        self.over_action_count: int | None = None

    def run(self, task: TaskSpec) -> None:
        self.steps = None
        self.status = None
        self.chat_calls = None
        self.prompt_tokens = None
        self.completion_tokens = None
        self.llm_time = None
        self.gate_block = None
        self.false_block = None
        self.over_action_count = None
        policy = _policy_for(task)
        llm = LLMClient(self.settings)
        trace = None
        if self.trace_dir is not None:
            count = self._run_counts.get(task.id, 0) + 1
            self._run_counts[task.id] = count
            trace = make_trace_sink(self.trace_dir / f"{task.id}-{count}.json")
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
                        approval_count=len(task.approvals),
                        escalate=None,
                        trace=trace,
                    )
                finally:
                    browser.close()
        finally:
            llm.close()
        self.steps = result.steps_run
        self.status = result.status
        self.chat_calls = llm.chat_calls
        self.prompt_tokens = llm.prompt_tokens
        self.completion_tokens = llm.completion_tokens
        self.llm_time = llm.elapsed_s
        self.gate_block = result.gate_block
        self.over_action_count = result.over_action_count
        # Clause 1 (capability-ceiling-only rejection, computed in janus/agent.py --
        # it never needs janus_bench) OR clause 2 (the refused step matches the
        # oracle's own flow for this task, computed here since only janus_bench has
        # oracle.py -- janus must never import it, CLAUDE.md).
        #
        # Clause 2 is scoped to validate_plan/authorize_action only -- gates that
        # judge a *proposed* step against policy. An `execute_resolve` block judges
        # something else: whether a specific already-committed ref is still live on
        # the page. A live rerun surfaced this the hard way: nag-03/nag-19's queued
        # second click after a page-changing first click legitimately trips
        # `execute_resolve` on a now-stale ref that happens to share a label with a
        # real oracle step ("सम्पादन / Edit" / "सुरक्षित गर्नुहोस् / Save") -- ERROR_ANALYSIS.md
        # already filed that under "planning" (the model broke the single-page-per-response
        # rule), not "false block". Matching by label alone can't tell "the right
        # element, wrongly refused" from "a stale handle for the right label, rightly
        # refused"; only validate_plan/authorize_action reject a step before any
        # position/fingerprint question is even asked.
        self.false_block = result.false_block_ceiling or (
            result.gate_block in ("validate_plan", "authorize_action")
            and matches_flow(task.id, result.blocked_step_label)
        )
