"""validate_plan: the sole gate a planner-produced Plan passes through before any
step may be authorized (CLAUDE.md: "Only deterministic code in janus/validator/
authorizes actions").

Checks, all deterministic and model-free:
  - every step's op is in the task's allowed ops, and the plan doesn't exceed max_steps
  - NAVIGATE targets are on the origin allowlist (invariant 6)
  - every element ref a step touches actually exists on the current snapshot
  - a sensitive field (by name attribute) is bound via `$inputs.<key>`, never a literal
    the model could have invented (invariant 3)
  - when replanning (`committed_capabilities` given), the new plan's capabilities are a
    subset of what was already committed (invariant 2)

It also computes each step's effective consequential flag: the deterministic
classifier OR'd with the step's own hint, so a hint can only ever upgrade, never
downgrade (invariant 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from janus.observer.snapshot import Element, PageSnapshot
from janus.planner.ops import Plan, input_ref_key
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
