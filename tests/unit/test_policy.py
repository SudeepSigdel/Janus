"""ApprovalTarget/approval_matches: per-target, per-use approval matching
(docs/PLAN.md Q2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from janus.policy import ApprovalTarget, approval_matches, op_kinds_for


def test_op_kinds_for_known_action() -> None:
    assert op_kinds_for("cancel_application") == frozenset({"CLICK", "SUBMIT"})


def test_op_kinds_for_unknown_action_grants_nothing() -> None:
    assert op_kinds_for("some_future_label") == frozenset()


def test_approval_requires_non_empty_names() -> None:
    with pytest.raises(ValidationError):
        ApprovalTarget(action="cancel_application", names=[], id="045")


def test_approval_rejects_both_id_and_path() -> None:
    with pytest.raises(ValidationError):
        ApprovalTarget(action="cancel_application", names=["Cancel"], id="045", path="/x/*")


def test_approval_rejects_neither_id_nor_path() -> None:
    with pytest.raises(ValidationError):
        ApprovalTarget(action="cancel_application", names=["Cancel"])


def test_approval_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ApprovalTarget(action="cancel_application", names=["Cancel"], id="045", surprise=1)


_CANCEL_045 = ApprovalTarget(
    action="cancel_application",
    names=["रद्द / Cancel", "रद्द गर्नुहोस् / Confirm cancel"],
    id="045",
)


def test_id_binding_matches_row_key_on_the_list_page() -> None:
    assert approval_matches(_CANCEL_045, "CLICK", "रद्द / Cancel", "045", "http://x/applications")


def test_id_binding_matches_a_whole_path_segment_on_the_confirm_page() -> None:
    assert approval_matches(
        _CANCEL_045,
        "SUBMIT",
        "रद्द गर्नुहोस् / Confirm cancel",
        None,
        "http://x/applications/045/cancel",
    )


def test_id_binding_denies_a_different_row_key() -> None:
    assert not approval_matches(
        _CANCEL_045, "CLICK", "रद्द / Cancel", "046", "http://x/applications"
    )


def test_id_binding_denies_a_different_path_segment() -> None:
    assert not approval_matches(
        _CANCEL_045,
        "SUBMIT",
        "रद्द गर्नुहोस् / Confirm cancel",
        None,
        "http://x/applications/046/cancel",
    )


def test_id_binding_does_not_substring_match() -> None:
    # "045" must not match inside "1045" or "0450" -- a whole path segment only.
    assert not approval_matches(
        _CANCEL_045,
        "SUBMIT",
        "रद्द गर्नुहोस् / Confirm cancel",
        None,
        "http://x/applications/1045/cancel",
    )


def test_name_mismatch_denies_even_with_a_matching_id() -> None:
    assert not approval_matches(
        _CANCEL_045, "CLICK", "सम्पादन / Edit", "045", "http://x/applications"
    )


def test_op_kind_mismatch_denies_even_with_a_matching_name_and_id() -> None:
    # cancel_application never grants FILL_FORM.
    assert not approval_matches(
        _CANCEL_045, "FILL_FORM", "रद्द / Cancel", "045", "http://x/applications"
    )


_APPLY_ISSUE = ApprovalTarget(
    action="apply_issue",
    names=["अर्को / Next: review", "आवेदन पेश गर्नुहोस् / Submit application"],
    path="/apply/nic-asia-debenture*",
)


def test_path_binding_matches_the_glob() -> None:
    assert approval_matches(
        _APPLY_ISSUE,
        "SUBMIT",
        "आवेदन पेश गर्नुहोस् / Submit application",
        None,
        "http://x/apply/nic-asia-debenture/review",
    )


def test_path_binding_matches_the_pre_review_page_too() -> None:
    assert approval_matches(
        _APPLY_ISSUE, "CLICK", "अर्को / Next: review", None, "http://x/apply/nic-asia-debenture"
    )


def test_path_binding_denies_a_different_issue() -> None:
    assert not approval_matches(
        _APPLY_ISSUE,
        "SUBMIT",
        "आवेदन पेश गर्नुहोस् / Submit application",
        None,
        "http://x/apply/sunrise-bank-rights/review",
    )


def test_login_is_not_in_apply_issue_names() -> None:
    # docs/PLAN.md Q2's own fix target (E6): a login SUBMIT must never match
    # apply_issue, even on the right path.
    assert not approval_matches(
        _APPLY_ISSUE, "SUBMIT", "लगइन / Login", None, "http://x/apply/nic-asia-debenture"
    )
