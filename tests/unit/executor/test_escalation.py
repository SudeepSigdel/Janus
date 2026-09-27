"""make_granted_ops/parse_approval/cli_escalation: turning declared approvals into
granted op kinds, deterministically."""

from __future__ import annotations

import pytest

from janus.executor.escalation import cli_escalation, make_granted_ops, parse_approval
from janus.planner.ops import SubmitStep


def test_parse_approval_splits_target_suffix() -> None:
    assert parse_approval("cancel_application:045") == ("cancel_application", "045")


def test_parse_approval_without_target() -> None:
    assert parse_approval("submit_application") == ("submit_application", None)


def test_submit_application_grants_submit_and_click() -> None:
    # Both, not SUBMIT alone: the planner isn't forced to call the submit button's op
    # SUBMIT rather than CLICK, and either op trips the same "submit"/"पेश" keyword
    # (validator/consequential.py) -- see escalation.py's module docstring (M6).
    assert make_granted_ops(["submit_application"]) == frozenset({"SUBMIT", "CLICK"})


def test_cancel_application_grants_click_and_submit() -> None:
    assert make_granted_ops(["cancel_application:045"]) == frozenset({"CLICK", "SUBMIT"})


def test_apply_issue_grants_submit_and_click() -> None:
    assert make_granted_ops(["apply_issue"]) == frozenset({"SUBMIT", "CLICK"})


def test_withdraw_application_grants_click_and_submit() -> None:
    assert make_granted_ops(["withdraw_application:043"]) == frozenset({"CLICK", "SUBMIT"})


def test_no_approvals_grants_nothing() -> None:
    assert make_granted_ops([]) == frozenset()


def test_unknown_approval_label_grants_nothing() -> None:
    assert make_granted_ops(["some_future_label"]) == frozenset()


def test_approvals_union_across_multiple_labels() -> None:
    granted = make_granted_ops(["submit_application", "cancel_application:045"])
    assert granted == frozenset({"SUBMIT", "CLICK"})


def test_cli_escalation_accepts_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    step = SubmitStep(ref="e3")
    assert cli_escalation(step, "task declared submit_application") is True


def test_cli_escalation_rejects_anything_else(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    step = SubmitStep(ref="e3")
    assert cli_escalation(step, "not approved") is False
