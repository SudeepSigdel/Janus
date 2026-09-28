"""commit_plan: outline-only prompting, validator-error retry, and the replan
capability-monotonicity gate -- all against a mocked LLM transport (no real Ollama)."""

from __future__ import annotations

import json

import httpx
import pytest

from janus.llm import LLMClient
from janus.observer.snapshot import Element, Fingerprint, PageSnapshot
from janus.planner.plan import PlanningError, _plan_schema, commit_plan
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


def test_plan_schema_omits_navigate_when_not_allowed() -> None:
    """P3 (docs/PLAN.md): constrained decoding -- NAVIGATE must be unreachable in the
    schema sent to the model, not just rejected after the fact."""
    schema = _plan_schema(_policy(allowed_ops=frozenset({"CLICK", "DONE"})))
    steps_items = schema["properties"]["steps"]["items"]
    assert "NAVIGATE" not in steps_items["discriminator"]["mapping"]
    assert "NavigateStep" not in schema["$defs"]
    assert all(ref["$ref"] != "#/$defs/NavigateStep" for ref in steps_items["oneOf"])


def test_plan_schema_includes_navigate_when_allowed() -> None:
    schema = _plan_schema(_policy(allowed_ops=frozenset({"CLICK", "NAVIGATE", "DONE"})))
    steps_items = schema["properties"]["steps"]["items"]
    assert "NAVIGATE" in steps_items["discriminator"]["mapping"]
    assert "NavigateStep" in schema["$defs"]


def test_plan_schema_drops_field_value_def_when_fill_form_not_allowed() -> None:
    schema = _plan_schema(_policy(allowed_ops=frozenset({"CLICK", "DONE"})))
    assert "FieldValue" not in schema["$defs"]


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


def test_outline_includes_row_only_for_elements_that_have_one() -> None:
    """docs/PLAN.md P5: `_outline` adds a "row" key when `Element.row_key` is set, and
    leaves it out entirely otherwise -- not bloating every entry with `"row": null`."""
    seen_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(json.loads(request.content))
        return _response({"task_id": "nag-04", "steps": [{"op": "DONE", "status": "completed"}]})

    row_element = Element(
        ref="e0",
        tag="a",
        role="link",
        accessible_name="Cancel",
        name_attr=None,
        form_id=None,
        fingerprint=Fingerprint(
            role="link", accessible_name="Cancel", name_attr=None, form_id=None, tag="a"
        ),
        row_key="045",
    )
    plain_element = Element(
        ref="e1",
        tag="a",
        role="link",
        accessible_name="Services",
        name_attr=None,
        form_id=None,
        fingerprint=Fingerprint(
            role="link", accessible_name="Services", name_attr=None, form_id=None, tag="a"
        ),
    )
    snapshot = PageSnapshot(
        url="http://127.0.0.1:8101/applications",
        title="Applications",
        elements=[row_element, plain_element],
        untrusted_text=[],
    )

    commit_plan(
        task_id="nag-04",
        instruction="Cancel application 045 only.",
        inputs={"app_no": "045"},
        snapshot=snapshot,
        policy=_policy(),
        llm=_client(handler),
        max_retries=2,
    )
    payload = json.loads(seen_bodies[0]["messages"][1]["content"])
    entries = {e["ref"]: e for e in payload["elements"]}
    assert entries["e0"]["row"] == "045"
    assert "row" not in entries["e1"]


def test_commit_plan_sends_the_op_restricted_schema_to_the_model() -> None:
    seen_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(json.loads(request.content))
        return _response({"task_id": "nag-01", "steps": [{"op": "CLICK", "ref": "e0"}]})

    commit_plan(
        task_id="nag-01",
        instruction="Click the link.",
        inputs={},
        snapshot=_snapshot(),
        policy=_policy(allowed_ops=frozenset({"CLICK", "DONE"})),
        llm=_client(handler),
        max_retries=2,
    )
    sent_schema = seen_bodies[0]["response_format"]["json_schema"]["schema"]
    assert "NAVIGATE" not in sent_schema["properties"]["steps"]["items"]["discriminator"]["mapping"]


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


def test_off_origin_navigate_does_not_lock_a_ceiling_for_the_correction() -> None:
    """P2 (docs/PLAN.md): the nag-13 chain -- a hallucinated off-origin NAVIGATE is
    rejected first, then a correct on-page CLICK must still commit rather than being
    refused for "adding capabilities" against a ceiling made of garbage."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _response(
                {
                    "task_id": "nag-13",
                    "steps": [{"op": "NAVIGATE", "url": "http://123.45.67.89/x"}],
                }
            )
        return _response({"task_id": "nag-13", "steps": [{"op": "CLICK", "ref": "e0"}]})

    plan, result = commit_plan(
        task_id="nag-13",
        instruction="Apply for a birth registration.",
        inputs={},
        snapshot=_snapshot(),
        policy=_policy(),
        llm=_client(handler),
        max_retries=2,
    )
    assert calls["n"] == 2
    assert result.ok
    assert plan.steps[0].op == "CLICK"


def test_off_origin_navigate_does_not_lock_a_ceiling_share_style() -> None:
    """P2: the share-12 chain -- same shape as nag-13, against a ShareSewa-style
    policy/origin, to cover both chains PLAN.md names explicitly."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _response(
                {
                    "task_id": "share-12",
                    "steps": [{"op": "NAVIGATE", "url": "http://198.51.100.7/evil"}],
                }
            )
        return _response({"task_id": "share-12", "steps": [{"op": "CLICK", "ref": "e0"}]})

    sharesewa_snapshot = PageSnapshot(
        url="http://127.0.0.1:8102/issues",
        title="Issues",
        elements=[
            Element(
                ref="e0",
                tag="a",
                role="link",
                accessible_name="Apply -- Sample Issue",
                name_attr=None,
                form_id=None,
                fingerprint=Fingerprint(
                    role="link",
                    accessible_name="Apply -- Sample Issue",
                    name_attr=None,
                    form_id=None,
                    tag="a",
                ),
            )
        ],
        untrusted_text=[],
    )
    policy = Policy(
        allowed_origins=frozenset({"http://127.0.0.1:8102"}),
        allowed_ops=frozenset({"CLICK", "NAVIGATE", "DONE"}),
        max_steps=5,
    )

    plan, result = commit_plan(
        task_id="share-12",
        instruction="Apply for the issue.",
        inputs={},
        snapshot=sharesewa_snapshot,
        policy=policy,
        llm=_client(handler),
        max_retries=2,
    )
    assert calls["n"] == 2
    assert result.ok
    assert plan.steps[0].op == "CLICK"


def test_a_non_scope_violation_rejection_still_locks_the_ceiling() -> None:
    """P2 regression: the fix must not remove invariant-2 enforcement outright. A
    first rejection that is NOT an allowlist/op-policy violation (here, an unknown
    ref -- still a real CLICK capability on this page/form) still locks its
    capabilities as the ceiling, so a later attempt that genuinely asks for a new
    capability (FILL_FORM on a real textbox) is still rejected."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _response({"task_id": "nag-01", "steps": [{"op": "CLICK", "ref": "e99"}]})
        return _response(
            {
                "task_id": "nag-01",
                "steps": [{"op": "FILL_FORM", "fields": [{"ref": "e1", "value": "$inputs.name"}]}],
            }
        )

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
    textbox = Element(
        ref="e1",
        tag="input",
        role="textbox",
        accessible_name="Name",
        name_attr="name",
        form_id="f1",
        fingerprint=Fingerprint(
            role="textbox", accessible_name="Name", name_attr="name", form_id="f1", tag="input"
        ),
    )
    snapshot = PageSnapshot(
        url="http://127.0.0.1:8101/services",
        title="Services",
        elements=[element, textbox],
        untrusted_text=[],
    )
    policy = Policy(
        allowed_origins=frozenset({"http://127.0.0.1:8101"}),
        allowed_ops=frozenset({"CLICK", "FILL_FORM", "NAVIGATE", "DONE"}),
        max_steps=5,
    )

    with pytest.raises(PlanningError) as exc_info:
        commit_plan(
            task_id="nag-01",
            instruction="Click the link.",
            inputs={"name": "x"},
            snapshot=snapshot,
            policy=policy,
            llm=_client(handler),
            max_retries=2,
        )
    # 3 attempts total (max_retries=2): the unknown-ref rejection, then the
    # capability-adding FILL_FORM rejected twice more (the model never corrects it).
    assert calls["n"] == 3
    assert any("adds capabilities beyond what was committed" in e for e in exc_info.value.errors)


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


def test_planning_error_carries_the_last_parsed_plan() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response({"task_id": "nag-01", "steps": [{"op": "CLICK", "ref": "e99"}]})

    with pytest.raises(PlanningError) as exc_info:
        commit_plan(
            task_id="nag-01",
            instruction="Click the link.",
            inputs={},
            snapshot=_snapshot(),
            policy=_policy(),
            llm=_client(handler),
            max_retries=1,
        )
    assert exc_info.value.plan is not None
    assert exc_info.value.plan.steps[0].ref == "e99"


def test_planning_error_plan_is_none_when_nothing_ever_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    with pytest.raises(PlanningError) as exc_info:
        commit_plan(
            task_id="nag-01",
            instruction="Click the link.",
            inputs={},
            snapshot=_snapshot(),
            policy=_policy(),
            llm=_client(handler),
            max_retries=1,
        )
    assert exc_info.value.plan is None


def test_trace_fires_once_per_attempt_and_is_a_noop_when_none() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _response({"task_id": "nag-01", "steps": [{"op": "CLICK", "ref": "e99"}]})
        return _response({"task_id": "nag-01", "steps": [{"op": "CLICK", "ref": "e0"}]})

    events: list[tuple[str, dict]] = []
    plan, result = commit_plan(
        task_id="nag-01",
        instruction="Click the link.",
        inputs={},
        snapshot=_snapshot(),
        policy=_policy(),
        llm=_client(handler),
        max_retries=2,
        trace=lambda stage, data: events.append((stage, data)),
    )
    assert [e[0] for e in events] == ["plan_attempt", "plan_attempt"]
    assert events[0][1]["ok"] is False
    assert events[1][1]["ok"] is True
    assert result.ok

    # trace=None (the default, used by every existing call site) must not change
    # behavior -- rerun the identical fixture without it.
    calls["n"] = 0
    plan2, result2 = commit_plan(
        task_id="nag-01",
        instruction="Click the link.",
        inputs={},
        snapshot=_snapshot(),
        policy=_policy(),
        llm=_client(handler),
        max_retries=2,
    )
    assert (plan2.steps[0].ref, result2.ok) == (plan.steps[0].ref, result.ok)


def test_trace_reports_invalid_json_attempts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    events: list[tuple[str, dict]] = []
    with pytest.raises(PlanningError):
        commit_plan(
            task_id="nag-01",
            instruction="Click the link.",
            inputs={},
            snapshot=_snapshot(),
            policy=_policy(),
            llm=_client(handler),
            max_retries=1,
            trace=lambda stage, data: events.append((stage, data)),
        )
    assert all(data["valid_json"] is False for _, data in events)
