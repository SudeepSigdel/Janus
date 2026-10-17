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

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from janus.config import Settings
from janus.executor.egress import install_egress_guard
from janus.executor.executor import StepOutcome, execute_step
from janus.executor.prefill import clear_untouched_prefill, scan_untouched_prefill
from janus.executor.resolve import ResolutionError, resolve_element
from janus.llm import LLMClient
from janus.observer.extract import extract_snapshot
from janus.observer.snapshot import Element, Fingerprint, PageSnapshot
from janus.planner.ground import GroundingError, ground_plan
from janus.planner.ops import (
    ASK_INPUT_PREFIX,
    READ_INPUT_PREFIX,
    AskUserStep,
    DoneStep,
    FillFormStep,
    OpKind,
    ReadStep,
    SelectStep,
    Step,
)
from janus.planner.plan import PlanningError, commit_plan
from janus.policy import ApprovalTarget
from janus.read import quarantined_read
from janus.text.nepali import ad_to_bs
from janus.validator.action import authorize_action
from janus.validator.plan import validate_plan
from janus.validator.policy import Policy
from janus.verifier.verify import Status, StepCheck, classify_run, verify_step

GateBlock = Literal["validate_plan", "grounding", "authorize_action", "execute_resolve", "ask_user"]


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
    # docs/PLAN.md P4: a consequential step authorized and executed after this
    # run's declared approvals were already exhausted -- should always be 0
    # (authorize_action denies it once the grant is revoked); measured, not assumed.
    over_action_count: int = 0


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


def _target_element(step: Step, snapshot: PageSnapshot) -> Element | None:
    """The element `step` targets, if any (a FILL_FORM step's first field, for a
    step with more than one -- same convention `_step_label` used before this
    helper existed)."""
    if isinstance(step, FillFormStep):
        ref = step.fields[0].ref if step.fields else None
    else:
        ref = getattr(step, "ref", None)
    if ref is None:
        return None
    for element in snapshot.elements:
        if element.ref == ref:
            return element
    return None


def _step_label(step: Step, snapshot: PageSnapshot) -> str | None:
    """The accessible name of the element `step` targets, if any -- used only for
    evaluation (gate_block/false_block reporting), never for authorization."""
    element = _target_element(step, snapshot)
    return element.accessible_name if element is not None else None


_View = tuple[str, frozenset[Fingerprint], str]


def _view(snapshot: PageSnapshot) -> _View:
    """What changed on a page, for the loop guard: its URL, its element outline, and a hash
    of its visible text. The text is compared, never read by the planner or any model: a
    click that only switched a tab's content did something, even though the outline is the
    same, and must not be refused as a no-op."""
    digest = hashlib.sha256(chr(10).join(snapshot.untrusted_text).encode("utf-8")).hexdigest()
    return snapshot.url, frozenset(e.fingerprint for e in snapshot.elements), digest


def _all_labels(step: Step, snapshot: PageSnapshot) -> str | None:
    """Every field a FILL_FORM step writes, by label (never by value), for traces and reports."""
    if not isinstance(step, FillFormStep):
        return None
    names = [e.accessible_name for e in snapshot.elements if e.ref in {f.ref for f in step.fields}]
    return ", ".join(names) or None


def _written_refs(step: Step) -> list[str]:
    if isinstance(step, FillFormStep):
        return [f.ref for f in step.fields]
    if isinstance(step, SelectStep):
        return [step.ref]
    return []


def _prefill_handle(page: Page, target: Element | None):
    try:
        return resolve_element(page, target) if target is not None else None
    except (ResolutionError, PlaywrightError):
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
    approvals: Sequence[ApprovalTarget] = (),
    escalate: Callable[[Step, str], bool] | None = None,
    trace: Callable[[str, dict[str, Any]], None] | None = None,
    navigate: bool = True,
    stop: Callable[[], bool] | None = None,
    stop_after_approved_commit: bool = False,
    ask_user: Callable[[str, bool], str | None] | None = None,
) -> RunResult:
    """`trace`, if given, is called with (stage, data) at each pipeline stage
    (docs/PLAN.md P1: "trace capture as a runtime feature"). It never influences any
    gate's decision; with `trace=None` this function's behavior is unchanged from
    before the parameter existed.

    `approvals` (docs/PLAN.md Q2, generalizing P4) is this run's declared
    `ApprovalTarget`s (typically `task.approvals`); each is consumed by one *commit*
    that matches it -- a consequential step, authorized against it by
    `authorize_action`, that causes a main-frame POST navigation. When the last
    remaining approval is consumed and the step verifies, the run ends `completed`
    immediately instead of waiting for the model to self-report DONE, and every
    remaining live grant is revoked so a later consequential step is denied.
    `approvals=()` (the default, and every task with no approvals) leaves this
    logic entirely inactive -- behavior is unchanged from before P4/Q2.

    `stop_after_approved_commit` is the same idea for a run with no declared approvals
    (`janus run --goal`): a consequential step that a human approved through `escalate`,
    caused a real POST navigation and verified ends the run `completed` at once, so it no
    longer depends on the model self-reporting DONE from a receipt page it cannot read.
    The cost is that a goal needing two separate consequential actions needs two runs."""
    install_egress_guard(page, policy.allowed_origins)
    # B0: an environment that sets the page up itself (e.g. BrowserGym's MiniWoB, which
    # injects the episode after loading) passes navigate=False and an already-open page.
    if navigate:
        page.goto(start_url)

    inputs = _normalize_inputs(inputs)
    granted = set(granted_ops)
    outcomes: list[StepOutcome] = []
    checks: list[StepCheck] = []
    completed_ops: list[str] = []
    # Field names/labels this run has filled, so the user's own values are never mistaken for
    # untouched pre-filled ones, including when a form comes back re-rendered at another URL
    # after a validation error.
    filled_run: set[str] = set()
    # Untouched pre-filled fields seen at the last form submit, so the later approval prompt
    # (often on a review page where the form is gone) can still list them.
    carried_prefill: list[dict[str, str]] = []
    claimed: Status = "partial"
    steps_run = 0
    replans = 0
    done = False
    blocked = False
    gate_block: GateBlock | None = None
    blocked_step_op: OpKind | None = None
    blocked_step_label: str | None = None
    false_block_ceiling = False
    remaining: list[ApprovalTarget] = list(approvals)
    over_action_count = 0
    # E21 loop guard: (url, fingerprint) of CLICKs that changed nothing observable.
    no_effect: set[tuple[str, Fingerprint]] = set()
    prev_view: _View | None = None
    last_click: tuple[str, Fingerprint] | None = None
    # A submit that posts and brings back the identical page (the same validation errors)
    # stays retryable once, so the planner can fix a field; the third identical repeat is
    # treated as a no-op like a dead click.
    last_post: tuple[str, Fingerprint] | None = None
    post_repeats: dict[tuple[str, Fingerprint], int] = {}

    for leg in range(settings.max_replan_attempts + 1):
        # B1: the environment says the episode is over (e.g. MiniWoB scored it); stop
        # without another planning call. Never consulted by any gate.
        if stop is not None and stop():
            break
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
        view = _view(snapshot)
        if last_click is not None and view == prev_view:
            no_effect.add(last_click)
            if trace is not None:
                trace(
                    "loop_guard",
                    {"leg": leg, "url": last_click[0], "label": last_click[1].accessible_name},
                )
        if last_post is not None and view == prev_view:
            post_repeats[last_post] = post_repeats.get(last_post, 0) + 1
            if post_repeats[last_post] >= 2:
                no_effect.add(last_post)
                if trace is not None:
                    trace(
                        "loop_guard",
                        {"leg": leg, "url": last_post[0], "label": last_post[1].accessible_name},
                    )
        prev_view, last_click, last_post = view, None, None
        no_effect_refs = frozenset(
            e.ref for e in snapshot.elements if (snapshot.url, e.fingerprint) in no_effect
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
                no_effect_refs=no_effect_refs,
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
            result = validate_plan(plan, policy, snapshot, inputs, no_effect_refs=no_effect_refs)
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

        filled_names = filled_run
        for index, (step, decision) in enumerate(zip(plan.steps, result.decisions, strict=True)):
            if isinstance(step, DoneStep):
                claimed = step.status
                done = True
                break

            if isinstance(step, AskUserStep):
                answer = ask_user(step.question, step.secret) if ask_user is not None else None
                answer = answer.strip() if answer else None
                # The question is the model's; the answer is the user's and is never traced.
                if trace is not None:
                    trace(
                        "ask_user",
                        {"name": step.name, "question": step.question, "answered": bool(answer)},
                    )
                if not answer:
                    outcomes.append(StepOutcome(ok=False, blocked=True, reason="no answer"))
                    blocked = True
                    gate_block = "ask_user"
                    break
                inputs[f"{ASK_INPUT_PREFIX}{step.name}"] = answer
                completed_ops.append("ASK_USER")
                steps_run += 1
                break  # the next leg's planner sees the answer as an input key

            if isinstance(step, ReadStep):
                value = quarantined_read(step.question, step.kind, snapshot, llm)
                if trace is not None:
                    trace(
                        "read",
                        {"name": step.name, "question": step.question, "value": value},
                    )
                if value is None:
                    outcomes.append(StepOutcome(ok=False, reason="no value of that shape"))
                    break
                inputs[f"{READ_INPUT_PREFIX}{step.name}"] = value
                completed_ops.append("READ")
                steps_run += 1
                break  # the next leg's planner sees the value as an input key

            target = _target_element(step, snapshot)
            target_name = target.accessible_name if target is not None else None
            target_row_key = target.row_key if target is not None else None
            prefilled: list[dict[str, str]] = []
            if policy.prefill != "allow" and step.op in ("CLICK", "SUBMIT") and target is not None:
                handle = _prefill_handle(page, target)
                if handle is not None:
                    try:
                        prefilled = scan_untouched_prefill(handle, filled_names)
                    except PlaywrightError:
                        prefilled = []
                if prefilled:
                    carried_prefill = prefilled
                    if trace is not None:
                        trace("prefill_untouched", {"fields": prefilled})
            authorization = authorize_action(
                step.op,
                decision.consequential,
                target_name,
                target_row_key,
                snapshot.url,
                remaining,
                frozenset(granted),
            )
            note = ""
            if decision.consequential and (prefilled or carried_prefill):
                shown = ", ".join(
                    f"{f['label'] or '?'}={f['value']!r}" for f in (prefilled or carried_prefill)
                )
                note = f"; untouched pre-filled fields: {shown}"
            human_approved = False
            if not authorization.allowed and escalate is not None:
                if escalate(
                    step,
                    f"{authorization.reason}; target {target_name!r} at {snapshot.url}{note}",
                ):
                    granted.add(step.op)
                    human_approved = True
                    authorization = authorize_action(
                        step.op,
                        decision.consequential,
                        target_name,
                        target_row_key,
                        snapshot.url,
                        remaining,
                        frozenset(granted),
                    )
            if trace is not None:
                trace(
                    "authorize",
                    {
                        "op": step.op,
                        "label": _all_labels(step, snapshot) or target_name,
                        "consequential": decision.consequential,
                        "allowed": authorization.allowed,
                        "reason": authorization.reason,
                    },
                )
            if not authorization.allowed:
                blocked = True
                gate_block = "authorize_action"
                blocked_step_op = step.op
                blocked_step_label = _step_label(step, snapshot)
                break

            # Safety net (docs/PLAN.md P4, generalized by Q2): this step was
            # authorized even though this run's declared approvals were already
            # exhausted. Should never happen through the `approvals` channel --
            # `authorize_action` can't match an empty `remaining` -- but a live
            # `escalate` grant is a separate channel and this is measured, not
            # assumed.
            if decision.consequential and approvals and not remaining:
                over_action_count += 1

            if prefilled and policy.prefill == "clear":
                handle = _prefill_handle(page, target)
                if handle is not None:
                    try:
                        removed = clear_untouched_prefill(handle, filled_names)
                    except PlaywrightError:
                        removed = []
                    if trace is not None:
                        trace("prefill_cleared", {"fields": removed})
            outcome = execute_step(page, step, snapshot, inputs)
            outcomes.append(outcome)
            steps_run += 1
            if trace is not None:
                trace(
                    "execute",
                    {
                        "op": step.op,
                        "label": target_name,
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
            for ref in _written_refs(step):
                written = next((e for e in snapshot.elements if e.ref == ref), None)
                if written is not None:
                    filled_names.update(
                        n for n in (written.name_attr, written.accessible_name) if n
                    )
            # Only a plain CLICK (no form POST) can be a no-op the loop guard records;
            # a submit that came back with validation errors must stay retryable.
            last_click = (
                (snapshot.url, target.fingerprint)
                if step.op == "CLICK" and target is not None and not outcome.caused_post
                else None
            )
            last_post = (
                (snapshot.url, target.fingerprint)
                if target is not None and outcome.caused_post
                else None
            )
            check = verify_step(page, step, snapshot, inputs)
            checks.append(check)
            if trace is not None:
                trace("verify", {"op": step.op, "ok": check.ok, "reason": check.reason})

            # A commit (docs/PLAN.md P4, generalized by Q2): a consequential step,
            # authorized against a specific declared approval (`authorization.matched`,
            # not the live `escalate` channel), whose click caused a real POST
            # navigation. Consumes that one approval; once none remain, the run ends
            # `completed` right here and revokes every remaining live grant, so a
            # later consequential step is denied rather than merely never attempted.
            if (
                decision.consequential
                and outcome.caused_post
                and check.ok
                and authorization.matched is not None
            ):
                remaining.remove(authorization.matched)
                if not remaining:
                    granted.clear()
                    claimed = "completed"
                    done = True
                    break

            if (
                stop_after_approved_commit
                and human_approved
                and decision.consequential
                and outcome.caused_post
                and check.ok
            ):
                granted.clear()
                claimed = "completed"
                done = True
                break

            # E23 (docs/AUDIT.md A1): after a step that changed the page, the rest of
            # this plan targets refs of a page that is gone. Drop it and start a new leg
            # against a fresh snapshot instead of authorizing it against the stale one.
            # A trailing DONE is kept: it carries the model's claim, which classify_run
            # still checks against what actually happened.
            later = plan.steps[index + 1 :]
            page_changed = outcome.caused_post or (
                getattr(page, "url", snapshot.url) != snapshot.url
            )
            if page_changed and any(not isinstance(s, DoneStep) for s in later):
                if trace is not None:
                    trace("plan_truncated", {"leg": leg, "dropped": [s.op for s in later]})
                break

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
        over_action_count=over_action_count,
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
                "over_action_count": result.over_action_count,
            },
        )
    return result
