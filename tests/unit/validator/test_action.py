"""authorize_action: consequential actions are denied unless already granted (a live
`granted_ops` op-kind grant) or matched against a declared, per-target approval
(docs/PLAN.md Q2)."""

from __future__ import annotations

from janus.policy import ApprovalTarget
from janus.validator.action import authorize_action

_CANCEL_045 = ApprovalTarget(
    action="cancel_application", names=["Cancel", "Confirm cancel"], id="045"
)


def test_non_consequential_action_is_always_allowed() -> None:
    result = authorize_action("CLICK", False, None, None, "http://x/", [])
    assert result.allowed is True
    assert result.matched is None


def test_unapproved_submit_is_denied() -> None:
    result = authorize_action("SUBMIT", True, None, None, "http://x/", [])
    assert result.allowed is False
    assert result.consequential is True
    assert result.matched is None


def test_submit_granted_in_advance_is_allowed() -> None:
    result = authorize_action(
        "SUBMIT", True, None, None, "http://x/", [], granted_ops=frozenset({"SUBMIT"})
    )
    assert result.allowed is True
    assert result.matched is None  # the live channel, not a declared approval


def test_grant_for_a_different_op_does_not_authorize_submit() -> None:
    result = authorize_action(
        "SUBMIT", True, None, None, "http://x/", [], granted_ops=frozenset({"CLICK"})
    )
    assert result.allowed is False


def test_matching_approval_is_allowed_and_returned() -> None:
    result = authorize_action(
        "CLICK", True, "Cancel", "045", "http://x/applications", [_CANCEL_045]
    )
    assert result.allowed is True
    assert result.matched is _CANCEL_045


def test_wrong_target_id_is_denied() -> None:
    result = authorize_action(
        "CLICK", True, "Cancel", "046", "http://x/applications", [_CANCEL_045]
    )
    assert result.allowed is False
    assert result.matched is None


def test_wrong_name_is_denied_even_with_a_matching_id() -> None:
    result = authorize_action("CLICK", True, "Edit", "045", "http://x/applications", [_CANCEL_045])
    assert result.allowed is False


def test_an_exhausted_approval_list_denies() -> None:
    result = authorize_action("CLICK", True, "Cancel", "045", "http://x/applications", [])
    assert result.allowed is False
