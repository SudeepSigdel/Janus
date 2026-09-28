"""End-to-end: agent.run_task's deterministic stop (docs/PLAN.md P4) against the real
nagarpalika replica, driven by a scripted fake LLM (httpx.MockTransport) rather than a
real model -- this tests the orchestrator loop's own approval-consumption logic, not
planning, so it stays `browser`-only (no `ollama` marker, no live Ollama needed). Each
scripted response is computed from the page's *actual* current snapshot at call time
(same helpers as test_executor_browser.py), so it stays correct regardless of exactly
how the observer numbers refs.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from janus.agent import run_task
from janus.config import Settings
from janus.executor.escalation import make_granted_ops
from janus.llm import LLMClient
from janus.observer.extract import INTERACTIVE_SELECTOR, extract_snapshot
from janus.observer.snapshot import PageSnapshot
from janus.validator.policy import Policy
from janus_bench.harness.server import running_site

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    with running_site("nagarpalika") as url:
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
        granted_ops=make_granted_ops(["cancel_application:042"]),
        approval_count=1,
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
        granted_ops=make_granted_ops(["submit_application"]),
        approval_count=1,
    )

    assert result.status == "completed"
    assert calls["n"] == 3

    state = _state(base_url)
    assert len(state["applications"]) == 7
    application = state["applications"]["047"]
    assert application["status"] == "submitted"
    assert application["name_ne"] == "सीता तामाङ"
