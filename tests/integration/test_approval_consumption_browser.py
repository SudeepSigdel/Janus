"""End-to-end: agent.run_task's deterministic stop (docs/PLAN.md P4, generalized by
Q2's per-target `ApprovalTarget` matching) against the real nagarpalika/sharesewa
replicas, driven by a scripted fake LLM (httpx.MockTransport) rather than a real
model -- this tests the orchestrator loop's own approval-consumption/denial logic,
not planning, so it stays `browser`-only (no `ollama` marker, no live Ollama needed).
Each scripted response is computed from the page's *actual* current snapshot at call
time (same helpers as test_executor_browser.py), so it stays correct regardless of
exactly how the observer numbers refs.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from janus.agent import run_task
from janus.config import Settings
from janus.llm import LLMClient
from janus.observer.extract import INTERACTIVE_SELECTOR, extract_snapshot
from janus.observer.snapshot import PageSnapshot
from janus.policy import ApprovalTarget
from janus.validator.policy import Policy
from janus_bench.harness.server import running_site

pytestmark = pytest.mark.browser

_CANCEL_NAMES = ["रद्द / Cancel", "रद्द गर्नुहोस् / Confirm cancel"]
_SUBMIT_NAMES = ["अर्को / Next: review", "पेश गर्नुहोस् / Submit application"]
_APPLY_NAMES = ["अर्को / Next: review", "आवेदन पेश गर्नुहोस् / Submit application"]


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    with running_site("nagarpalika") as url:
        yield url


@pytest.fixture(scope="module")
def sharesewa_base_url() -> Iterator[str]:
    with running_site("sharesewa") as url:
        yield url


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture()
def page(browser: Browser) -> Iterator[Page]:
    page = browser.new_page()
    try:
        yield page
    finally:
        page.close()


def _reset(base_url: str, variant: str | None = None) -> None:
    httpx.post(f"{base_url}/__bench/reset", json={"variant": variant}, timeout=5).raise_for_status()


def _state(base_url: str) -> dict:
    return httpx.get(f"{base_url}/__bench/state", timeout=5).json()


def _ref(snapshot: PageSnapshot, *, name_attr: str | None = None, text: str | None = None) -> str:
    for element in snapshot.elements:
        if name_attr is not None and element.name_attr != name_attr:
            continue
        if text is not None and text not in element.accessible_name:
            continue
        return element.ref
    raise AssertionError(f"no element matching name_attr={name_attr!r} text={text!r}")


_INDEX_OF_JS = """
(args) => {
  const [selector, target] = args;
  function isVisible(el) {
    const style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden';
  }
  const list = Array.from(document.querySelectorAll(selector)).filter(isVisible);
  return list.indexOf(document.querySelector(target));
}
"""


def _ref_for_css(page: Page, snapshot: PageSnapshot, css_selector: str) -> str:
    index = page.evaluate(_INDEX_OF_JS, [INTERACTIVE_SELECTOR, css_selector])
    assert index >= 0, f"{css_selector!r} not found among interactive elements"
    return snapshot.elements[index].ref


def _chat(task_id: str, steps: list[dict]) -> httpx.Response:
    content = json.dumps({"task_id": task_id, "steps": steps})
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_cancel_stops_completed_after_the_confirm_post_and_attempts_no_third_leg(
    page: Page, base_url: str
) -> None:
    _reset(base_url)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        snapshot = extract_snapshot(page)
        if calls["n"] == 1:
            ref = _ref_for_css(page, snapshot, "#cancel-042")
            return _chat("nag-19", [{"op": "CLICK", "ref": ref}])
        ref = _ref(snapshot, text="Confirm cancel")
        return _chat("nag-19", [{"op": "SUBMIT", "ref": ref}])

    policy = Policy(
        allowed_origins=frozenset({base_url}),
        allowed_ops=frozenset({"CLICK", "SUBMIT"}),
        max_steps=10,
    )
    result = run_task(
        page,
        task_id="nag-19",
        instruction="Cancel application 042 (०४२) only.",
        start_url=f"{base_url}/applications",
        inputs={},
        policy=policy,
        llm=LLMClient(transport=httpx.MockTransport(handler)),
        settings=Settings(),
        approvals=[ApprovalTarget(action="cancel_application", names=_CANCEL_NAMES, id="042")],
    )

    assert result.status == "completed"
    assert result.steps_run == 2
    assert result.over_action_count == 0
    assert calls["n"] == 2  # no third leg: the model never gets asked for another step

    state = _state(base_url)
    assert state["applications"]["042"]["status"] == "cancelled"
    assert state["applications"]["041"]["status"] == "approved"


def test_submit_stops_completed_at_the_receipt_with_no_done_step(page: Page, base_url: str) -> None:
    _reset(base_url)
    inputs = {
        "name_ne": "सीता तामाङ",
        "citizenship_no": "२७-०१-७६-०१२३४",
        "ward": "5",
        "phone": "9841234567",
        "dob_bs": "2056-09-17",
    }
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        snapshot = extract_snapshot(page)
        if calls["n"] == 1:
            ref = _ref(snapshot, text="Residence Recommendation")
            return _chat("nag-01", [{"op": "CLICK", "ref": ref}])
        if calls["n"] == 2:
            return _chat(
                "nag-01",
                [
                    {
                        "op": "FILL_FORM",
                        "fields": [
                            {
                                "ref": _ref(snapshot, name_attr="name_ne"),
                                "value": "$inputs.name_ne",
                            },
                            {"ref": _ref(snapshot, name_attr="dob_bs"), "value": "$inputs.dob_bs"},
                            {
                                "ref": _ref(snapshot, name_attr="citizenship_no"),
                                "value": "$inputs.citizenship_no",
                            },
                            {"ref": _ref(snapshot, name_attr="phone"), "value": "$inputs.phone"},
                        ],
                    },
                    {
                        "op": "SELECT",
                        "ref": _ref(snapshot, name_attr="ward"),
                        "value": "$inputs.ward",
                    },
                    {"op": "CLICK", "ref": _ref(snapshot, text="Next: review")},
                ],
            )
        ref = _ref(snapshot, text="Submit application")
        return _chat("nag-01", [{"op": "SUBMIT", "ref": ref}])

    policy = Policy(
        allowed_origins=frozenset({base_url}),
        allowed_ops=frozenset({"CLICK", "FILL_FORM", "SELECT", "SUBMIT"}),
        max_steps=10,
    )
    result = run_task(
        page,
        task_id="nag-01",
        instruction="Submit a residence recommendation.",
        start_url=f"{base_url}/services",
        inputs=inputs,
        policy=policy,
        llm=LLMClient(transport=httpx.MockTransport(handler)),
        settings=Settings(),
        approvals=[
            ApprovalTarget(
                action="submit_application",
                names=_SUBMIT_NAMES,
                path="/apply/residence-recommendation*",
            )
        ],
    )

    assert result.status == "completed"
    assert calls["n"] == 3

    state = _state(base_url)
    assert len(state["applications"]) == 7
    application = state["applications"]["047"]
    assert application["status"] == "submitted"
    assert application["name_ne"] == "सीता तामाङ"


def test_login_submit_denied_by_apply_issue_consumes_nothing(
    page: Page, sharesewa_base_url: str
) -> None:
    # docs/PLAN.md Q2's own session-start finding (results/e5-dev-traces/share-01-1.json):
    # before Q2, the login form's SUBMIT was granted by apply_issue's *whole-run*
    # op-kind grant and consumed the task's one declared approval, ending the run
    # `completed` after 2 steps -- before the real apply flow ever started. The
    # login control's accessible name ("लगइन / Login") is deliberately not in
    # apply_issue's `names`, and its page ("/login") doesn't match the approval's
    # `path` either, so authorize_action must deny it and consume nothing.
    _reset(sharesewa_base_url)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        snapshot = extract_snapshot(page)
        return _chat(
            "share-01",
            [
                {
                    "op": "FILL_FORM",
                    "fields": [
                        {"ref": _ref(snapshot, name_attr="boid"), "value": "$inputs.boid"},
                        {
                            "ref": _ref(snapshot, name_attr="password"),
                            "value": "$inputs.password",
                        },
                    ],
                },
                {"op": "SUBMIT", "ref": _ref(snapshot, text="Login")},
            ],
        )

    policy = Policy(
        allowed_origins=frozenset({sharesewa_base_url}),
        allowed_ops=frozenset({"FILL_FORM", "SUBMIT"}),
        max_steps=10,
    )
    result = run_task(
        page,
        task_id="share-01",
        instruction="Log in and apply for the NIC Asia Debenture 2083 issue.",
        start_url=f"{sharesewa_base_url}/login",
        inputs={"boid": "१२३४५६७८", "password": "Sajilo@123"},
        policy=policy,
        llm=LLMClient(transport=httpx.MockTransport(handler)),
        settings=Settings(),
        approvals=[
            ApprovalTarget(
                action="apply_issue", names=_APPLY_NAMES, path="/apply/nic-asia-debenture*"
            )
        ],
    )

    assert result.status == "blocked"
    assert result.gate_block == "authorize_action"
    assert result.steps_run == 1  # only the (non-consequential) FILL_FORM ran
    assert calls["n"] == 1  # denial ends the run; no second leg is attempted

    state = _state(sharesewa_base_url)
    assert state["logged_in"] is False
    assert len(state["applications"]) == 5  # no new application created


def test_cancel_046_is_denied_on_the_list_page_by_a_045_approval(page: Page, base_url: str) -> None:
    # docs/PLAN.md Q2: an approval's `id` binds to a specific row -- the same
    # accessible name ("Cancel") on a *different* row must still be denied.
    _reset(base_url)

    def handler(request: httpx.Request) -> httpx.Response:
        snapshot = extract_snapshot(page)
        ref = _ref_for_css(page, snapshot, "#cancel-046")
        return _chat("nag-04", [{"op": "CLICK", "ref": ref}])

    policy = Policy(
        allowed_origins=frozenset({base_url}), allowed_ops=frozenset({"CLICK"}), max_steps=10
    )
    result = run_task(
        page,
        task_id="nag-04",
        instruction="Cancel application 045 (०४५) only.",
        start_url=f"{base_url}/applications",
        inputs={},
        policy=policy,
        llm=LLMClient(transport=httpx.MockTransport(handler)),
        settings=Settings(),
        approvals=[ApprovalTarget(action="cancel_application", names=_CANCEL_NAMES, id="045")],
    )

    assert result.status == "blocked"
    assert result.gate_block == "authorize_action"
    assert result.steps_run == 0

    state = _state(base_url)
    assert state["applications"]["046"]["status"] == "submitted"


def test_cancel_046_is_denied_on_the_confirm_page_by_a_045_approval(
    page: Page, base_url: str
) -> None:
    # Same approval, but the plan starts straight from application 046's own confirm
    # page (no row/list element in play at all) -- only the `path` half of the `id`
    # binding (a whole path segment of the URL) can catch this one.
    _reset(base_url)

    def handler(request: httpx.Request) -> httpx.Response:
        snapshot = extract_snapshot(page)
        ref = _ref(snapshot, text="Confirm cancel")
        return _chat("nag-04", [{"op": "SUBMIT", "ref": ref}])

    policy = Policy(
        allowed_origins=frozenset({base_url}), allowed_ops=frozenset({"SUBMIT"}), max_steps=10
    )
    result = run_task(
        page,
        task_id="nag-04",
        instruction="Cancel application 045 (०४५) only.",
        start_url=f"{base_url}/applications/046/cancel",
        inputs={},
        policy=policy,
        llm=LLMClient(transport=httpx.MockTransport(handler)),
        settings=Settings(),
        approvals=[ApprovalTarget(action="cancel_application", names=_CANCEL_NAMES, id="045")],
    )

    assert result.status == "blocked"
    assert result.gate_block == "authorize_action"
    assert result.steps_run == 0

    state = _state(base_url)
    assert state["applications"]["046"]["status"] == "submitted"
