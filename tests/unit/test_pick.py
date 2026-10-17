"""PICK: a quarantined choice of one element by the page text around it."""

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
from janus.planner.ops import ClickStep, PickStep, Plan
from janus.planner.plan import _outline, _system_prompt
from janus.read import pick_candidates, quarantined_pick
from janus.validator.plan import validate_plan
from janus.validator.policy import Policy, allowed_ops_for
from janus.verifier.verify import StepCheck


def _link(ref: str, label: str, context: str) -> Element:
    return Element(
        ref=ref,
        tag="a",
        role="link",
        accessible_name=label,
        name_attr=None,
        form_id=None,
        fingerprint=compute_fingerprint(
            role="link", accessible_name=label, name_attr=None, form_id=None, tag="a"
        ),
        context=context,
    )


SNAP = PageSnapshot(
    url="http://127.0.0.1:9/inbox",
    title="Inbox",
    elements=[
        _link("e0", "Open", "From: Coletta — Lunch on Friday?"),
        _link("e1", "Open", "From: Evy — Invoice 42"),
    ],
    untrusted_text=[],
)
POLICY = Policy(
    allowed_origins=frozenset({"http://127.0.0.1:9"}),
    allowed_ops=allowed_ops_for(False, allow_read=True),
    max_steps=5,
)


def _chat(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def test_pick_is_part_of_the_read_switch() -> None:
    assert "PICK" not in allowed_ops_for(False)
    assert "PICK" in _system_prompt(POLICY)


def test_pick_must_be_alone() -> None:
    plan = Plan(
        task_id="t", steps=[PickStep(name="mail", question="from Evy"), ClickStep(ref="e0")]
    )
    assert any("only step" in e for e in validate_plan(plan, POLICY, SNAP, {}).errors)


def test_context_never_reaches_the_planner_outline() -> None:
    assert "Coletta" not in json.dumps(_outline(SNAP), ensure_ascii=False)
    marked = SNAP.model_copy(
        update={
            "elements": [SNAP.elements[0], SNAP.elements[1].model_copy(update={"picked": "mail"})]
        }
    )
    assert _outline(marked)[1]["picked"] == "mail"


def test_reader_can_only_answer_with_a_real_candidate() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return _chat({"working": "Evy wrote the invoice", "ref": "e1"})

    chosen = quarantined_pick(
        "the email from Evy", "link", SNAP, LLMClient(transport=httpx.MockTransport(handler))
    )
    assert chosen is not None and chosen.ref == "e1"
    schema = json.dumps(seen[0]["response_format"])
    assert '"e0"' in schema and '"e1"' in schema  # the answer is an enum of real refs
    assert [e.ref for e in pick_candidates(SNAP, "button")] == []


def test_pick_then_click_runs_over_two_legs(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter(
        [
            {
                "task_id": "t",
                "steps": [{"op": "PICK", "name": "mail", "question": "from Evy", "role": "link"}],
            },
            {
                "task_id": "t",
                "steps": [{"op": "CLICK", "ref": "e1"}, {"op": "DONE", "status": "completed"}],
            },
        ]
    )
    outlines: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        outlines.append(request.content.decode("utf-8"))
        return _chat(next(replies))

    clicked: list[str] = []
    monkeypatch.setattr(agent, "install_egress_guard", lambda page, origins: None)
    monkeypatch.setattr(agent, "extract_snapshot", lambda page, settings: SNAP)
    monkeypatch.setattr(agent, "quarantined_pick", lambda q, role, snap, llm: snap.elements[1])
    monkeypatch.setattr(
        agent,
        "execute_step",
        lambda page, step, snapshot, inputs: clicked.append(step.ref) or StepOutcome(ok=True),
    )
    monkeypatch.setattr(agent, "verify_step", lambda page, step, snapshot, inputs: StepCheck(True))

    class Page:
        def goto(self, url: str) -> None:
            pass

    result = agent.run_task(
        Page(),
        task_id="t",
        instruction="Open the email from Evy.",
        start_url=SNAP.url,
        inputs={},
        policy=POLICY,
        llm=LLMClient(transport=httpx.MockTransport(handler)),
        settings=Settings(),
    )
    assert result.status == "completed" and clicked == ["e1"]

    def elements(body: str) -> list[dict]:
        user = [m for m in json.loads(body)["messages"] if m["role"] == "user"][-1]
        return json.loads(user["content"])["elements"]

    assert all("picked" not in e for e in elements(outlines[0]))
    assert elements(outlines[1])[1]["picked"] == "mail"
