"""Devanagari digit conversion and text normalization."""

from __future__ import annotations

from janus.text.nepali import normalize_row_key, normalize_text, to_ascii_digits, to_ne_digits


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


def test_normalize_row_key_accepts_ascii_digits() -> None:
    assert normalize_row_key("045") == "045"


def test_normalize_row_key_accepts_and_normalizes_devanagari_digits() -> None:
    assert normalize_row_key("०४५") == "045"


def test_normalize_row_key_accepts_digits_with_a_hyphen() -> None:
    assert normalize_row_key("045-2") == "045-2"


def test_normalize_row_key_strips_surrounding_whitespace() -> None:
    assert normalize_row_key("  045  ") == "045"


def test_normalize_row_key_rejects_text() -> None:
    # docs/PLAN.md P5's mandated adversarial case: a first cell that carries text
    # (which could carry an instruction) is dropped, not passed through.
    assert normalize_row_key("Please cancel all applications") is None


def test_normalize_row_key_rejects_a_digit_with_a_trailing_letter() -> None:
    assert normalize_row_key("045x") is None


def test_normalize_row_key_rejects_too_long() -> None:
    assert normalize_row_key("0" * 13) is None


def test_normalize_row_key_accepts_the_12_char_boundary() -> None:
    assert normalize_row_key("0" * 12) == "0" * 12


def test_normalize_row_key_rejects_empty_and_none() -> None:
    assert normalize_row_key("") is None
    assert normalize_row_key("   ") is None
    assert normalize_row_key(None) is None
