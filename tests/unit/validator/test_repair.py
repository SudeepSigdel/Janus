"""repair_roles (docs/PLAN.md Q1): the adversarial cases from Q1's own security
review, one per bullet, plus the normal-case rewrites and splits."""

from __future__ import annotations

from janus.observer.fingerprint import compute_fingerprint
from janus.observer.snapshot import Element, PageSnapshot
from janus.planner.ops import (
    ClickStep,
    FieldValue,
    FillFormStep,
    Plan,
    SelectStep,
    SubmitStep,
)
from janus.validator.plan import repair_roles, validate_plan
from janus.validator.policy import Policy, capabilities_of


def make_element(
    ref: str,
    accessible_name: str,
    name_attr: str | None,
    form_id: str | None = "apply-form",
    role: str = "textbox",
) -> Element:
    tag = {"textbox": "input", "combobox": "select", "checkbox": "input"}.get(role, "button")
    fingerprint = compute_fingerprint(
        role=role, accessible_name=accessible_name, name_attr=name_attr, form_id=form_id, tag=tag
    )
    return Element(
        ref=ref,
        tag=tag,
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
        make_element("e1", "वडा नं. / Ward no. (select)", "ward_select", role="combobox"),
        make_element("e2", "जिल्ला / District (select)", "district_select", role="combobox"),
        make_element("e3", "पेश गर्नुहोस् / Submit", None, role="button"),
        make_element("e4", "रद्द / Cancel", None, form_id=None, role="link"),
        make_element("e5", "नागरिकता नं. / Citizenship no.", "citizenship_no"),
        make_element("e6", "सहमत / Agree", None, role="checkbox"),
    ],
    untrusted_text=[],
)

POLICY = Policy(
    allowed_origins=frozenset({"http://127.0.0.1:8101"}),
    allowed_ops=frozenset(
        {"NAVIGATE", "FILL_FORM", "SELECT", "CLICK", "SUBMIT", "EXTRACT", "DONE"}
    ),
    max_steps=10,
    sensitive_fields=frozenset({"name_ne", "citizenship_no"}),
)


def test_fill_form_on_combobox_is_rewritten_to_select() -> None:
    plan = Plan(task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e1", value="5")])])
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert [s.op for s in repaired.steps] == ["SELECT"]
    assert repaired.steps[0].ref == "e1"
    assert repaired.steps[0].value == "5"
    assert repairs == [
        type(repairs[0])(step_index=0, ref="e1", from_op="FILL_FORM", to_op="SELECT")
    ]


def test_select_on_textbox_is_rewritten_to_fill_form() -> None:
    plan = Plan(task_id="t", steps=[SelectStep(ref="e0", value="x")])
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert [s.op for s in repaired.steps] == ["FILL_FORM"]
    assert repaired.steps[0].fields == [FieldValue(ref="e0", value="x")]
    assert len(repairs) == 1
    assert (repairs[0].from_op, repairs[0].to_op) == ("SELECT", "FILL_FORM")


def test_mixed_fill_form_splits_textbox_and_combobox_fields() -> None:
    plan = Plan(
        task_id="t",
        steps=[
            FillFormStep(
                fields=[
                    FieldValue(ref="e0", value="$inputs.name_ne"),
                    FieldValue(ref="e1", value="5"),
                    FieldValue(ref="e2", value="3"),
                ]
            )
        ],
    )
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert [s.op for s in repaired.steps] == ["FILL_FORM", "SELECT", "SELECT"]
    assert repaired.steps[0].fields == [FieldValue(ref="e0", value="$inputs.name_ne")]
    assert [s.ref for s in repaired.steps[1:]] == ["e1", "e2"]
    assert {(r.ref, r.from_op, r.to_op) for r in repairs} == {
        ("e1", "FILL_FORM", "SELECT"),
        ("e2", "FILL_FORM", "SELECT"),
    }


def test_all_combobox_fill_form_leaves_no_empty_fill_form_step() -> None:
    plan = Plan(
        task_id="t",
        steps=[
            FillFormStep(fields=[FieldValue(ref="e1", value="5"), FieldValue(ref="e2", value="3")])
        ],
    )
    repaired, _ = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert [s.op for s in repaired.steps] == ["SELECT", "SELECT"]


def test_correct_plan_is_returned_unchanged_with_no_repairs() -> None:
    plan = Plan(
        task_id="t",
        steps=[
            FillFormStep(fields=[FieldValue(ref="e0", value="$inputs.name_ne")]),
            SelectStep(ref="e1", value="5"),
        ],
    )
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert repaired is plan
    assert repairs == []


def test_click_on_combobox_is_never_rewritten() -> None:
    plan = Plan(task_id="t", steps=[ClickStep(ref="e1")])
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert repaired is plan
    assert repairs == []


def test_submit_on_combobox_is_never_rewritten() -> None:
    plan = Plan(task_id="t", steps=[SubmitStep(ref="e1")])
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert repaired is plan
    assert repairs == []


def test_fill_form_targeting_a_checkbox_is_left_whole() -> None:
    # e6 is neither textbox nor combobox: this repair only knows that pair, so the
    # whole step is left alone rather than partially repaired.
    plan = Plan(
        task_id="t",
        steps=[
            FillFormStep(
                fields=[
                    FieldValue(ref="e0", value="$inputs.name_ne"),
                    FieldValue(ref="e6", value="y"),
                ]
            )
        ],
    )
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert repaired is plan
    assert repairs == []


def test_fill_form_with_unknown_ref_is_left_whole() -> None:
    plan = Plan(
        task_id="t",
        steps=[
            FillFormStep(fields=[FieldValue(ref="e1", value="5"), FieldValue(ref="e99", value="y")])
        ],
    )
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert repaired is plan
    assert repairs == []


def test_select_targeting_a_button_is_left_alone() -> None:
    plan = Plan(task_id="t", steps=[SelectStep(ref="e3", value="x")])
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert repaired is plan
    assert repairs == []


def test_select_with_unknown_ref_is_left_alone() -> None:
    plan = Plan(task_id="t", steps=[SelectStep(ref="e99", value="x")])
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert repaired is plan
    assert repairs == []


def test_repair_declined_when_new_op_not_in_policy_allowed_ops() -> None:
    policy = POLICY.model_copy(update={"allowed_ops": POLICY.allowed_ops - {"SELECT"}})
    plan = Plan(task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e1", value="5")])])
    repaired, repairs = repair_roles(plan, SNAPSHOT, policy, None)
    assert repaired is plan
    assert repairs == []


def test_repair_declined_when_ceiling_excludes_the_new_capability() -> None:
    # Ceiling locked from an unrelated CLICK -- no SELECT capability in it.
    ceiling_plan = Plan(task_id="t", steps=[ClickStep(ref="e3")])
    ceiling = capabilities_of(ceiling_plan, SNAPSHOT)
    plan = Plan(task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e1", value="5")])])
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, ceiling)
    assert repaired is plan
    assert repairs == []


def test_repair_allowed_when_ceiling_includes_the_new_capability() -> None:
    ceiling_plan = Plan(task_id="t", steps=[SelectStep(ref="e1", value="5")])
    ceiling = capabilities_of(ceiling_plan, SNAPSHOT)
    plan = Plan(task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e1", value="5")])])
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, ceiling)
    assert [s.op for s in repaired.steps] == ["SELECT"]
    assert len(repairs) == 1


def test_repair_preserves_ref_value_and_form() -> None:
    plan = Plan(task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e2", value="3")])])
    repaired, _ = repair_roles(plan, SNAPSHOT, POLICY, None)
    step = repaired.steps[0]
    assert step.ref == "e2"
    assert step.value == "3"
    # The repaired step's capability must still resolve to e2's own form/origin --
    # unchanged from what the model itself targeted.
    (capability,) = capabilities_of(repaired, SNAPSHOT)
    assert capability == ("SELECT", "http://127.0.0.1:8101", "apply-form")


def test_hint_survives_repair_so_consequential_can_only_go_up() -> None:
    plan = Plan(task_id="t", steps=[SelectStep(ref="e0", value="x", consequential_hint=True)])
    repaired, _ = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert repaired.steps[0].consequential_hint is True
    result = validate_plan(repaired, POLICY, SNAPSHOT, inputs={})
    assert result.decisions[0].consequential is True


def test_select_literal_on_sensitive_textbox_is_repaired_then_rejected() -> None:
    # SELECT's page-enumerated-option exemption from invariant 3 does not carry
    # over: once repaired to FILL_FORM, a literal on a sensitive field is rejected
    # exactly as a model-written FILL_FORM literal would be.
    plan = Plan(task_id="t", steps=[SelectStep(ref="e5", value="literal-value")])
    repaired, repairs = repair_roles(plan, SNAPSHOT, POLICY, None)
    assert len(repairs) == 1
    result = validate_plan(repaired, POLICY, SNAPSHOT, inputs={})
    assert not result.ok
    assert any("must bind via $inputs" in e for e in result.errors)


def test_split_that_would_exceed_max_steps_is_declined() -> None:
    tight_policy = POLICY.model_copy(update={"max_steps": 1})
    plan = Plan(
        task_id="t",
        steps=[
            FillFormStep(
                fields=[
                    FieldValue(ref="e0", value="$inputs.name_ne"),
                    FieldValue(ref="e1", value="5"),
                ]
            )
        ],
    )
    repaired, repairs = repair_roles(plan, SNAPSHOT, tight_policy, None)
    # Declined, not truncated: the split would grow 1 step to 2, over max_steps=1.
    assert repaired is plan
    assert repairs == []
    # validate_plan still rejects the untouched step exactly as before Q1.
    result = validate_plan(repaired, tight_policy, SNAPSHOT, inputs={})
    assert not result.ok
    assert any("not fillable" in e for e in result.errors)


def test_split_within_max_steps_is_applied() -> None:
    fitting_policy = POLICY.model_copy(update={"max_steps": 2})
    plan = Plan(
        task_id="t",
        steps=[
            FillFormStep(
                fields=[
                    FieldValue(ref="e0", value="$inputs.name_ne"),
                    FieldValue(ref="e1", value="5"),
                ]
            )
        ],
    )
    repaired, repairs = repair_roles(plan, SNAPSHOT, fitting_policy, None)
    assert [s.op for s in repaired.steps] == ["FILL_FORM", "SELECT"]
    assert len(repairs) == 1


def test_original_plan_object_is_not_mutated() -> None:
    plan = Plan(task_id="t", steps=[FillFormStep(fields=[FieldValue(ref="e1", value="5")])])
    original_steps = list(plan.steps)
    repair_roles(plan, SNAPSHOT, POLICY, None)
    assert plan.steps == original_steps
