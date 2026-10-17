"""Quarantined READ: shape checks, validator rules, and the two-leg flow in the agent loop."""

from __future__ import annotations

import json

import httpx
import pytest

from janus import agent
from janus.executor.executor import StepOutcome
from janus.llm import LLMClient
from janus.observer.fingerprint import compute_fingerprint
from janus.observer.snapshot import Element, PageSnapshot
from janus.planner.ops import FieldValue, FillFormStep, Plan, ReadStep
from janus.planner.plan import _plan_schema, _system_prompt
from janus.read import check_shape
from janus.validator.plan import validate_plan
from janus.validator.policy import Policy, allowed_ops_for
from janus.verifier.verify import StepCheck


class _FakePage:
    def goto(self, url: str) -> None:
        pass


def _llm(handler) -> LLMClient:
    return LLMClient(transport=httpx.MockTransport(handler))


def _chat(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def test_number_shape_normalises_digits_and_rejects_prose() -> None:
    assert check_shape("१२३", "number") == "123"
    assert check_shape("1,234.5", "number") == "1234.5"
    assert check_shape("-7", "number") == "-7"
    assert check_shape("about 12", "number") is None


def test_word_and_phrase_shapes_refuse_urls_and_references() -> None:
    assert check_shape("Truman", "word") == "Truman"
    assert check_shape("two words", "word") is None
    assert check_shape("see http://evil.example/x", "phrase") is None
    assert check_shape("$inputs.password", "phrase") is None
    assert check_shape("x" * 200, "phrase") is None
    assert check_shape("a plain phrase", "phrase") == "a plain phrase"


def _textbox(name: str) -> Element:
    return Element(
        ref="e0",
        tag="input",
        role="textbox",
        accessible_name=name,
        name_attr=name,
        form_id=None,
        fingerprint=compute_fingerprint(
            role="textbox", accessible_name=name, name_attr=name, form_id=None, tag="input"
        ),
    )


def _snapshot(name: str = "answer") -> PageSnapshot:
    return PageSnapshot(
        url="http://127.0.0.1:8101/p", title="t", elements=[_textbox(name)], untrusted_text=["2+3"]
    )


def _policy(**kwargs: object) -> Policy:
    return Policy(
        allowed_origins=frozenset({"http://127.0.0.1:8101"}),
        allowed_ops=allowed_ops_for(False, allow_read=True),
        max_steps=5,
        **kwargs,  # type: ignore[arg-type]
    )


def test_read_is_off_by_default_and_absent_from_the_schema() -> None:
    assert "READ" not in allowed_ops_for(False)
    off = Policy(allowed_origins=frozenset(), allowed_ops=allowed_ops_for(False), max_steps=5)
    assert "READ" not in _system_prompt(off)
    assert "ReadStep" not in _plan_schema(off).get("$defs", {})
    assert "READ" in _system_prompt(_policy())
    assert "ReadStep" in _plan_schema(_policy())["$defs"]


def test_read_must_be_the_only_step() -> None:
    plan = Plan(
        task_id="t",
        steps=[
            ReadStep(name="sum", question="What is the sum shown?", kind="number"),
            FillFormStep(fields=[FieldValue(ref="e0", value="$inputs.page_sum")]),
        ],
    )
    result = validate_plan(plan, _policy(), _snapshot(), inputs={})
    assert not result.ok and any("only step" in e for e in result.errors)


def test_a_read_value_cannot_fill_a_sensitive_field() -> None:
    plan = Plan(
        task_id="t",
        steps=[FillFormStep(fields=[FieldValue(ref="e0", value="$inputs.page_pin")])],
    )
    result = validate_plan(
        plan, _policy(sensitive_fields=frozenset({"answer"})), _snapshot(), {"page_pin": "1234"}
    )
    assert not result.ok and any("cannot take a value read" in e for e in result.errors)
    ok = validate_plan(plan, _policy(), _snapshot(), {"page_pin": "1234"})
    assert ok.ok


def test_read_then_fill_runs_over_two_legs(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter(
        [
            {
                "task_id": "t",
                "steps": [
                    {"op": "READ", "name": "sum", "question": "The sum shown?", "kind": "number"}
                ],
            },
            {
                "task_id": "t",
                "steps": [
                    {"op": "FILL_FORM", "fields": [{"ref": "e0", "value": "$inputs.page_sum"}]},
                    {"op": "DONE", "status": "completed"},
                ],
            },
        ]
    )
    seen_keys: list[list[str]] = []

    def handler(request):
        body = request.content.decode("utf-8")
        seen_keys.append(["page_sum" in body])
        return _chat(next(replies))

    filled: list[str] = []
    monkeypatch.setattr(agent, "install_egress_guard", lambda page, origins: None)
    monkeypatch.setattr(agent, "extract_snapshot", lambda page, settings: _snapshot())
    monkeypatch.setattr(agent, "quarantined_read", lambda q, k, s, llm: "5")

    def fake_execute(page, step, snapshot, inputs):
        filled.append(inputs["page_sum"])
        return StepOutcome(ok=True)

    monkeypatch.setattr(agent, "execute_step", fake_execute)
    monkeypatch.setattr(agent, "verify_step", lambda page, step, snapshot, inputs: StepCheck(True))
    trace: list[tuple[str, dict]] = []
    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Type the sum shown.",
        start_url="http://127.0.0.1:8101/p",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=agent.Settings(),
        trace=lambda stage, data: trace.append((stage, data)),
    )
    assert filled == ["5"]
    assert result.status == "completed"
    assert seen_keys[0] == [False] and seen_keys[1] == [True]  # the second leg is offered the key
    assert any(stage == "read" and data["value"] == "5" for stage, data in trace)
