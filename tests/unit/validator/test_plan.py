"""validate_plan: the adversarial cases from docs/PLAN.md's M3 Accept criteria."""

from __future__ import annotations

from janus.observer.fingerprint import compute_fingerprint
from janus.observer.snapshot import Element, PageSnapshot
from janus.planner.ops import ClickStep, FieldValue, FillFormStep, NavigateStep, Plan, SubmitStep
from janus.validator.plan import validate_plan
from janus.validator.policy import Policy, capabilities_of


def make_element(
    ref: str, accessible_name: str, name_attr: str | None, form_id: str | None = "apply-form"
) -> Element:
    fingerprint = compute_fingerprint(
        role="textbox",
        accessible_name=accessible_name,
        name_attr=name_attr,
        form_id=form_id,
        tag="input",
    )
    return Element(
        ref=ref,
        tag="input",
        role="textbox",
        accessible_name=accessible_name,
        name_attr=name_attr,
        form_id=form_id,
        fingerprint=fingerprint,
    )


SNAPSHOT = PageSnapshot(
    url="http://127.0.0.1:8101/apply/residence-recommendation",
    title="Application",
    elements=[
        make_element("e0", "पूरा नाम / Full name", "name_ne"),
        make_element("e1", "वडा नं. / Ward no.", "ward"),
        make_element("e2", "पेश गर्नुहोस् / Submit application", None),
        make_element("e3", "रद्द / Cancel", None, form_id=None),
    ],
    untrusted_text=[],
)

POLICY = Policy(
    allowed_origins=frozenset({"http://127.0.0.1:8101"}),
    allowed_ops=frozenset(
        {"NAVIGATE", "FILL_FORM", "SELECT", "CLICK", "SUBMIT", "EXTRACT", "DONE"}
    ),
    max_steps=10,
    sensitive_fields=frozenset({"name_ne", "citizenship_no", "phone"}),
)


def test_off_allowlist_navigate_is_rejected() -> None:
    plan = Plan(task_id="t", steps=[NavigateStep(url="http://evil.example.com/steal")])
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={})
    assert not result.ok
    assert any("origin allowlist" in e for e in result.errors)


def test_on_allowlist_navigate_is_accepted() -> None:
    plan = Plan(task_id="t", steps=[NavigateStep(url="http://127.0.0.1:8101/services")])
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={})
    assert result.ok


def test_unknown_element_ref_is_rejected() -> None:
    plan = Plan(task_id="t", steps=[ClickStep(ref="e99")])
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={})
    assert not result.ok
    assert any("unknown element ref" in e for e in result.errors)


def test_literal_on_a_sensitive_field_is_rejected() -> None:
    plan = Plan(
        task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e0", value="सीता तामाङ")])]
    )
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={"name_ne": "सीता तामाङ"})
    assert not result.ok
    assert any("must bind via $inputs" in e for e in result.errors)


def test_input_ref_on_a_sensitive_field_is_accepted() -> None:
    plan = Plan(
        task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e0", value="$inputs.name_ne")])]
    )
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={"name_ne": "सीता तामाङ"})
    assert result.ok


def test_literal_on_a_non_sensitive_field_is_accepted() -> None:
    # ward is a page-enumerated select value, not sensitive PII (invariant 3).
    plan = Plan(task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e1", value="5")])])
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={})
    assert result.ok


def test_max_steps_exceeded_is_rejected() -> None:
    tight_policy = POLICY.model_copy(update={"max_steps": 1})
    plan = Plan(
        task_id="t",
        steps=[NavigateStep(url="http://127.0.0.1:8101/services"), ClickStep(ref="e3")],
    )
    result = validate_plan(plan, tight_policy, SNAPSHOT, inputs={})
    assert not result.ok
    assert any("max_steps" in e for e in result.errors)


def test_replan_that_adds_a_capability_is_rejected() -> None:
    committed_plan = Plan(task_id="t", steps=[ClickStep(ref="e2")])
    committed_capabilities = capabilities_of(committed_plan, SNAPSHOT)

    replan = Plan(task_id="t", steps=[ClickStep(ref="e2"), ClickStep(ref="e3")])
    result = validate_plan(
        replan, POLICY, SNAPSHOT, inputs={}, committed_capabilities=committed_capabilities
    )
    assert not result.ok
    assert any("adds capabilities" in e for e in result.errors)


def test_replan_within_committed_capabilities_is_accepted() -> None:
    committed_plan = Plan(task_id="t", steps=[ClickStep(ref="e2"), ClickStep(ref="e3")])
    committed_capabilities = capabilities_of(committed_plan, SNAPSHOT)

    replan = Plan(task_id="t", steps=[ClickStep(ref="e2")])
    result = validate_plan(
        replan, POLICY, SNAPSHOT, inputs={}, committed_capabilities=committed_capabilities
    )
    assert result.ok


def test_submit_step_is_flagged_consequential_even_with_a_false_hint() -> None:
    plan = Plan(task_id="t", steps=[SubmitStep(ref="e2", consequential_hint=False)])
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={})
    assert result.ok
    assert result.decisions[0].consequential is True


def test_click_hint_can_upgrade_a_non_keyword_element_to_consequential() -> None:
    plan = Plan(task_id="t", steps=[ClickStep(ref="e1", consequential_hint=True)])
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={})
    assert result.decisions[0].consequential is True


def test_click_on_plain_element_without_hint_is_not_consequential() -> None:
    plan = Plan(task_id="t", steps=[ClickStep(ref="e1")])
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={})
    assert result.decisions[0].consequential is False
