"""cli_escalation: a human approves or denies one consequential action at a prompt
(docs/PLAN.md Q2 replaced the string-label parsing this file used to test --
`make_granted_ops`/`parse_approval` -- with `janus.policy.ApprovalTarget`, tested in
tests/unit/test_policy.py)."""

from __future__ import annotations

import pytest

from janus.executor.escalation import cli_escalation
from janus.planner.ops import SubmitStep


def test_cli_escalation_accepts_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    step = SubmitStep(ref="e3")
    assert cli_escalation(step, "task declared submit_application") is True


def test_cli_escalation_rejects_anything_else(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    step = SubmitStep(ref="e3")
    assert cli_escalation(step, "not approved") is False
