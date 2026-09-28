"""agent.run_task's orchestration: bounded replan, escalation, and status
classification -- exercised against real commit_plan/ground_plan/validate_plan (a
fake LLMClient stands in for Ollama) with the Page-touching primitives
(extract_snapshot, execute_step, verify_step, install_egress_guard) faked out, since
this is a unit test with no browser.
"""

from __future__ import annotations

import itertools
import json

import httpx
import pytest

import janus.llm as llm_module
from janus import agent
from janus.config import Settings
from janus.executor.executor import StepOutcome
from janus.llm import LLMClient
from janus.observer.snapshot import Element, Fingerprint, PageSnapshot
from janus.planner.ops import Step
from janus.validator.policy import Policy
from janus.verifier.verify import StepCheck


def test_normalize_inputs_derives_bs_date_from_ad() -> None:
    inputs = agent._normalize_inputs({"dob_ad": "2000-01-01"})
    assert inputs["dob_bs"] == "2056-09-17"
    assert inputs["dob_ad"] == "2000-01-01"


def test_normalize_inputs_leaves_an_existing_dob_bs_alone() -> None:
    inputs = agent._normalize_inputs({"dob_ad": "2000-01-01", "dob_bs": "1111-01-01"})
    assert inputs["dob_bs"] == "1111-01-01"


def test_normalize_inputs_is_a_noop_without_dob_ad() -> None:
    inputs = agent._normalize_inputs({"ward": "5"})
    assert inputs == {"ward": "5"}


class _FakePage:
    def __init__(self) -> None:
        self.navigated: list[str] = []

    def goto(self, url: str) -> None:
        self.navigated.append(url)


def _element_snapshot(*, tag: str, role: str, name: str) -> PageSnapshot:
    element = Element(
        ref="e0",
        tag=tag,
        role=role,
        accessible_name=name,
        name_attr=None,
        form_id=None,
        fingerprint=Fingerprint(
            role=role, accessible_name=name, name_attr=None, form_id=None, tag=tag
        ),
    )
    return PageSnapshot(
        url="http://127.0.0.1:8101/services", title="t", elements=[element], untrusted_text=[]
    )


def _snapshot() -> PageSnapshot:
    return _element_snapshot(tag="a", role="link", name="Next")


def _policy(allowed_ops: frozenset[str] = frozenset({"CLICK", "DONE"})) -> Policy:
    return Policy(
        allowed_origins=frozenset({"http://127.0.0.1:8101"}), allowed_ops=allowed_ops, max_steps=5
    )


def _llm(handler) -> LLMClient:
    return LLMClient(transport=httpx.MockTransport(handler))


def _chat(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def _patch_page_primitives(monkeypatch: pytest.MonkeyPatch, *, outcome: StepOutcome) -> None:
    monkeypatch.setattr(agent, "install_egress_guard", lambda page, origins: None)
    monkeypatch.setattr(agent, "extract_snapshot", lambda page, settings: _snapshot())
    monkeypatch.setattr(agent, "execute_step", lambda page, step, snapshot, inputs: outcome)
    monkeypatch.setattr(agent, "verify_step", lambda page, step, snapshot, inputs: StepCheck(True))


def test_completes_in_one_leg(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat(
            {
                "task_id": "t",
                "steps": [{"op": "CLICK", "ref": "e0"}, {"op": "DONE", "status": "completed"}],
            }
        )

    _patch_page_primitives(monkeypatch, outcome=StepOutcome(ok=True))
    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Click next.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(),
    )
    assert result.status == "completed"
    assert result.steps_run == 1
    assert result.replans == 0


def test_exhausts_replan_budget_without_done(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Never emits DONE: every leg re-commits the same single CLICK step.
        return _chat({"task_id": "t", "steps": [{"op": "CLICK", "ref": "e0"}]})

    _patch_page_primitives(monkeypatch, outcome=StepOutcome(ok=True))
    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Click next.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(max_replan_attempts=2),
    )
    assert result.status == "partial"
    assert result.replans == 2
    assert result.steps_run == 3


def test_a_blocked_outcome_overrides_a_claimed_done(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat(
            {
                "task_id": "t",
                "steps": [{"op": "CLICK", "ref": "e0"}, {"op": "DONE", "status": "completed"}],
            }
        )

    _patch_page_primitives(monkeypatch, outcome=StepOutcome(ok=False, blocked=True, reason="gone"))
    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Click next.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(),
    )
    assert result.status == "blocked"
    assert result.steps_run == 1


def test_consequential_step_is_blocked_without_escalation(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat({"task_id": "t", "steps": [{"op": "CLICK", "ref": "e0"}]})

    # "Submit application" trips the consequential keyword match; nothing grants it.
    snapshot = _element_snapshot(tag="button", role="button", name="Submit application")
    monkeypatch.setattr(agent, "install_egress_guard", lambda page, origins: None)
    monkeypatch.setattr(agent, "extract_snapshot", lambda page, settings: snapshot)
    monkeypatch.setattr(
        agent, "execute_step", lambda page, step, snapshot, inputs: StepOutcome(ok=True)
    )

    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Submit it.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(),
    )
    assert result.status == "blocked"
    assert result.steps_run == 0


def test_escalation_callback_grants_a_consequential_step(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat(
            {
                "task_id": "t",
                "steps": [{"op": "CLICK", "ref": "e0"}, {"op": "DONE", "status": "completed"}],
            }
        )

    snapshot = _element_snapshot(tag="button", role="button", name="Submit application")
    monkeypatch.setattr(agent, "install_egress_guard", lambda page, origins: None)
    monkeypatch.setattr(agent, "extract_snapshot", lambda page, settings: snapshot)
    monkeypatch.setattr(
        agent, "execute_step", lambda page, step, snapshot, inputs: StepOutcome(ok=True)
    )
    monkeypatch.setattr(agent, "verify_step", lambda page, step, snapshot, inputs: StepCheck(True))

    approved: list[Step] = []

    def escalate(step: Step, reason: str) -> bool:
        approved.append(step)
        return True

    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Submit it.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(),
        escalate=escalate,
    )
    assert result.status == "completed"
    assert len(approved) == 1


def test_gate_block_and_false_block_ceiling_on_a_capability_ceiling_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First attempt: off-origin NAVIGATE, correctly rejected -- locks the retry
    # ceiling to that rejected attempt's own capabilities (planner/plan.py). The
    # second attempt is a legitimate correction (CLICK a real element) but is
    # rejected anyway, only for "adds capabilities beyond committed" -- the exact
    # P0 chain (docs/ERROR_ANALYSIS.md) this milestone's false-block rule targets.
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _chat(
                {"task_id": "t", "steps": [{"op": "NAVIGATE", "url": "http://evil.example/x"}]}
            )
        return _chat({"task_id": "t", "steps": [{"op": "CLICK", "ref": "e0"}]})

    _patch_page_primitives(monkeypatch, outcome=StepOutcome(ok=True))
    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Click next.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(allowed_ops=frozenset({"CLICK", "NAVIGATE", "DONE"})),
        llm=_llm(handler),
        settings=Settings(max_plan_retries=1),
    )
    assert result.status == "blocked"
    assert result.gate_block == "validate_plan"
    assert result.false_block_ceiling is True
    # PlanningError.plan is the *last* attempt (the legitimate correction), not the
    # hallucinated first one -- that's the whole point of the false-block finding.
    assert result.blocked_step_op == "CLICK"
    assert result.blocked_step_label == "Next"


def test_gate_block_without_false_block_on_an_unrelated_validator_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every attempt targets an op the task's policy never allows at all -- a real
    # rejection, not a capability-ceiling artifact.
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat({"task_id": "t", "steps": [{"op": "CLICK", "ref": "e0"}]})

    _patch_page_primitives(monkeypatch, outcome=StepOutcome(ok=True))
    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Click next.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(allowed_ops=frozenset({"DONE"})),  # CLICK isn't allowed
        llm=_llm(handler),
        settings=Settings(max_plan_retries=1),
    )
    assert result.status == "blocked"
    assert result.gate_block == "validate_plan"
    assert result.false_block_ceiling is False


def test_gate_block_is_authorize_action_when_escalation_denies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat({"task_id": "t", "steps": [{"op": "CLICK", "ref": "e0"}]})

    snapshot = _element_snapshot(tag="button", role="button", name="Submit application")
    monkeypatch.setattr(agent, "install_egress_guard", lambda page, origins: None)
    monkeypatch.setattr(agent, "extract_snapshot", lambda page, settings: snapshot)
    monkeypatch.setattr(
        agent, "execute_step", lambda page, step, snapshot, inputs: StepOutcome(ok=True)
    )

    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Submit it.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(),
    )
    assert result.gate_block == "authorize_action"
    assert result.false_block_ceiling is False
    assert result.blocked_step_label == "Submit application"


def test_gate_block_is_execute_resolve_on_a_stale_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat({"task_id": "t", "steps": [{"op": "CLICK", "ref": "e0"}]})

    _patch_page_primitives(monkeypatch, outcome=StepOutcome(ok=False, blocked=True, reason="gone"))
    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Click next.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(),
    )
    assert result.gate_block == "execute_resolve"
    assert result.blocked_step_label == "Next"


def test_chat_calls_and_tokens_are_read_off_the_llm_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # A request against an in-memory MockTransport can finish inside one clock
    # tick, making `llm_time > 0` flaky on some platforms; control the clock.
    ticks = itertools.count(step=0.5)
    monkeypatch.setattr(llm_module.time, "monotonic", lambda: next(ticks))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"task_id": "t", "steps": [{"op": "DONE", "status": "completed"}]}
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 5},
            },
        )

    _patch_page_primitives(monkeypatch, outcome=StepOutcome(ok=True))
    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Click next.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(),
    )
    assert result.chat_calls == 1
    assert result.prompt_tokens == 50
    assert result.completion_tokens == 5
    assert result.llm_time > 0


def test_trace_is_a_noop_when_none_and_run_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat(
            {
                "task_id": "t",
                "steps": [{"op": "CLICK", "ref": "e0"}, {"op": "DONE", "status": "completed"}],
            }
        )

    _patch_page_primitives(monkeypatch, outcome=StepOutcome(ok=True))
    without_trace = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Click next.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(),
    )

    events: list[tuple[str, dict]] = []
    with_trace = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Click next.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(),
        trace=lambda stage, data: events.append((stage, data)),
    )
    assert without_trace.status == with_trace.status
    assert without_trace.steps_run == with_trace.steps_run
    assert without_trace.replans == with_trace.replans
    assert [e[0] for e in events] == [
        "snapshot",
        "plan_attempt",
        "authorize",
        "execute",
        "verify",
        "run_result",
    ]
