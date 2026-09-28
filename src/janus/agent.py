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
from typing import Any, Literal

from playwright.sync_api import Page

from janus.config import Settings
from janus.executor.egress import install_egress_guard
from janus.executor.executor import StepOutcome, execute_step
from janus.llm import LLMClient
from janus.observer.extract import extract_snapshot
from janus.observer.snapshot import PageSnapshot
from janus.planner.ground import GroundingError, ground_plan
from janus.planner.ops import DoneStep, FillFormStep, OpKind, Step
from janus.planner.plan import PlanningError, commit_plan
from janus.text.nepali import ad_to_bs
from janus.validator.action import authorize_action
from janus.validator.plan import validate_plan
from janus.validator.policy import Policy
from janus.verifier.verify import Status, StepCheck, classify_run, verify_step

GateBlock = Literal["validate_plan", "grounding", "authorize_action", "execute_resolve"]


@dataclass(frozen=True)
class RunResult:
    status: Status
    steps_run: int
    replans: int
    # Evaluation infra (docs/PLAN.md P1), not consulted by any gate: which gate (if
    # any) ended the run, what it refused, and whether the refusal looks like the
    # known "retry capability ceiling" false-block chain (P0's diagnosis). The
    # harness (janus_bench) combines `false_block_ceiling` with its own oracle-flow
    # check for the rule's second clause, since that needs oracle.py's routines and
    # `janus` must never import `janus_bench`.
    gate_block: GateBlock | None = None
    blocked_step_op: OpKind | None = None
    blocked_step_label: str | None = None
    false_block_ceiling: bool = False
    chat_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_time: float = 0.0


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


def _is_ceiling_only(errors: list[str]) -> bool:
    """P0's diagnosed false-block chain (docs/ERROR_ANALYSIS.md): the *only* thing
    wrong with a rejected attempt is that it asks for a capability beyond the ceiling
    a hallucinated first attempt locked in -- never a real policy/role/origin problem."""
    return len(errors) == 1 and errors[0].startswith(
        "replan adds capabilities beyond what was committed"
    )


def _step_label(step: Step, snapshot: PageSnapshot) -> str | None:
    """The accessible name of the element `step` targets, if any -- used only for
    evaluation (gate_block/false_block reporting), never for authorization."""
    if isinstance(step, FillFormStep):
        ref = step.fields[0].ref if step.fields else None
    else:
        ref = getattr(step, "ref", None)
    if ref is None:
        return None
    for element in snapshot.elements:
        if element.ref == ref:
            return element.accessible_name
    return None


def _grounding_diff(before: list[Step], after: list[Step]) -> list[dict[str, Any]]:
    changes = []
    for i, (b, a) in enumerate(zip(before, after, strict=True)):
        bd, ad = b.model_dump(), a.model_dump()
        if bd != ad:
            changes.append({"step": i, "before": bd, "after": ad})
    return changes


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
    trace: Callable[[str, dict[str, Any]], None] | None = None,
) -> RunResult:
    """`trace`, if given, is called with (stage, data) at each pipeline stage
    (docs/PLAN.md P1: "trace capture as a runtime feature"). It never influences any
    gate's decision; with `trace=None` this function's behavior is unchanged from
    before the parameter existed."""
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
    gate_block: GateBlock | None = None
    blocked_step_op: OpKind | None = None
    blocked_step_label: str | None = None
    false_block_ceiling = False

    for leg in range(settings.max_replan_attempts + 1):
        replans = leg
        snapshot = extract_snapshot(page, settings)
        if trace is not None:
            trace(
                "snapshot",
                {
                    "leg": leg,
                    "url": snapshot.url,
                    "title": snapshot.title,
                    "elements": len(snapshot.elements),
                },
            )
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
                trace=trace,
            )
            pre_ground_steps = plan.steps
            plan = ground_plan(
                plan,
                snapshot,
                page,
                inputs,
                llm,
                sensitive_fields=policy.sensitive_fields,
                threshold=settings.grounding_similarity_threshold,
            )
            if trace is not None:
                diff = _grounding_diff(pre_ground_steps, plan.steps)
                if diff:
                    trace("grounding_diff", {"leg": leg, "changes": diff})
            # Grounding may have rewritten refs/values; re-check the gate it must
            # still pass, exactly as an untouched model output would.
            result = validate_plan(plan, policy, snapshot, inputs)
        except PlanningError as exc:
            blocked = True
            gate_block = "validate_plan"
            false_block_ceiling = _is_ceiling_only(exc.errors)
            if exc.plan is not None and exc.plan.steps:
                last = exc.plan.steps[-1]
                blocked_step_op = last.op
                blocked_step_label = _step_label(last, snapshot)
            if trace is not None:
                trace("blocked", {"gate": gate_block, "errors": exc.errors})
            break
        except GroundingError as exc:
            blocked = True
            gate_block = "grounding"
            if trace is not None:
                trace("blocked", {"gate": gate_block, "error": str(exc)})
            break
        if not result.ok:
            blocked = True
            gate_block = "validate_plan"
            false_block_ceiling = _is_ceiling_only(result.errors)
            if plan.steps:
                last = plan.steps[-1]
                blocked_step_op = last.op
                blocked_step_label = _step_label(last, snapshot)
            if trace is not None:
                trace("blocked", {"gate": gate_block, "errors": result.errors})
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
            if trace is not None:
                trace(
                    "authorize",
                    {
                        "op": step.op,
                        "consequential": decision.consequential,
                        "allowed": authorization.allowed,
                    },
                )
            if not authorization.allowed:
                blocked = True
                gate_block = "authorize_action"
                blocked_step_op = step.op
                blocked_step_label = _step_label(step, snapshot)
                break

            outcome = execute_step(page, step, snapshot, inputs)
            outcomes.append(outcome)
            steps_run += 1
            if trace is not None:
                trace(
                    "execute",
                    {
                        "op": step.op,
                        "ok": outcome.ok,
                        "blocked": outcome.blocked,
                        "reason": outcome.reason,
                    },
                )
            if outcome.blocked:
                blocked = True
                gate_block = "execute_resolve"
                blocked_step_op = step.op
                blocked_step_label = _step_label(step, snapshot)
                break
            if not outcome.ok:
                break
            completed_ops.append(step.op)
            check = verify_step(page, step, snapshot, inputs)
            checks.append(check)
            if trace is not None:
                trace("verify", {"op": step.op, "ok": check.ok, "reason": check.reason})

        if done or blocked:
            break

    status = classify_run(outcomes, checks, claimed if done else "partial")
    if blocked:
        status = "blocked"
    result = RunResult(
        status=status,
        steps_run=steps_run,
        replans=replans,
        gate_block=gate_block,
        blocked_step_op=blocked_step_op,
        blocked_step_label=blocked_step_label,
        false_block_ceiling=false_block_ceiling,
        chat_calls=llm.chat_calls,
        prompt_tokens=llm.prompt_tokens,
        completion_tokens=llm.completion_tokens,
        llm_time=llm.elapsed_s,
    )
    if trace is not None:
        trace(
            "run_result",
            {
                "status": result.status,
                "steps_run": result.steps_run,
                "replans": result.replans,
                "gate_block": result.gate_block,
                "chat_calls": result.chat_calls,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "llm_time": result.llm_time,
            },
        )
    return result
