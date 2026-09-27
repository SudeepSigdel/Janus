"""agent.run_task's orchestration: bounded replan, escalation, and status
classification -- exercised against real commit_plan/ground_plan/validate_plan (a
fake LLMClient stands in for Ollama) with the Page-touching primitives
(extract_snapshot, execute_step, verify_step, install_egress_guard) faked out, since
this is a unit test with no browser.
"""

from __future__ import annotations

import json

import httpx
import pytest

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
