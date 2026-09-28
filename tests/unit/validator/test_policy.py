"""Capability extraction and the monotonicity (subset) check."""

from __future__ import annotations

from janus.observer.fingerprint import compute_fingerprint
from janus.observer.snapshot import Element, PageSnapshot
from janus.planner.ops import ClickStep, FieldValue, FillFormStep, NavigateStep, Plan, SubmitStep
from janus.validator.policy import allowed_ops_for, capabilities_of, is_capability_subset


def make_element(ref: str, name_attr: str | None, form_id: str | None = "apply-form") -> Element:
    fingerprint = compute_fingerprint(
        role="textbox", accessible_name=ref, name_attr=name_attr, form_id=form_id, tag="input"
    )
    return Element(
        ref=ref,
        tag="input",
        role="textbox",
        accessible_name=ref,
        name_attr=name_attr,
        form_id=form_id,
        fingerprint=fingerprint,
    )


SNAPSHOT = PageSnapshot(
    url="http://127.0.0.1:8101/apply/residence-recommendation",
    title="Application",
    elements=[
        make_element("e0", "name_ne"),
        make_element("e1", "phone"),
        make_element("e2", None, form_id=None),  # e.g. a nav link or cancel button
    ],
    untrusted_text=[],
)


def test_capabilities_of_a_navigate_and_fill_form_plan() -> None:
    plan = Plan(
        task_id="t",
        steps=[
            NavigateStep(url="http://127.0.0.1:8101/services"),
            FillFormStep(fields=[FieldValue(ref="e0", value="$inputs.name_ne")]),
            SubmitStep(ref="e0"),
        ],
    )
    capabilities = capabilities_of(plan, SNAPSHOT)
    assert ("NAVIGATE", "http://127.0.0.1:8101", None) in capabilities
    assert ("FILL_FORM", "http://127.0.0.1:8101", "apply-form") in capabilities
    assert ("SUBMIT", "http://127.0.0.1:8101", "apply-form") in capabilities
    assert len(capabilities) == 3


def test_identical_plan_is_a_subset_of_itself() -> None:
    plan = Plan(task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e0", value="x")])])
    capabilities = capabilities_of(plan, SNAPSHOT)
    assert is_capability_subset(capabilities, capabilities)


def test_replan_adding_a_new_capability_fails_the_subset_check() -> None:
    committed_plan = Plan(
        task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e0", value="$inputs.name_ne")])]
    )
    committed = capabilities_of(committed_plan, SNAPSHOT)

    replan = Plan(
        task_id="t",
        steps=[
            FillFormStep(fields=[FieldValue(ref="e0", value="$inputs.name_ne")]),
            ClickStep(ref="e2"),  # a capability not present in the committed plan
        ],
    )
    new_capabilities = capabilities_of(replan, SNAPSHOT)
    assert not is_capability_subset(new_capabilities, committed)


def test_allowed_ops_for_excludes_navigate_by_default() -> None:
    """P3 (docs/PLAN.md): no dev task needs a mid-task NAVIGATE, so a task's default
    policy shouldn't even offer it to the planner."""
    ops = allowed_ops_for(allow_navigate=False)
    assert "NAVIGATE" not in ops
    assert {"FILL_FORM", "SELECT", "CLICK", "SUBMIT", "EXTRACT", "DONE"} <= ops


def test_allowed_ops_for_includes_navigate_when_opted_in() -> None:
    ops = allowed_ops_for(allow_navigate=True)
    assert "NAVIGATE" in ops
    assert {"FILL_FORM", "SELECT", "CLICK", "SUBMIT", "EXTRACT", "DONE"} <= ops


def test_replan_that_only_drops_steps_still_passes() -> None:
    committed_plan = Plan(
        task_id="t",
        steps=[
            FillFormStep(fields=[FieldValue(ref="e0", value="$inputs.name_ne")]),
            SubmitStep(ref="e0"),
        ],
    )
    committed = capabilities_of(committed_plan, SNAPSHOT)

    replan = Plan(
        task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e0", value="$inputs.name_ne")])]
    )
    new_capabilities = capabilities_of(replan, SNAPSHOT)
    assert is_capability_subset(new_capabilities, committed)
