"""classify_run: a claimed DONE status never overrides what execution actually did."""

from __future__ import annotations

from janus.executor.executor import StepOutcome
from janus.verifier.verify import StepCheck, classify_run


def test_all_ok_keeps_the_claimed_completed_status() -> None:
    outcomes = [StepOutcome(ok=True), StepOutcome(ok=True)]
    checks = [StepCheck(ok=True)]
    assert classify_run(outcomes, checks, "completed") == "completed"


def test_blocked_outcome_overrides_a_claimed_completed_status() -> None:
    outcomes = [StepOutcome(ok=False, blocked=True, reason="fingerprint mismatch")]
    assert classify_run(outcomes, [], "completed") == "blocked"


def test_failed_outcome_downgrades_to_partial() -> None:
    outcomes = [StepOutcome(ok=True), StepOutcome(ok=False, reason="unknown ref")]
    assert classify_run(outcomes, [], "completed") == "partial"


def test_failed_postcondition_check_downgrades_to_partial() -> None:
    outcomes = [StepOutcome(ok=True)]
    checks = [StepCheck(ok=False, reason="field did not take its value")]
    assert classify_run(outcomes, checks, "completed") == "partial"


def test_blocked_outcome_wins_over_failed_check() -> None:
    outcomes = [StepOutcome(ok=False, blocked=True, reason="mismatch")]
    checks = [StepCheck(ok=False, reason="also failed")]
    assert classify_run(outcomes, checks, "completed") == "blocked"
