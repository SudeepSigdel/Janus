"""authorize_action: consequential actions are denied unless already granted."""

from __future__ import annotations

from janus.validator.action import authorize_action


def test_non_consequential_action_is_always_allowed() -> None:
    result = authorize_action("CLICK", consequential=False, granted_ops=frozenset())
    assert result.allowed is True


def test_unapproved_submit_is_denied() -> None:
    result = authorize_action("SUBMIT", consequential=True, granted_ops=frozenset())
    assert result.allowed is False
    assert result.consequential is True


def test_submit_granted_in_advance_is_allowed() -> None:
    result = authorize_action("SUBMIT", consequential=True, granted_ops=frozenset({"SUBMIT"}))
    assert result.allowed is True


def test_grant_for_a_different_op_does_not_authorize_submit() -> None:
    result = authorize_action("SUBMIT", consequential=True, granted_ops=frozenset({"CLICK"}))
    assert result.allowed is False
