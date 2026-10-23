"""Find a value a plan needs but the user never gave, so the agent can ask for it.

A small planner often reaches for a value it does not have: it writes `$inputs.mobile_no` for a
required field, or submits past that field empty. Asking the user to plan an ASK_USER step is
brittle, so when a plan is refused for that reason the agent asks itself. Everything here is
deterministic: the field comes from the refused plan's own refs, the question is built from that
field's label (an outline label the planner already sees), and the answer is the person's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from janus.observer.snapshot import Element, PageSnapshot
from janus.planner.ops import ASK_INPUT_PREFIX, FillFormStep, Plan, input_ref_key
from janus.validator.plan import required_empty_fields
from janus.validator.policy import Policy

# Keys that already mean "the user's answer" or "a value read off the page".
_DERIVED_PREFIXES = ("user_", "page_")
MAX_AUTO_ASKS = 4


@dataclass(frozen=True)
class NeededValue:
    name: str  # the ASK_USER name: the answer becomes the input `user_<name>`
    question: str
    secret: bool


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    slug = re.sub(r"^[0-9_]+", "", slug)[:30].strip("_")
    return slug or "value"


def _needed(element: Element, name: str, policy: Policy) -> NeededValue:
    label = element.accessible_name or element.name_attr or "this field"
    return NeededValue(
        name=name,
        question=f"What should I enter for “{label}”?"[:200],
        secret=element.input_type == "password" or element.name_attr in policy.sensitive_fields,
    )


def needed_user_value(
    plan: Plan, snapshot: PageSnapshot, policy: Policy, inputs: dict[str, str]
) -> NeededValue | None:
    """The first field `plan` needs a user-given value for, or None. A name the user already
    answered is never asked twice, and one-time-code fields are left to their own handling."""
    by_ref = {e.ref: e for e in snapshot.elements}
    for step in plan.steps:
        if not isinstance(step, FillFormStep):
            continue
        for field in step.fields:
            key = input_ref_key(field.value)
            element = by_ref.get(field.ref)
            if (
                key is None
                or key in inputs
                or key.startswith(_DERIVED_PREFIXES)
                or element is None
                or element.role != "textbox"
                or element.autocomplete == "one-time-code"
            ):
                continue
            name = _slug(key)
            if f"{ASK_INPUT_PREFIX}{name}" not in inputs:
                return _needed(element, name, policy)
    for element in required_empty_fields(plan, snapshot):
        if element.autocomplete == "one-time-code":
            continue
        name = _slug(element.name_attr or element.accessible_name)
        if f"{ASK_INPUT_PREFIX}{name}" not in inputs:
            return _needed(element, name, policy)
    return None
