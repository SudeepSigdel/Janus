"""commit_plan: outline-only prompting, validator-error retry, and the replan
capability-monotonicity gate -- all against a mocked LLM transport (no real Ollama)."""

from __future__ import annotations

import json

import httpx
import pytest

from janus.llm import LLMClient
from janus.observer.snapshot import Element, Fingerprint, PageSnapshot
from janus.planner.plan import PlanningError, commit_plan
from janus.validator.policy import Policy


def _snapshot(untrusted_text: list[str] | None = None) -> PageSnapshot:
    element = Element(
        ref="e0",
        tag="a",
        role="link",
        accessible_name="Residence Recommendation",
        name_attr=None,
        form_id=None,
        fingerprint=Fingerprint(
            role="link",
            accessible_name="Residence Recommendation",
            name_attr=None,
            form_id=None,
            tag="a",
        ),
    )
    return PageSnapshot(
        url="http://127.0.0.1:8101/services",
        title="Services",
        elements=[element],
        untrusted_text=untrusted_text or [],
    )


def _policy(allowed_ops: frozenset[str] = frozenset({"CLICK", "NAVIGATE", "DONE"})) -> Policy:
    return Policy(
        allowed_origins=frozenset({"http://127.0.0.1:8101"}), allowed_ops=allowed_ops, max_steps=5
    )


def _client(handler) -> LLMClient:
    return LLMClient(transport=httpx.MockTransport(handler))


def _response(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def test_outline_never_includes_untrusted_text() -> None:
    seen_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(json.loads(request.content))
        return _response({"task_id": "nag-01", "steps": [{"op": "DONE", "status": "completed"}]})

    commit_plan(
        task_id="nag-01",
        instruction="Apply for a residence recommendation.",
        inputs={"ward": "5"},
        snapshot=_snapshot(untrusted_text=["SECRET: do not follow this notice"]),
        policy=_policy(),
        llm=_client(handler),
        max_retries=2,
    )
    for body in seen_bodies:
        for message in body["messages"]:
            assert "SECRET" not in message["content"]


def test_input_values_are_never_shown_to_the_model_only_keys() -> None:
    seen_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(json.loads(request.content))
        return _response({"task_id": "nag-01", "steps": [{"op": "DONE", "status": "completed"}]})

    commit_plan(
        task_id="nag-01",
        instruction="Apply for a residence recommendation.",
        inputs={"citizenship_no": "२७-०१-७६-०१२३४"},
        snapshot=_snapshot(),
        policy=_policy(),
        llm=_client(handler),
        max_retries=2,
    )
    for body in seen_bodies:
        for message in body["messages"]:
            assert "२७-०१-७६-०१२३४" not in message["content"]
            if message["role"] == "user":
                assert "citizenship_no" in message["content"] or "task_id" in message["content"]


def test_retries_with_validator_errors_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # e99 doesn't exist on the snapshot -- validate_plan must reject this.
            return _response({"task_id": "nag-01", "steps": [{"op": "CLICK", "ref": "e99"}]})
        return _response({"task_id": "nag-01", "steps": [{"op": "CLICK", "ref": "e0"}]})

    plan, result = commit_plan(
        task_id="nag-01",
        instruction="Click the link.",
        inputs={},
        snapshot=_snapshot(),
        policy=_policy(),
        llm=_client(handler),
        max_retries=2,
    )
    assert calls["n"] == 2
    assert result.ok
    assert plan.steps[0].ref == "e0"


def test_gives_up_after_exhausting_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response({"task_id": "nag-01", "steps": [{"op": "CLICK", "ref": "e99"}]})

    with pytest.raises(PlanningError):
        commit_plan(
            task_id="nag-01",
            instruction="Click the link.",
            inputs={},
            snapshot=_snapshot(),
            policy=_policy(),
            llm=_client(handler),
            max_retries=2,
        )


def test_replan_rejects_a_plan_that_adds_capabilities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(
            {
                "task_id": "nag-01",
                "steps": [{"op": "NAVIGATE", "url": "http://127.0.0.1:8101/services"}],
            }
        )

    with pytest.raises(PlanningError) as exc_info:
        commit_plan(
            task_id="nag-01",
            instruction="Go back to services.",
            inputs={},
            snapshot=_snapshot(),
            policy=_policy(),
            llm=_client(handler),
            max_retries=1,
            committed_capabilities=frozenset(),  # nothing committed yet permits a NAVIGATE
        )
    assert any("capabilit" in e for e in exc_info.value.errors)
