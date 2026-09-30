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
from janus.policy import ApprovalTarget
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


def test_off_origin_navigate_correction_no_longer_false_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # P2 (docs/PLAN.md): the nag-13/share-12 chain. First attempt is a hallucinated
    # off-origin NAVIGATE, correctly rejected by the allowlist; before P2 that
    # rejection's own (off-allowlist) capabilities got locked in as the retry
    # ceiling, so the legitimate correction that followed was refused too, only for
    # "adds capabilities beyond committed" (docs/ERROR_ANALYSIS.md's diagnosed
    # false-block chain, 12/87 dev runs). Since a rejection that is itself an
    # allowlist/op-policy violation no longer locks a ceiling, the correction now
    # commits and runs instead of blocking.
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _chat(
                {"task_id": "t", "steps": [{"op": "NAVIGATE", "url": "http://evil.example/x"}]}
            )
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
        policy=_policy(allowed_ops=frozenset({"CLICK", "NAVIGATE", "DONE"})),
        llm=_llm(handler),
        settings=Settings(max_plan_retries=1),
    )
    assert calls["n"] == 2
    assert result.status == "completed"
    assert result.gate_block is None
    assert result.false_block_ceiling is False
    assert result.steps_run == 1


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


def test_approval_consumed_by_a_commit_ends_the_run_completed_with_no_done_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # docs/PLAN.md P4: a single-step plan, no DONE step at all -- the deterministic
    # stop must end the run `completed` on its own once the one declared approval
    # is consumed by a commit (consequential + caused_post).
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat({"task_id": "t", "steps": [{"op": "CLICK", "ref": "e0"}]})

    snapshot = _element_snapshot(tag="button", role="button", name="Confirm cancel")
    monkeypatch.setattr(agent, "install_egress_guard", lambda page, origins: None)
    monkeypatch.setattr(agent, "extract_snapshot", lambda page, settings: snapshot)
    monkeypatch.setattr(
        agent,
        "execute_step",
        lambda page, step, snapshot, inputs: StepOutcome(ok=True, caused_post=True),
    )
    monkeypatch.setattr(agent, "verify_step", lambda page, step, snapshot, inputs: StepCheck(True))

    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Cancel application 042.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(),
        approvals=[ApprovalTarget(action="cancel_application", names=["Confirm cancel"], path="*")],
    )
    assert result.status == "completed"
    assert result.steps_run == 1
    assert result.replans == 0
    assert result.over_action_count == 0


def test_a_second_commit_after_approvals_exhausted_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First leg: one commit consumes the only declared approval and ends the run.
    # This proves a queued second consequential step in the *same* plan never even
    # runs (the loop breaks immediately) -- the complementary browser-level test
    # proves a second commit attempted in a later leg is denied by authorize_action
    # once `granted` is empty.
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat(
            {
                "task_id": "t",
                "steps": [{"op": "CLICK", "ref": "e0"}, {"op": "CLICK", "ref": "e0"}],
            }
        )

    snapshot = _element_snapshot(tag="button", role="button", name="Confirm cancel")
    monkeypatch.setattr(agent, "install_egress_guard", lambda page, origins: None)
    monkeypatch.setattr(agent, "extract_snapshot", lambda page, settings: snapshot)
    monkeypatch.setattr(
        agent,
        "execute_step",
        lambda page, step, snapshot, inputs: StepOutcome(ok=True, caused_post=True),
    )
    monkeypatch.setattr(agent, "verify_step", lambda page, step, snapshot, inputs: StepCheck(True))

    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Cancel application 042.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(),
        approvals=[ApprovalTarget(action="cancel_application", names=["Confirm cancel"], path="*")],
    )
    assert result.status == "completed"
    assert result.steps_run == 1  # the second CLICK in the plan never ran
    assert result.over_action_count == 0


def test_a_click_that_does_not_cause_a_post_does_not_consume_an_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cancel *link* (a GET to a confirm page) is not a commit -- it must not
    # end the run or consume the declared approval on its own.
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat({"task_id": "t", "steps": [{"op": "CLICK", "ref": "e0"}]})

    snapshot = _element_snapshot(tag="a", role="link", name="Cancel")
    monkeypatch.setattr(agent, "install_egress_guard", lambda page, origins: None)
    monkeypatch.setattr(agent, "extract_snapshot", lambda page, settings: snapshot)
    monkeypatch.setattr(
        agent,
        "execute_step",
        lambda page, step, snapshot, inputs: StepOutcome(ok=True, caused_post=False),
    )
    monkeypatch.setattr(agent, "verify_step", lambda page, step, snapshot, inputs: StepCheck(True))

    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Cancel application 042.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(max_replan_attempts=0),
        approvals=[ApprovalTarget(action="cancel_application", names=["Cancel"], path="*")],
    )
    assert result.status == "partial"  # never claimed DONE, and the run wasn't ended for it


def test_an_id_bound_approval_for_a_different_target_still_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # docs/PLAN.md Q2: a control whose name matches an approval but whose row/path
    # id doesn't must still be denied -- the approval's own `id` binding, not just
    # `janus.policy.approval_matches` in isolation, has to flow through run_task's
    # element/row_key lookup.
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat({"task_id": "t", "steps": [{"op": "CLICK", "ref": "e0"}]})

    element = Element(
        ref="e0",
        tag="a",
        role="link",
        accessible_name="Cancel",
        name_attr=None,
        form_id=None,
        row_key="046",
        fingerprint=Fingerprint(
            role="link",
            accessible_name="Cancel",
            name_attr=None,
            form_id=None,
            tag="a",
            row_key="046",
        ),
    )
    snapshot = PageSnapshot(
        url="http://127.0.0.1:8101/applications", title="t", elements=[element], untrusted_text=[]
    )
    monkeypatch.setattr(agent, "install_egress_guard", lambda page, origins: None)
    monkeypatch.setattr(agent, "extract_snapshot", lambda page, settings: snapshot)
    monkeypatch.setattr(
        agent, "execute_step", lambda page, step, snapshot, inputs: StepOutcome(ok=True)
    )

    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Cancel application 045.",
        start_url="http://127.0.0.1:8101/applications",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(),
        approvals=[ApprovalTarget(action="cancel_application", names=["Cancel"], id="045")],
    )
    assert result.status == "blocked"
    assert result.gate_block == "authorize_action"
    assert result.steps_run == 0


def test_no_declared_approvals_keeps_current_done_self_report_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # approval_count=0 (the default, and every edit task): a commit-shaped step
    # (consequential, caused_post) must not trigger the new deterministic-stop
    # logic at all -- the run still needs a model-claimed DONE.
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat({"task_id": "t", "steps": [{"op": "CLICK", "ref": "e0"}]})

    snapshot = _element_snapshot(tag="button", role="button", name="Save")
    monkeypatch.setattr(agent, "install_egress_guard", lambda page, origins: None)
    monkeypatch.setattr(agent, "extract_snapshot", lambda page, settings: snapshot)
    monkeypatch.setattr(
        agent,
        "execute_step",
        lambda page, step, snapshot, inputs: StepOutcome(ok=True, caused_post=True),
    )
    monkeypatch.setattr(agent, "verify_step", lambda page, step, snapshot, inputs: StepCheck(True))

    result = agent.run_task(
        _FakePage(),
        task_id="t",
        instruction="Save it.",
        start_url="http://127.0.0.1:8101/services",
        inputs={},
        policy=_policy(),
        llm=_llm(handler),
        settings=Settings(max_replan_attempts=0),
    )
    assert result.status == "partial"  # no DONE step, approval_count=0: unchanged from before P4


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
