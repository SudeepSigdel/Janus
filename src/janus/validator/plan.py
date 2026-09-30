"""validate_plan: the sole gate a planner-produced Plan passes through before any
step may be authorized (CLAUDE.md: "Only deterministic code in janus/validator/
authorizes actions").

Checks, all deterministic and model-free:
  - every step's op is in the task's allowed ops, and the plan doesn't exceed max_steps
  - NAVIGATE targets are on the origin allowlist (invariant 6)
  - every element ref a step touches actually exists on the current snapshot, and
    its role matches the op (FILL_FORM needs a textbox, SELECT needs a combobox) --
    a role mismatch is rejected here rather than left to crash the executor with a
    raw Playwright error (found running the real planner model in M5: it once put a
    combobox's ref in a FILL_FORM step)
  - a sensitive field (by name attribute) is bound via `$inputs.<key>`, never a literal
    the model could have invented (invariant 3)
  - when replanning (`committed_capabilities` given), the new plan's capabilities are a
    subset of what was already committed (invariant 2)

It also computes each step's effective consequential flag: the deterministic
classifier OR'd with the step's own hint, so a hint can only ever upgrade, never
downgrade (invariant 4).

`repair_roles` (docs/PLAN.md Q1) is a separate, pure pre-pass -- `planner/plan.py`'s
`commit_plan` runs it on every attempt, before this module's own `validate_plan`, to
rewrite a FILL_FORM/SELECT role mismatch instead of leaving it to set the retry
ceiling (P0's diagnosed false-block chain). It never authorizes anything itself; its
output still goes through the unchanged `validate_plan` below.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from janus.observer.snapshot import Element, PageSnapshot
from janus.planner.ops import FieldValue, FillFormStep, Plan, SelectStep, Step, input_ref_key
from janus.validator.consequential import is_consequential
from janus.validator.policy import (
    Capability,
    Policy,
    capabilities_of,
    is_capability_subset,
    origin_of,
)


@dataclass(frozen=True)
class StepDecision:
    index: int
    consequential: bool


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    capabilities: frozenset[Capability] = frozenset()
    decisions: list[StepDecision] = field(default_factory=list)


def _element_by_ref(snapshot: PageSnapshot, ref: str) -> Element | None:
    for element in snapshot.elements:
        if element.ref == ref:
            return element
    return None


def _check_sensitive_binding(
    errors: list[str], index: int, policy: Policy, element: Element, ref: str, value: str
) -> None:
    if element.name_attr in policy.sensitive_fields and input_ref_key(value) is None:
        errors.append(
            f"step {index}: field {ref!r} ({element.name_attr}) is sensitive and must "
            "bind via $inputs, not a literal"
        )


def validate_plan(
    plan: Plan,
    policy: Policy,
    snapshot: PageSnapshot,
    inputs: dict[str, str],
    committed_capabilities: frozenset[Capability] | None = None,
) -> ValidationResult:
    errors: list[str] = []
    decisions: list[StepDecision] = []

    if len(plan.steps) > policy.max_steps:
        errors.append(f"plan has {len(plan.steps)} steps, exceeding max_steps={policy.max_steps}")

    for i, step in enumerate(plan.steps):
        if step.op not in policy.allowed_ops:
            errors.append(f"step {i}: op {step.op} is not in the allowed ops for this task")

        if step.op == "NAVIGATE":
            if origin_of(step.url) not in policy.allowed_origins:
                errors.append(f"step {i}: NAVIGATE to {step.url!r} is off the origin allowlist")
            decisions.append(StepDecision(i, step.consequential_hint))
            continue

        if step.op == "FILL_FORM":
            consequential = step.consequential_hint
            for field_value in step.fields:
                element = _element_by_ref(snapshot, field_value.ref)
                if element is None:
                    errors.append(f"step {i}: unknown element ref {field_value.ref!r}")
                    continue
                if element.role != "textbox":
                    errors.append(
                        f"step {i}: field {field_value.ref!r} is role {element.role!r}, "
                        "not fillable (expected textbox; a combobox needs SELECT instead)"
                    )
                consequential = consequential or is_consequential(step.op, element.accessible_name)
                _check_sensitive_binding(
                    errors, i, policy, element, field_value.ref, field_value.value
                )
            decisions.append(StepDecision(i, consequential))
            continue

        if step.op in ("SELECT", "CLICK", "SUBMIT"):
            element = _element_by_ref(snapshot, step.ref)
            if element is None:
                errors.append(f"step {i}: unknown element ref {step.ref!r}")
                decisions.append(StepDecision(i, step.consequential_hint))
                continue
            if step.op == "SELECT":
                if element.role != "combobox":
                    errors.append(
                        f"step {i}: SELECT target {step.ref!r} is role {element.role!r}, "
                        "not selectable (expected combobox)"
                    )
                _check_sensitive_binding(errors, i, policy, element, step.ref, step.value)
            consequential = (
                is_consequential(step.op, element.accessible_name) or step.consequential_hint
            )
            decisions.append(StepDecision(i, consequential))
            continue

        # EXTRACT, DONE: no element ref; only a hint can mark these consequential.
        decisions.append(StepDecision(i, step.consequential_hint))

    capabilities = capabilities_of(plan, snapshot)
    if committed_capabilities is not None and not is_capability_subset(
        capabilities, committed_capabilities
    ):
        added = capabilities - committed_capabilities
        errors.append(
            f"replan adds capabilities beyond what was committed: {sorted(map(str, added))}"
        )

    return ValidationResult(
        ok=not errors, errors=errors, capabilities=capabilities, decisions=decisions
    )


@dataclass(frozen=True)
class Repair:
    """One role-mismatch fix `repair_roles` made: a FILL_FORM field aimed at a
    `combobox` rewritten to its own SELECT step, or a SELECT step aimed at a
    `textbox` rewritten to FILL_FORM. Reported once per field/step actually
    rewritten -- never for a mismatch the repair declined, which is left for
    `validate_plan` to reject exactly as before this pre-pass existed."""

    step_index: int
    ref: str
    from_op: str
    to_op: str


def _repair_capability_ok(
    capability: Capability, policy: Policy, ceiling: frozenset[Capability] | None
) -> bool:
    """A repair may only ask for an op/origin/form the policy already allows and,
    if a retry ceiling is already locked, that the ceiling already contains --
    a repair is a correction, not a way to quietly expand scope under its cover."""
    if capability[0] not in policy.allowed_ops:
        return False
    return ceiling is None or capability in ceiling


def _split_fill_form(
    step: FillFormStep,
    index: int,
    snapshot: PageSnapshot,
    policy: Policy,
    ceiling: frozenset[Capability] | None,
    page_origin: str,
) -> tuple[list[Step], list[Repair]] | None:
    """Repair one FILL_FORM step, or return `None` to leave it untouched.

    Declines whole (returns `None`, no partial repair) if any field's ref is
    unknown, if any field targets a role that is neither textbox nor combobox
    (this repair knows only the textbox<->combobox pair -- CLICK/SUBMIT/NAVIGATE/
    EXTRACT/DONE and any other role are never touched), if every field is already a
    textbox (nothing to repair), or if the SELECT capability a combobox field would
    need isn't in `policy.allowed_ops` or (when set) `ceiling`.
    """
    textbox_fields: list[FieldValue] = []
    combobox_fields: list[FieldValue] = []
    for field_value in step.fields:
        element = _element_by_ref(snapshot, field_value.ref)
        if element is None or element.role not in ("textbox", "combobox"):
            return None
        (textbox_fields if element.role == "textbox" else combobox_fields).append(field_value)

    if not combobox_fields:
        return None

    for field_value in combobox_fields:
        element = _element_by_ref(snapshot, field_value.ref)
        assert element is not None  # checked in the loop above
        capability: Capability = ("SELECT", page_origin, element.form_id)
        if not _repair_capability_ok(capability, policy, ceiling):
            return None

    new_steps: list[Step] = []
    if textbox_fields:
        new_steps.append(
            FillFormStep(fields=textbox_fields, consequential_hint=step.consequential_hint)
        )
    repairs: list[Repair] = []
    for field_value in combobox_fields:
        new_steps.append(
            SelectStep(
                ref=field_value.ref,
                value=field_value.value,
                consequential_hint=step.consequential_hint,
            )
        )
        repairs.append(Repair(index, field_value.ref, "FILL_FORM", "SELECT"))
    return new_steps, repairs


def _repair_select(
    step: SelectStep,
    index: int,
    snapshot: PageSnapshot,
    policy: Policy,
    ceiling: frozenset[Capability] | None,
    page_origin: str,
) -> tuple[Step, Repair] | None:
    """Repair one SELECT step, or return `None` to leave it untouched (unknown ref,
    a role other than textbox, or the FILL_FORM capability it would need isn't
    allowed/ceilinged)."""
    element = _element_by_ref(snapshot, step.ref)
    if element is None or element.role != "textbox":
        return None
    capability: Capability = ("FILL_FORM", page_origin, element.form_id)
    if not _repair_capability_ok(capability, policy, ceiling):
        return None
    new_step: Step = FillFormStep(
        fields=[FieldValue(ref=step.ref, value=step.value)],
        consequential_hint=step.consequential_hint,
    )
    return new_step, Repair(index, step.ref, "SELECT", "FILL_FORM")


def _build_repaired_steps(
    plan: Plan,
    snapshot: PageSnapshot,
    policy: Policy,
    ceiling: frozenset[Capability] | None,
    *,
    allow_split: bool,
) -> tuple[list[Step], list[Repair]]:
    page_origin = origin_of(snapshot.url)
    new_steps: list[Step] = []
    repairs: list[Repair] = []
    for i, step in enumerate(plan.steps):
        if allow_split and isinstance(step, FillFormStep):
            fixed = _split_fill_form(step, i, snapshot, policy, ceiling, page_origin)
            if fixed is not None:
                steps_out, step_repairs = fixed
                new_steps.extend(steps_out)
                repairs.extend(step_repairs)
                continue
        elif isinstance(step, SelectStep):
            fixed_select = _repair_select(step, i, snapshot, policy, ceiling, page_origin)
            if fixed_select is not None:
                new_step, repair = fixed_select
                new_steps.append(new_step)
                repairs.append(repair)
                continue
        new_steps.append(step)
    return new_steps, repairs


def repair_roles(
    plan: Plan,
    snapshot: PageSnapshot,
    policy: Policy,
    ceiling: frozenset[Capability] | None = None,
) -> tuple[Plan, list[Repair]]:
    """Deterministic pre-pass (docs/PLAN.md Q1): rewrite a FILL_FORM step that
    targets a `combobox` to SELECT, and a SELECT step that targets a `textbox` to
    FILL_FORM, before the plan reaches `validate_plan`. `commit_plan`
    (planner/plan.py) calls this on every attempt, passing its own current retry
    ceiling straight through.

    Deliberately does NOT touch: CLICK, SUBMIT, NAVIGATE, EXTRACT, DONE, or any
    role other than the textbox<->combobox pair; a step's ref, value binding,
    origin, or form; a locked ceiling's scope (a repair whose corrected capability
    isn't already in `ceiling` is declined, not applied); or invariant 3's
    sensitive-binding check -- a repaired FILL_FORM still goes through
    `validate_plan`'s `_check_sensitive_binding` exactly as a model-written one
    would, so SELECT's page-enumerated-option exemption does not carry over: a
    literal SELECT value repaired onto a sensitive textbox is still rejected, just
    one step later than before.

    A field/step this function declines to repair is left exactly as the model
    wrote it, so `validate_plan` rejects it with the same message as if this
    pre-pass didn't exist.

    Returns the plan unchanged (same object) and an empty repair list when nothing
    was repaired.
    """
    new_steps, repairs = _build_repaired_steps(plan, snapshot, policy, ceiling, allow_split=True)
    if repairs and len(new_steps) > policy.max_steps:
        # The split(s) pushed the plan over max_steps -- rejected, not truncated:
        # retry without any FILL_FORM split. SELECT->FILL_FORM conversions never
        # add a step, so they're unaffected by this budget.
        new_steps, repairs = _build_repaired_steps(
            plan, snapshot, policy, ceiling, allow_split=False
        )
    if not repairs:
        return plan, []
    return plan.model_copy(update={"steps": new_steps}), repairs
