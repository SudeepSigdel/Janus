"""validate_plan: the adversarial cases from docs/PLAN.md's M3 Accept criteria."""

from __future__ import annotations

from janus.observer.fingerprint import compute_fingerprint
from janus.observer.snapshot import Element, PageSnapshot
from janus.planner.ops import (
    ClickStep,
    DoneStep,
    FieldValue,
    FillFormStep,
    NavigateStep,
    Plan,
    SelectStep,
    SubmitStep,
)
from janus.validator.plan import validate_plan
from janus.validator.policy import Policy, capabilities_of


def make_element(
    ref: str,
    accessible_name: str,
    name_attr: str | None,
    form_id: str | None = "apply-form",
    role: str = "textbox",
) -> Element:
    fingerprint = compute_fingerprint(
        role=role,
        accessible_name=accessible_name,
        name_attr=name_attr,
        form_id=form_id,
        tag="input" if role == "textbox" else "select",
    )
    return Element(
        ref=ref,
        tag="input" if role == "textbox" else "select",
        role=role,
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
        make_element("e4", "वडा नं. / Ward no. (select)", "ward_select", role="combobox"),
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


def test_navigate_rejected_when_not_in_allowed_ops() -> None:
    """P3 (docs/PLAN.md): a task's default policy excludes NAVIGATE. Even an
    otherwise-legitimate, on-origin NAVIGATE must still be rejected by the
    deterministic op-policy check -- constrained decoding (planner/plan.py's schema)
    is a belt, this is the suspenders."""
    policy = POLICY.model_copy(update={"allowed_ops": POLICY.allowed_ops - {"NAVIGATE"}})
    plan = Plan(task_id="t", steps=[NavigateStep(url="http://127.0.0.1:8101/services")])
    result = validate_plan(plan, policy, SNAPSHOT, inputs={})
    assert not result.ok
    assert any("not in the allowed ops" in e for e in result.errors)


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


def test_fill_form_targeting_a_combobox_is_rejected() -> None:
    # A real planner model once did exactly this instead of using SELECT; catching
    # it here means a validator error instead of execute_step crashing on Playwright's
    # ElementHandle.fill() (which only works on textbox/textarea elements).
    plan = Plan(task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e4", value="5")])])
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={})
    assert not result.ok
    assert any("not fillable" in e for e in result.errors)


def test_select_targeting_a_textbox_is_rejected() -> None:
    plan = Plan(task_id="t", steps=[SelectStep(ref="e1", value="5")])
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={})
    assert not result.ok
    assert any("not selectable" in e for e in result.errors)


def test_select_targeting_a_combobox_is_accepted() -> None:
    plan = Plan(task_id="t", steps=[SelectStep(ref="e4", value="5")])
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={})
    assert result.ok


def test_unknown_input_key_in_fill_form_is_rejected() -> None:
    """E18 share-06: `$inputs.bank` copied from the worked example on a task without a
    `bank` input passed validation and crashed the executor with KeyError."""
    plan = Plan(
        task_id="t",
        steps=[FillFormStep(fields=[FieldValue(ref="e1", value="$inputs.bank")])],
    )
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={"ward": "5"})
    assert not result.ok
    assert any("$inputs.bank is not an input of this task" in e for e in result.errors)
    assert any("available input keys: ward" in e for e in result.errors)


def test_unknown_input_key_in_select_is_rejected() -> None:
    plan = Plan(task_id="t", steps=[SelectStep(ref="e4", value="$inputs.bank")])
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={"ward": "5"})
    assert not result.ok
    assert any("$inputs.bank is not an input of this task" in e for e in result.errors)


def test_known_input_key_is_accepted() -> None:
    plan = Plan(task_id="t", steps=[SelectStep(ref="e4", value="$inputs.ward")])
    assert validate_plan(plan, POLICY, SNAPSHOT, inputs={"ward": "5"}).ok


def test_click_on_a_no_effect_ref_is_rejected() -> None:
    plan = Plan(task_id="t", steps=[ClickStep(ref="e3")])
    result = validate_plan(plan, POLICY, SNAPSHOT, inputs={}, no_effect_refs=frozenset({"e3"}))
    assert not result.ok
    assert any("nothing changed" in e for e in result.errors)


def test_no_effect_guard_never_blocks_a_submit_or_other_refs() -> None:
    """A submit that came back with validation errors must stay retryable."""
    plan = Plan(task_id="t", steps=[SubmitStep(ref="e2")])
    assert validate_plan(plan, POLICY, SNAPSHOT, inputs={}, no_effect_refs=frozenset({"e2"})).ok
    plan = Plan(task_id="t", steps=[ClickStep(ref="e2")])
    assert validate_plan(plan, POLICY, SNAPSHOT, inputs={}, no_effect_refs=frozenset({"e3"})).ok


def test_click_on_a_current_page_link_is_rejected() -> None:
    """B4a: a link to the page already open is a no-op; reject it before acting."""
    here = make_element("e5", "Forums", None, form_id=None, role="link")
    snapshot = SNAPSHOT.model_copy(
        update={"elements": [*SNAPSHOT.elements, here.model_copy(update={"current_page": True})]}
    )
    result = validate_plan(
        Plan(task_id="t", steps=[ClickStep(ref="e5")]), POLICY, snapshot, inputs={}
    )
    assert not result.ok
    assert any("already open" in e for e in result.errors)
    assert validate_plan(
        Plan(task_id="t", steps=[ClickStep(ref="e3")]), POLICY, snapshot, inputs={}
    ).ok


def test_done_grants_no_capability() -> None:
    """B4b: a replan that only adds DONE stays inside a ceiling committed without it."""
    committed = capabilities_of(Plan(task_id="t", steps=[ClickStep(ref="e3")]), SNAPSHOT)
    replan = Plan(task_id="t", steps=[ClickStep(ref="e3"), DoneStep(status="completed")])
    assert all(op != "DONE" for op, _, _ in capabilities_of(replan, SNAPSHOT))
    assert validate_plan(replan, POLICY, SNAPSHOT, inputs={}, committed_capabilities=committed).ok
