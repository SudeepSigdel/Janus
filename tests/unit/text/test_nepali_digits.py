"""Devanagari digit conversion and text normalization."""

from __future__ import annotations

from janus.text.nepali import normalize_text, to_ascii_digits, to_ne_digits


def test_to_ne_digits_converts_ascii() -> None:
    assert to_ne_digits("2056-09-17") == "२०५६-०९-१७"


def test_to_ascii_digits_converts_devanagari() -> None:
    assert to_ascii_digits("२७-०१-७६-०१२३४") == "27-01-76-01234"


def test_digit_round_trip() -> None:
    original = "9841234567"
    assert to_ascii_digits(to_ne_digits(original)) == original


def test_digit_conversion_leaves_other_characters_alone() -> None:
    assert to_ne_digits("Ward 5") == "Ward ५"
    assert to_ascii_digits("वडा ५") == "वडा 5"


def test_normalize_text_collapses_whitespace_and_casefolds() -> None:
    assert normalize_text("  Submit   Application  ") == "submit application"


def test_normalize_text_is_noop_on_already_normalized_devanagari() -> None:
    assert normalize_text("रद्द / Cancel") == "रद्द / cancel"
