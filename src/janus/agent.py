"""The orchestrator loop: task -> plan-commit -> ground -> validate -> authorize ->
execute -> verify, repeated (bounded replan) across page transitions.

Each iteration ("leg", per docs/PLAN.md M4) commits a fresh plan against exactly one
snapshot, since a Plan's element refs only make sense against the snapshot they were
built from -- and since the planner only ever sees one page (invariant 1), a new leg
can legitimately need capabilities no earlier page had any way to declare (a form
appearing after a link click, say). So no capability ceiling carries across legs;
`commit_plan` still enforces invariant 2 *within* a leg's own validator-error
retries, per its own docstring. This loop just threads plan/step results through
the other gates and stops on the first one that says no, same as `authorize_action`
and `execute_step` always have.

Escalation is generic: a caller may pre-grant op kinds up front (`granted_ops`, e.g.
the benchmark's simulated user via `executor.escalation.make_granted_ops`), and/or
supply a live `escalate` callback consulted the moment a consequential action isn't
already granted (e.g. `executor.escalation.cli_escalation` for `janus run`). Either
way the only thing that ever actually authorizes a step is `authorize_action`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from playwright.sync_api import Page

from janus.config import Settings
from janus.executor.egress import install_egress_guard
from janus.executor.executor import StepOutcome, execute_step
from janus.llm import LLMClient
from janus.observer.extract import extract_snapshot
from janus.planner.ground import GroundingError, ground_plan
from janus.planner.ops import DoneStep, OpKind, Step
from janus.planner.plan import PlanningError, commit_plan
from janus.text.nepali import ad_to_bs
from janus.validator.action import authorize_action
from janus.validator.plan import validate_plan
from janus.validator.policy import Policy
from janus.verifier.verify import Status, StepCheck, classify_run, verify_step


@dataclass(frozen=True)
class RunResult:
    status: Status
    steps_run: int
    replans: int


def _normalize_inputs(inputs: dict[str, str]) -> dict[str, str]:
    """Derive inputs a form needs but a task only gave in another format.

    CLAUDE.md: "BS dates ... are code in janus/text ..., not prompts" -- an 8B model
    doing Bikram Sambat arithmetic in its head is not a reliable substitute (found
    running nag-02 for real: the model quietly dropped the date field it couldn't
    fill rather than convert it). If a task gives `dob_ad` but not `dob_bs`, convert
    it here, before the planner ever sees the input keys, so `$inputs.dob_bs` exists
    and behaves exactly like a task that provided it directly.
    """
    if "dob_ad" in inputs and "dob_bs" not in inputs:
        year, month, day = ad_to_bs(date.fromisoformat(inputs["dob_ad"]))
        inputs = {**inputs, "dob_bs": f"{year:04d}-{month:02d}-{day:02d}"}
    return inputs


def run_task(
    page: Page,
    *,
    task_id: str,
    instruction: str,
    start_url: str,
    inputs: dict[str, str],
    policy: Policy,
    llm: LLMClient,
    settings: Settings,
    granted_ops: frozenset[OpKind] = frozenset(),
    escalate: Callable[[Step, str], bool] | None = None,
) -> RunResult:
    install_egress_guard(page, policy.allowed_origins)
    page.goto(start_url)

    inputs = _normalize_inputs(inputs)
    granted = set(granted_ops)
    outcomes: list[StepOutcome] = []
    checks: list[StepCheck] = []
    completed_ops: list[str] = []
    claimed: Status = "partial"
    steps_run = 0
    replans = 0
    done = False
    blocked = False

    for leg in range(settings.max_replan_attempts + 1):
        replans = leg
        snapshot = extract_snapshot(page, settings)
        try:
            plan, result = commit_plan(
                task_id=task_id,
                instruction=instruction,
                inputs=inputs,
                snapshot=snapshot,
                policy=policy,
                llm=llm,
                max_retries=settings.max_plan_retries,
                completed_ops=completed_ops,
            )
            plan = ground_plan(
                plan,
                snapshot,
                page,
                inputs,
                llm,
                sensitive_fields=policy.sensitive_fields,
                threshold=settings.grounding_similarity_threshold,
            )
            # Grounding may have rewritten refs/values; re-check the gate it must
            # still pass, exactly as an untouched model output would.
            result = validate_plan(plan, policy, snapshot, inputs)
        except (PlanningError, GroundingError):
            blocked = True
            break
        if not result.ok:
            blocked = True
            break

        for step, decision in zip(plan.steps, result.decisions, strict=True):
            if isinstance(step, DoneStep):
                claimed = step.status
                done = True
                break

            authorization = authorize_action(step.op, decision.consequential, frozenset(granted))
            if not authorization.allowed and escalate is not None:
                if escalate(step, authorization.reason):
                    granted.add(step.op)
                    authorization = authorize_action(
                        step.op, decision.consequential, frozenset(granted)
                    )
            if not authorization.allowed:
                blocked = True
                break

            outcome = execute_step(page, step, snapshot, inputs)
            outcomes.append(outcome)
            steps_run += 1
            if outcome.blocked:
                blocked = True
                break
            if not outcome.ok:
                break
            completed_ops.append(step.op)
            checks.append(verify_step(page, step, snapshot, inputs))

        if done or blocked:
            break

    status = classify_run(outcomes, checks, claimed if done else "partial")
    if blocked:
        status = "blocked"
    return RunResult(status=status, steps_run=steps_run, replans=replans)
