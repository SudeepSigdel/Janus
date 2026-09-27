"""Executor + egress + escalation + verifier against the live nagarpalika replica.

No model is involved (M4 scope): each task's plan below is hand-written per page,
mirroring the oracle agent's routines (agents/oracle.py) but going through the same
gates a real planner-driven run passes through from M5 onward: validate_plan ->
authorize_action (with escalation.make_granted_ops standing in for a human/simulated
user) -> execute_step -> verify_step.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from datetime import date

import httpx
import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from janus.executor.egress import install_egress_guard
from janus.executor.escalation import make_granted_ops
from janus.executor.executor import StepOutcome, execute_step
from janus.observer.extract import INTERACTIVE_SELECTOR, extract_snapshot
from janus.observer.snapshot import PageSnapshot
from janus.planner.ops import (
    ClickStep,
    FieldValue,
    FillFormStep,
    OpKind,
    Plan,
    SelectStep,
    SubmitStep,
)
from janus.text.nepali import ad_to_bs
from janus.validator.action import authorize_action
from janus.validator.plan import validate_plan
from janus.validator.policy import Policy
from janus.verifier.verify import StepCheck, verify_step
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


def _policy(base_url: str, allowed_ops: frozenset[OpKind]) -> Policy:
    return Policy(allowed_origins=frozenset({base_url}), allowed_ops=allowed_ops, max_steps=10)


def _ref(snapshot: PageSnapshot, *, name_attr: str | None = None, text: str | None = None) -> str:
    """Find a ref by accessible_name/name_attr. Only safe when the match is unique on
    the current page -- rows with identical labels (e.g. every "Edit" link on the
    applications list) need `_ref_for_css` instead."""
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
    """White-box lookup by the replica's real DOM id, for elements that are otherwise
    indistinguishable by accessible_name alone (every row's "Edit"/"Cancel" link has
    the same text). A hand-written test plan is allowed this; production grounding
    (M5) never sees DOM ids -- it works from labels and structure only."""
    index = page.evaluate(_INDEX_OF_JS, [INTERACTIVE_SELECTOR, css_selector])
    assert index >= 0, f"{css_selector!r} not found among interactive elements"
    return snapshot.elements[index].ref


def _run_leg(
    page: Page,
    snapshot: PageSnapshot,
    plan: Plan,
    policy: Policy,
    inputs: dict[str, str],
    granted_ops: frozenset[OpKind],
) -> tuple[list[StepOutcome], list[StepCheck]]:
    """validate -> authorize -> execute -> verify, one page's worth of steps at a time
    (a Plan's refs only make sense against the single snapshot it was built from)."""
    result = validate_plan(plan, policy, snapshot, inputs)
    assert result.ok, result.errors

    outcomes: list[StepOutcome] = []
    checks: list[StepCheck] = []
    for step, decision in zip(plan.steps, result.decisions, strict=True):
        authorization = authorize_action(step.op, decision.consequential, granted_ops)
        assert authorization.allowed, authorization.reason
        outcome = execute_step(page, step, snapshot, inputs)
        outcomes.append(outcome)
        if outcome.blocked or not outcome.ok:
            return outcomes, checks
        checks.append(verify_step(page, step, snapshot, inputs))
    return outcomes, checks


def test_hand_written_plan_submits_residence_recommendation(page: Page, base_url: str) -> None:
    _reset(base_url)
    page.goto(f"{base_url}/services")
    install_egress_guard(page, frozenset({base_url}))
    inputs = {
        "name_ne": "सीता तामाङ",
        "citizenship_no": "२७-०१-७६-०१२३४",
        "ward": "5",
        "phone": "9841234567",
        "dob_bs": "2056-09-17",
    }
    policy = _policy(base_url, frozenset({"CLICK", "FILL_FORM", "SELECT", "SUBMIT"}))
    granted_ops = make_granted_ops(["submit_application"])

    snapshot = extract_snapshot(page)
    link_ref = _ref(snapshot, text="Residence Recommendation")
    outcomes, _ = _run_leg(
        page,
        snapshot,
        Plan(task_id="nag-01", steps=[ClickStep(ref=link_ref)]),
        policy,
        inputs,
        granted_ops,
    )
    assert all(o.ok for o in outcomes)

    snapshot = extract_snapshot(page)
    plan = Plan(
        task_id="nag-01",
        steps=[
            FillFormStep(
                fields=[
                    FieldValue(ref=_ref(snapshot, name_attr="name_ne"), value="$inputs.name_ne"),
                    FieldValue(ref=_ref(snapshot, name_attr="dob_bs"), value="$inputs.dob_bs"),
                    FieldValue(
                        ref=_ref(snapshot, name_attr="citizenship_no"),
                        value="$inputs.citizenship_no",
                    ),
                    FieldValue(ref=_ref(snapshot, name_attr="phone"), value="$inputs.phone"),
                ]
            ),
            SelectStep(ref=_ref(snapshot, name_attr="ward"), value="$inputs.ward"),
            ClickStep(ref=_ref(snapshot, text="Next: review")),
        ],
    )
    outcomes, checks = _run_leg(page, snapshot, plan, policy, inputs, granted_ops)
    assert all(o.ok for o in outcomes)
    assert all(c.ok for c in checks), checks

    snapshot = extract_snapshot(page)
    submit_ref = _ref(snapshot, text="Submit application")
    outcomes, _ = _run_leg(
        page,
        snapshot,
        Plan(task_id="nag-01", steps=[SubmitStep(ref=submit_ref)]),
        policy,
        inputs,
        granted_ops,
    )
    assert all(o.ok for o in outcomes)

    page.wait_for_selector("#receipt-id")
    state = _state(base_url)
    assert len(state["applications"]) == 7
    application = state["applications"]["047"]
    assert application["status"] == "submitted"
    assert application["name_ne"] == "सीता तामाङ"
    assert application["dob_bs"] == "2056-09-17"
    assert application["ward"] == 5
    assert application["phone"] == "9841234567"


def test_hand_written_plan_converts_ad_date_before_submitting(page: Page, base_url: str) -> None:
    _reset(base_url)
    page.goto(f"{base_url}/services")
    install_egress_guard(page, frozenset({base_url}))
    year, month, day = ad_to_bs(date(2000, 1, 1))
    inputs = {
        "name_ne": "सीता तामाङ",
        "citizenship_no": "२७-०१-७६-०१२३४",
        "ward": "5",
        "phone": "9841234567",
        "dob_bs": f"{year:04d}-{month:02d}-{day:02d}",
    }
    policy = _policy(base_url, frozenset({"CLICK", "FILL_FORM", "SELECT", "SUBMIT"}))
    granted_ops = make_granted_ops(["submit_application"])

    snapshot = extract_snapshot(page)
    link_ref = _ref(snapshot, text="Residence Recommendation")
    _run_leg(
        page,
        snapshot,
        Plan(task_id="nag-02", steps=[ClickStep(ref=link_ref)]),
        policy,
        inputs,
        granted_ops,
    )

    snapshot = extract_snapshot(page)
    plan = Plan(
        task_id="nag-02",
        steps=[
            FillFormStep(
                fields=[
                    FieldValue(ref=_ref(snapshot, name_attr="name_ne"), value="$inputs.name_ne"),
                    FieldValue(ref=_ref(snapshot, name_attr="dob_bs"), value="$inputs.dob_bs"),
                    FieldValue(
                        ref=_ref(snapshot, name_attr="citizenship_no"),
                        value="$inputs.citizenship_no",
                    ),
                    FieldValue(ref=_ref(snapshot, name_attr="phone"), value="$inputs.phone"),
                ]
            ),
            SelectStep(ref=_ref(snapshot, name_attr="ward"), value="$inputs.ward"),
            ClickStep(ref=_ref(snapshot, text="Next: review")),
        ],
    )
    outcomes, checks = _run_leg(page, snapshot, plan, policy, inputs, granted_ops)
    assert all(o.ok for o in outcomes)
    assert all(c.ok for c in checks), checks

    snapshot = extract_snapshot(page)
    submit_ref = _ref(snapshot, text="Submit application")
    _run_leg(
        page,
        snapshot,
        Plan(task_id="nag-02", steps=[SubmitStep(ref=submit_ref)]),
        policy,
        inputs,
        granted_ops,
    )

    page.wait_for_selector("#receipt-id")
    state = _state(base_url)
    assert state["applications"]["047"]["dob_bs"] == "2056-09-17"


def test_hand_written_plan_updates_phone_number(page: Page, base_url: str) -> None:
    _reset(base_url)
    page.goto(f"{base_url}/applications")
    install_egress_guard(page, frozenset({base_url}))
    inputs = {"phone": "9851098765"}
    policy = _policy(base_url, frozenset({"CLICK", "FILL_FORM"}))
    granted_ops = make_granted_ops([])  # approvals: [] -- this is deliberately non-consequential

    snapshot = extract_snapshot(page)
    edit_ref = _ref_for_css(page, snapshot, "#edit-043")
    outcomes, _ = _run_leg(
        page,
        snapshot,
        Plan(task_id="nag-03", steps=[ClickStep(ref=edit_ref)]),
        policy,
        inputs,
        granted_ops,
    )
    assert all(o.ok for o in outcomes)

    snapshot = extract_snapshot(page)
    plan = Plan(
        task_id="nag-03",
        steps=[
            FillFormStep(
                fields=[FieldValue(ref=_ref(snapshot, name_attr="phone"), value="$inputs.phone")]
            ),
            ClickStep(ref=_ref(snapshot, text="Save")),
        ],
    )
    outcomes, checks = _run_leg(page, snapshot, plan, policy, inputs, granted_ops)
    assert all(o.ok for o in outcomes)
    assert all(c.ok for c in checks), checks

    page.wait_for_url("**/applications")
    state = _state(base_url)
    assert state["applications"]["043"]["phone"] == "9851098765"
    assert state["applications"]["043"]["status"] == "submitted"
    assert state["applications"]["041"]["status"] == "approved"


def test_hand_written_plan_cancels_a_specific_application(page: Page, base_url: str) -> None:
    _reset(base_url)
    page.goto(f"{base_url}/applications")
    install_egress_guard(page, frozenset({base_url}))
    inputs: dict[str, str] = {}
    policy = _policy(base_url, frozenset({"CLICK", "SUBMIT"}))
    granted_ops = make_granted_ops(["cancel_application:045"])

    snapshot = extract_snapshot(page)
    cancel_ref = _ref_for_css(page, snapshot, "#cancel-045")
    outcomes, _ = _run_leg(
        page,
        snapshot,
        Plan(task_id="nag-04", steps=[ClickStep(ref=cancel_ref)]),
        policy,
        inputs,
        granted_ops,
    )
    assert all(o.ok for o in outcomes)

    snapshot = extract_snapshot(page)
    confirm_ref = _ref(snapshot, text="Confirm cancel")
    outcomes, _ = _run_leg(
        page,
        snapshot,
        Plan(task_id="nag-04", steps=[SubmitStep(ref=confirm_ref)]),
        policy,
        inputs,
        granted_ops,
    )
    assert all(o.ok for o in outcomes)

    page.wait_for_url("**/applications")
    state = _state(base_url)
    assert state["applications"]["045"]["status"] == "cancelled"
    assert state["applications"]["046"]["status"] == "submitted"
    assert len(state["applications"]) == 6


def test_dom_mutation_between_observe_and_act_blocks_the_step(page: Page, base_url: str) -> None:
    _reset(base_url)
    page.goto(f"{base_url}/apply/residence-recommendation")
    snapshot = extract_snapshot(page)
    phone_ref = _ref(snapshot, name_attr="phone")

    # The page changes after we observed it but before we act: the phone field is
    # gone. Act-time re-resolution must notice and block, not silently act on
    # whatever now occupies that position (security invariant 5).
    page.evaluate("document.getElementById('phone').remove()")

    outcome = execute_step(
        page,
        FillFormStep(fields=[FieldValue(ref=phone_ref, value="9841234567")]),
        snapshot,
        {},
    )
    assert outcome.blocked is True
    assert outcome.ok is False


class _CountingHandler(http.server.BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self) -> None:
        type(self).hits += 1
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # quiet: no stderr spam
        pass


@pytest.fixture()
def attacker_origin() -> Iterator[str]:
    _CountingHandler.hits = 0
    server = http.server.HTTPServer(("127.0.0.1", 8199), _CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:8199"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _fetch_and_ignore_errors(page: Page, url: str) -> None:
    page.evaluate("(url) => fetch(url).catch(() => undefined)", url)
    page.wait_for_timeout(200)


def test_egress_guard_blocks_requests_to_a_non_allowlisted_origin(
    page: Page, base_url: str, attacker_origin: str
) -> None:
    _reset(base_url)
    page.goto(f"{base_url}/services")
    install_egress_guard(page, frozenset({base_url}))

    _fetch_and_ignore_errors(page, f"{attacker_origin}/exfiltrate")

    assert _CountingHandler.hits == 0


def test_without_the_guard_the_attacker_would_receive_the_request(
    page: Page, base_url: str, attacker_origin: str
) -> None:
    """Positive control: proves the fixture above is a real block, not a fetch that
    would have failed anyway (e.g. from CORS) with no guard installed at all."""
    _reset(base_url)
    page.goto(f"{base_url}/services")

    _fetch_and_ignore_errors(page, f"{attacker_origin}/exfiltrate")

    assert _CountingHandler.hits == 1
