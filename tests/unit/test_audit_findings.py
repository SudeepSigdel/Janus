"""Failing tests that demonstrate docs/AUDIT.md findings (deterministic, no browser/model).

Each test asserts the *intended* property and is `xfail(strict=True)`: once the finding is
fixed it XPASSes, which fails the suite until the marker is removed.
"""

from __future__ import annotations

import json

import pytest

from janus.observer.snapshot import Element, Fingerprint, PageSnapshot
from janus.planner.ground import _exact_match
from janus.planner.ops import ClickStep, NavigateStep, Plan, SubmitStep
from janus.planner.plan import _user_message
from janus.policy import ApprovalTarget, approval_matches
from janus.validator.action import authorize_action
from janus.validator.plan import validate_plan
from janus.validator.policy import Policy, allowed_ops_for

ORIGIN = "http://127.0.0.1:8102"
KNOWN_ROLES = {"link", "button", "textbox", "combobox", "checkbox", "radio", "generic"}


def _el(ref: str, role: str, name: str, *, row_key: str | None = None) -> Element:
    fp = Fingerprint(
        role=role, accessible_name=name, name_attr=None, form_id=None, tag="button", row_key=row_key
    )
    return Element(
        ref=ref,
        tag="button",
        role=role,
        accessible_name=name,
        name_attr=None,
        form_id=None,
        fingerprint=fp,
        row_key=row_key,
    )


def _snap(elements: list[Element], *, title: str = "t", url: str = f"{ORIGIN}/x") -> PageSnapshot:
    return PageSnapshot(url=url, title=title, elements=elements, untrusted_text=[])


def _policy(**kw) -> Policy:
    return Policy(
        allowed_origins=frozenset({ORIGIN}),
        allowed_ops=kw.get("ops", allowed_ops_for(False)),
        max_steps=20,
    )


@pytest.mark.xfail(strict=True, reason="AUDIT.md S1: page <title> reaches the planner uncapped")
def test_s1_page_title_is_capped_in_planner_prompt() -> None:
    title = "x" * 2000
    msg = json.loads(_user_message("t", "i", [], _snap([], title=title), []))
    assert len(msg["page_title"]) <= 80


@pytest.mark.xfail(
    strict=True, reason="AUDIT.md S1: page-authored role attribute reaches the planner verbatim"
)
def test_s1_outline_role_is_from_a_closed_vocabulary() -> None:
    snap = _snap([_el("e0", "free text of any length chosen by the page " * 10, "")])
    msg = json.loads(_user_message("t", "i", [], snap, []))
    assert all(e["role"] in KNOWN_ROLES for e in msg["elements"])


@pytest.mark.xfail(
    strict=True, reason="AUDIT.md S3: op choice (CLICK vs SUBMIT) downgrades consequential"
)
def test_s3_same_target_is_equally_consequential_under_click_and_submit() -> None:
    save = _el("e0", "button", "Save")  # a form-submit button with no keyword in its label
    snap = _snap([save])
    policy = _policy()
    as_submit = validate_plan(Plan(task_id="t", steps=[SubmitStep(ref="e0")]), policy, snap, {})
    as_click = validate_plan(Plan(task_id="t", steps=[ClickStep(ref="e0")]), policy, snap, {})
    assert as_submit.decisions[0].consequential == as_click.decisions[0].consequential


@pytest.mark.xfail(
    strict=True, reason="AUDIT.md S4: approval path glob matches a lookalike issue slug"
)
def test_s4_path_approval_does_not_cover_a_lookalike_issue() -> None:
    approval = ApprovalTarget(
        action="apply_issue", names=["Submit application"], path="/apply/nic-asia-debenture*"
    )
    assert not approval_matches(
        approval,
        "SUBMIT",
        "Submit application",
        None,
        f"{ORIGIN}/apply/nic-asia-debenture-series-b/review",
    )


@pytest.mark.xfail(
    strict=True, reason="AUDIT.md S6: grounding's exact tier prefers a page-authored label copy"
)
def test_s6_exact_tier_does_not_prefer_a_short_copy_over_the_real_control() -> None:
    cands = {"e2": "आवेदन पेश गर्नुहोस् / Submit application", "e9": "Submit application"}
    # Two candidates both contain the desired label; an unambiguous pick needs more than
    # equality with the shorter, page-authored copy.
    assert _exact_match("Submit application", cands) is None


def test_s7_bench_endpoints_are_denied_by_policy() -> None:
    plan = Plan(task_id="t", steps=[NavigateStep(url=f"{ORIGIN}/__bench/state")])
    result = validate_plan(plan, _policy(ops=allowed_ops_for(True)), _snap([]), {})
    assert not result.ok


@pytest.mark.xfail(strict=True, reason="AUDIT.md S8: a row_key mismatch falls back to the URL path")
def test_s8_row_key_mismatch_is_a_hard_deny() -> None:
    approval = ApprovalTarget(action="cancel_application", names=["Cancel"], id="045")
    assert not approval_matches(
        approval, "CLICK", "Cancel", "046", f"{ORIGIN}/applications/045/view"
    )


@pytest.mark.xfail(
    strict=True, reason="AUDIT.md S11: a live 'yes' grants the op kind for the whole run"
)
def test_s11_live_grant_does_not_authorize_a_different_target() -> None:
    # agent.py adds step.op to `granted` after one interactive yes; a later, different
    # consequential target with the same op kind is then authorized without asking.
    result = authorize_action(
        "CLICK", True, "Delete account", None, f"{ORIGIN}/x", [], granted_ops=frozenset({"CLICK"})
    )
    assert not result.allowed
