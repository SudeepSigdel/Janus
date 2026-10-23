"""Login and second-factor support: ASK_USER, secret inputs and one-time-code fields."""

from __future__ import annotations

import json

import httpx
import pytest

from janus import agent
from janus.config import Settings
from janus.executor.executor import StepOutcome
from janus.llm import LLMClient
from janus.observer.fingerprint import compute_fingerprint
from janus.observer.snapshot import Element, PageSnapshot
from janus.planner.ops import AskUserStep, FieldValue, FillFormStep, Plan
from janus.planner.plan import _plan_schema, _system_prompt
from janus.validator.plan import validate_plan
from janus.validator.policy import Policy, allowed_ops_for
from janus.verifier.verify import StepCheck


def _field(
    ref: str, name: str, input_type: str = "text", autocomplete: str | None = None
) -> Element:
    return Element(
        ref=ref,
        tag="input",
        role="textbox",
        accessible_name=name,
        name_attr=name,
        form_id=None,
        fingerprint=compute_fingerprint(
            role="textbox", accessible_name=name, name_attr=name, form_id=None, tag="input"
        ),
        input_type=input_type,
        autocomplete=autocomplete,
    )


SNAP = PageSnapshot(
    url="http://127.0.0.1:8102/login",
    title="Login",
    elements=[
        _field("e0", "user"),
        _field("e1", "password", "password"),
        _field("e2", "otp", autocomplete="one-time-code"),
    ],
    untrusted_text=[],
)


def _policy(**kwargs: object) -> Policy:
    return Policy(
        allowed_origins=frozenset({"http://127.0.0.1:8102"}),
        allowed_ops=allowed_ops_for(False, allow_ask=True),
        max_steps=5,
        **kwargs,  # type: ignore[arg-type]
    )


def _fill(ref: str, value: str) -> Plan:
    return Plan(task_id="t", steps=[FillFormStep(fields=[FieldValue(ref=ref, value=value)])])


INPUTS = {"username": "u", "password": "p", "user_otp": "123456"}


def test_ask_user_is_off_by_default() -> None:
    off = Policy(allowed_origins=frozenset(), allowed_ops=allowed_ops_for(False), max_steps=5)
    assert "ASK_USER" not in _system_prompt(off)
    assert "AskUserStep" not in _plan_schema(off).get("$defs", {})
    assert "ASK_USER" in _system_prompt(_policy())


def test_ask_user_must_be_alone() -> None:
    plan = Plan(
        task_id="t",
        steps=[
            AskUserStep(name="otp", question="The code?"),
            *_fill("e0", "$inputs.username").steps,
        ],
    )
    assert any("only step" in e for e in validate_plan(plan, _policy(), SNAP, {}).errors)


def test_secret_only_into_a_password_field_and_vice_versa() -> None:
    policy = _policy(secret_inputs=frozenset({"password"}))
    assert validate_plan(_fill("e1", "$inputs.password"), policy, SNAP, INPUTS).ok
    leaked = validate_plan(_fill("e0", "$inputs.password"), policy, SNAP, INPUTS)
    assert any("is a secret" in e for e in leaked.errors)
    guessed = validate_plan(_fill("e1", "hunter2"), policy, SNAP, INPUTS)
    assert any("password field" in e for e in guessed.errors)
    wrong = validate_plan(_fill("e1", "$inputs.username"), policy, SNAP, INPUTS)
    assert not wrong.ok


def test_one_time_code_field_only_takes_a_value_the_user_gave() -> None:
    policy = _policy()
    assert validate_plan(_fill("e2", "$inputs.user_otp"), policy, SNAP, INPUTS).ok
    for value in ("482915", "$inputs.page_code"):
        result = validate_plan(_fill("e2", value), policy, SNAP, {**INPUTS, "page_code": "1"})
        assert any("one-time code" in e for e in result.errors)


def test_unknown_input_hint_mentions_ask_user() -> None:
    result = validate_plan(_fill("e0", "$inputs.otp"), _policy(), SNAP, {})
    assert any("ASK_USER" in e for e in result.errors)


def _chat(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


class _Page:
    def goto(self, url: str) -> None:
        pass


def _run(monkeypatch: pytest.MonkeyPatch, ask) -> tuple[agent.RunResult, list, list]:
    replies = iter(
        [
            {"task_id": "t", "steps": [{"op": "ASK_USER", "name": "otp", "question": "The code?"}]},
            {
                "task_id": "t",
                "steps": [
                    {"op": "FILL_FORM", "fields": [{"ref": "e2", "value": "$inputs.user_otp"}]},
                    {"op": "DONE", "status": "completed"},
                ],
            },
        ]
    )
    typed: list[str] = []
    trace: list = []
    monkeypatch.setattr(agent, "install_egress_guard", lambda page, origins: None)
    monkeypatch.setattr(agent, "extract_snapshot", lambda page, settings: SNAP)

    def execute(page, step, snapshot, inputs):
        typed.append(inputs["user_otp"])
        return StepOutcome(ok=True)

    monkeypatch.setattr(agent, "execute_step", execute)
    monkeypatch.setattr(agent, "verify_step", lambda page, step, snapshot, inputs: StepCheck(True))
    result = agent.run_task(
        _Page(),
        task_id="t",
        instruction="Log in.",
        start_url=SNAP.url,
        inputs={},
        policy=_policy(),
        llm=LLMClient(transport=httpx.MockTransport(lambda r: _chat(next(replies)))),
        settings=Settings(),
        trace=lambda s, d: trace.append((s, d)),
        ask_user=ask,
    )
    return result, typed, trace


def test_the_users_answer_is_used_and_never_traced(monkeypatch: pytest.MonkeyPatch) -> None:
    result, typed, trace = _run(monkeypatch, lambda question, secret: "482915")
    assert result.status == "completed" and typed == ["482915"]
    assert "482915" not in json.dumps(trace)
    assert any(s == "ask_user" and d["answered"] for s, d in trace)


def test_no_answer_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    result, typed, _ = _run(monkeypatch, lambda question, secret: None)
    assert result.status == "blocked" and result.gate_block == "ask_user" and typed == []


def test_read_is_refused_on_a_one_time_code_page() -> None:
    from janus.planner.ops import ReadStep

    policy = Policy(
        allowed_origins=frozenset({"http://127.0.0.1:8102"}),
        allowed_ops=allowed_ops_for(False, allow_read=True, allow_ask=True),
        max_steps=5,
    )
    plan = Plan(task_id="t", steps=[ReadStep(name="otp", question="The code sent?", kind="number")])
    result = validate_plan(plan, policy, SNAP, {})
    assert any("one-time code" in e and "ASK_USER" in e for e in result.errors)


def test_input_refs_are_repaired_only_when_unambiguous() -> None:
    from janus.validator.plan import repair_input_refs

    plan = _fill("e2", "$inputs.otp")
    fixed = repair_input_refs(plan, {"user_otp": "1"})
    assert fixed.steps[0].fields[0].value == "$inputs.user_otp"
    both = repair_input_refs(plan, {"user_otp": "1", "page_otp": "2"})
    assert both.steps[0].fields[0].value == "$inputs.otp"
    assert repair_input_refs(plan, {"otp": "3"}).steps[0].fields[0].value == "$inputs.otp"


def test_submitting_with_an_empty_password_field_is_sent_back() -> None:
    from janus.planner.ops import ClickStep

    button = Element(
        ref="e3",
        tag="button",
        role="button",
        accessible_name="Next",
        name_attr=None,
        form_id=None,
        fingerprint=compute_fingerprint(
            role="button", accessible_name="Next", name_attr=None, form_id=None, tag="button"
        ),
    )
    snap = SNAP.model_copy(update={"elements": [*SNAP.elements, button]})
    policy = _policy(secret_inputs=frozenset({"password"}))
    missing = Plan(task_id="t", steps=[*_fill("e0", "$inputs.username").steps, ClickStep(ref="e3")])
    assert any("leaves empty" in e for e in validate_plan(missing, policy, snap, INPUTS).errors)
    full = Plan(
        task_id="t",
        steps=[
            FillFormStep(
                fields=[
                    FieldValue(ref="e0", value="$inputs.username"),
                    FieldValue(ref="e1", value="$inputs.password"),
                ]
            ),
            ClickStep(ref="e3"),
        ],
    )
    assert validate_plan(full, policy, snap, INPUTS).ok
    # without user secrets (every benchmark task) the rule is off
    assert validate_plan(missing, _policy(), snap, INPUTS).ok


def test_submitting_past_a_required_empty_field_asks_the_user() -> None:
    from janus.planner.ops import ClickStep

    needed = _field("e0", "mother's name").model_copy(update={"required_empty": True})
    button = Element(
        ref="e1",
        tag="button",
        role="button",
        accessible_name="Next",
        name_attr=None,
        form_id=None,
        fingerprint=compute_fingerprint(
            role="button", accessible_name="Next", name_attr=None, form_id=None, tag="button"
        ),
    )
    snap = SNAP.model_copy(update={"elements": [needed, button]})
    skip = Plan(task_id="t", steps=[ClickStep(ref="e1")])
    errors = validate_plan(skip, _policy(), snap, {}).errors
    assert any("required and empty" in e and "ASK_USER" in e for e in errors)
    filled = Plan(
        task_id="t",
        steps=[FillFormStep(fields=[FieldValue(ref="e0", value="Sita")]), ClickStep(ref="e1")],
    )
    assert not any(
        "required and empty" in e for e in validate_plan(filled, _policy(), snap, {}).errors
    )
    off = Policy(allowed_origins=frozenset(), allowed_ops=allowed_ops_for(False), max_steps=5)
    assert not any("required and empty" in e for e in validate_plan(skip, off, snap, {}).errors)


def test_required_field_advice_points_at_an_answer_the_user_already_gave() -> None:
    from janus.planner.ops import ClickStep

    needed = _field("e0", "crn").model_copy(update={"required_empty": True})
    button = Element(
        ref="e1",
        tag="button",
        role="button",
        accessible_name="Next",
        name_attr=None,
        form_id=None,
        fingerprint=compute_fingerprint(
            role="button", accessible_name="Next", name_attr=None, form_id=None, tag="button"
        ),
    )
    snap = SNAP.model_copy(update={"elements": [needed, button]})
    skip = Plan(task_id="t", steps=[ClickStep(ref="e1")])
    errors = validate_plan(skip, _policy(), snap, {"user_crn": "X"}).errors
    assert any("$inputs.user_crn" in e and "plan a single ASK_USER" not in e for e in errors)
