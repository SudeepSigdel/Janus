"""The agent asking for a value the plan needs but the user never gave (janus/missing.py)."""

from __future__ import annotations

from janus.missing import needed_user_value
from janus.observer.fingerprint import compute_fingerprint
from janus.observer.snapshot import Element, PageSnapshot
from janus.planner.ops import ClickStep, FieldValue, FillFormStep, Plan
from janus.validator.policy import Policy, allowed_ops_for


def _field(ref: str, name: str, **extra) -> Element:
    return Element(
        ref=ref,
        tag="input",
        role="textbox",
        accessible_name=name,
        name_attr=name.split()[0].lower(),
        form_id="f",
        fingerprint=compute_fingerprint(
            role="textbox", accessible_name=name, name_attr=name, form_id="f", tag="input"
        ),
        input_type="text",
        **extra,
    )


def _button(ref: str) -> Element:
    return Element(
        ref=ref,
        tag="button",
        role="button",
        accessible_name="Next",
        name_attr=None,
        form_id="f",
        fingerprint=compute_fingerprint(
            role="button", accessible_name="Next", name_attr=None, form_id="f", tag="button"
        ),
    )


def _snap(*elements: Element) -> PageSnapshot:
    return PageSnapshot(
        url="http://127.0.0.1:8101/apply", title="Apply", elements=list(elements), untrusted_text=[]
    )


POLICY = Policy(
    allowed_origins=frozenset({"http://127.0.0.1:8101"}),
    allowed_ops=allowed_ops_for(False, allow_ask=True),
    max_steps=5,
    sensitive_fields=frozenset({"pin"}),
)


def _fill(ref: str, value: str) -> FillFormStep:
    return FillFormStep(fields=[FieldValue(ref=ref, value=value)])


def test_an_invented_input_becomes_a_question_built_from_the_field_label() -> None:
    snap = _snap(_field("e0", "Mobile number"), _button("e1"))
    plan = Plan(task_id="t", steps=[_fill("e0", "$inputs.mobile_no"), ClickStep(ref="e1")])
    needed = needed_user_value(plan, snap, POLICY, {"name": "x"})
    assert needed is not None
    assert needed.name == "mobile_no" and "Mobile number" in needed.question
    assert needed.secret is False


def test_a_required_empty_field_the_plan_skips_becomes_a_question() -> None:
    snap = _snap(_field("e0", "Mobile number", required_empty=True), _button("e1"))
    plan = Plan(task_id="t", steps=[ClickStep(ref="e1")])
    needed = needed_user_value(plan, snap, POLICY, {})
    assert needed is not None and needed.name == "mobile"


def test_a_value_the_user_already_gave_is_not_asked_twice() -> None:
    snap = _snap(_field("e0", "Mobile number"), _button("e1"))
    plan = Plan(task_id="t", steps=[_fill("e0", "$inputs.mobile_no"), ClickStep(ref="e1")])
    assert needed_user_value(plan, snap, POLICY, {"user_mobile_no": "9841234567"}) is None


def test_known_inputs_literals_and_derived_keys_need_no_question() -> None:
    snap = _snap(_field("e0", "Mobile number"), _button("e1"))
    for value in ("$inputs.mobile", "9841234567", "$inputs.user_mobile", "$inputs.page_mobile"):
        plan = Plan(task_id="t", steps=[_fill("e0", value), ClickStep(ref="e1")])
        assert needed_user_value(plan, snap, POLICY, {"mobile": "9841234567"}) is None


def test_a_password_or_sensitive_field_is_asked_as_a_secret() -> None:
    password = _field("e0", "Password").model_copy(update={"input_type": "password"})
    pin = _field("e1", "pin")
    snap = _snap(password, pin, _button("e2"))
    for ref, key in (("e0", "$inputs.pw"), ("e1", "$inputs.code")):
        plan = Plan(task_id="t", steps=[_fill(ref, key), ClickStep(ref="e2")])
        needed = needed_user_value(plan, snap, POLICY, {})
        assert needed is not None and needed.secret is True


def test_a_one_time_code_field_is_left_to_its_own_handling() -> None:
    otp = _field("e0", "Code", autocomplete="one-time-code")
    snap = _snap(otp, _button("e1"))
    plan = Plan(task_id="t", steps=[_fill("e0", "$inputs.otp"), ClickStep(ref="e1")])
    assert needed_user_value(plan, snap, POLICY, {}) is None


def test_the_question_never_contains_an_input_value() -> None:
    snap = _snap(_field("e0", "Mobile number"), _button("e1"))
    plan = Plan(task_id="t", steps=[_fill("e0", "$inputs.mobile_no"), ClickStep(ref="e1")])
    needed = needed_user_value(plan, snap, POLICY, {"name": "SECRET-VALUE-123"})
    assert needed is not None and "SECRET-VALUE-123" not in needed.question
