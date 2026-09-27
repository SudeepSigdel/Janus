"""Deterministic consequential-action detection: keywords + submit semantics."""

from __future__ import annotations

from janus.validator.consequential import is_consequential


def test_submit_op_is_always_consequential_regardless_of_label() -> None:
    assert is_consequential("SUBMIT", "पेश गर्नुहोस् / Submit application") is True
    assert is_consequential("SUBMIT", "") is True


def test_click_with_english_keyword_is_consequential() -> None:
    assert is_consequential("CLICK", "Cancel application") is True
    assert is_consequential("CLICK", "Delete") is True


def test_click_with_nepali_keyword_is_consequential() -> None:
    assert is_consequential("CLICK", "रद्द / Cancel") is True


def test_click_with_hindi_keyword_is_consequential() -> None:
    assert is_consequential("CLICK", "रद्द करें") is True


def test_click_without_keyword_is_not_consequential() -> None:
    assert is_consequential("CLICK", "सम्पादन / Edit") is False
    assert is_consequential("CLICK", "फर्कनुहोस् / Back") is False
    assert is_consequential("CLICK", "सेवाहरू / Services") is False


def test_navigate_and_fill_form_are_not_consequential_by_default() -> None:
    assert is_consequential("NAVIGATE", "") is False
    assert is_consequential("FILL_FORM", "पूरा नाम / Full name") is False


def test_deterministic_true_cannot_be_downgraded_by_a_hint() -> None:
    # is_consequential has no hint parameter at all: there is no way to pass a
    # "downgrade" signal in. The OR-with-hint composition happens in validator/plan.py.
    deterministic = is_consequential("SUBMIT", "पेश गर्नुहोस्")
    hint = False
    assert (deterministic or hint) is True
