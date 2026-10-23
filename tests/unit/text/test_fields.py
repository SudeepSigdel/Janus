from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from janus.text.fields import NORMALIZERS, normalize_field


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("name", "  सीता   श्रेष्ठ ", "सीता श्रेष्ठ"),
        ("citizenship_no", "60-18-15-90830", "६०-१८-१५-९०८३०"),
        ("citizenship_no", "६०-१८-१५-९०८३०", "६०-१८-१५-९०८३०"),
        ("dob_bs", "२०४०-०७-१४", "2040-07-14"),
        ("dob_bs", "2040/7/14", "2040-07-14"),
        ("dob_ad", "२१/०७/२००४", "2004-07-21"),
        ("dob_ad", "2004-07-21", "2004-07-21"),
        ("ward", "०३", "3"),
        ("boid", "75513137", "७५५१३१३७"),
        ("crn", "CRN-353799", "CRN-353799"),
        ("crn", "353799", "CRN-353799"),
        ("crn", "crn353799", "CRN-353799"),
        ("phone", "९८७५११६३७२", "9875116372"),
        ("phone", "98-7511-6372", "9875116372"),
        ("bank", " नबिल  बैंक ", "नबिल बैंक"),
    ],
)
def test_accepts_and_canonicalises(key: str, raw: str, expected: str) -> None:
    assert normalize_field(key, raw) == expected


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("name", "Sita Shrestha"),  # the form wants Devanagari
        ("name", "सीता 2"),
        ("name", ""),
        ("citizenship_no", "12345"),
        ("dob_bs", "२१००-०१-०१"),  # outside the accepted years
        ("dob_bs", "२०४०-१३-०१"),  # month 13
        ("dob_bs", "२०४०-०२-४०"),  # day 40
        ("dob_bs", "१९९९-०१-०१"),
        ("dob_ad", "31/02/2004"),  # no such day
        ("dob_ad", "21/07/1850"),
        ("ward", "0"),
        ("ward", "33"),
        ("boid", "1234567"),
        ("boid", "123456789"),
        ("crn", "CRN-12345"),
        ("crn", "ABC-123456"),
        ("phone", "8875116372"),  # must start with 9
        ("phone", "987511637"),
        ("bank", ""),
        ("bank", None),
    ],
)
def test_rejects_impossible_reads(key: str, raw: str | None) -> None:
    assert normalize_field(key, raw) is None


@pytest.mark.parametrize("key", ["pin", "password", "otp", "unknown_field"])
def test_secrets_and_unknown_keys_are_never_read_off_a_photo(key: str) -> None:
    assert normalize_field(key, "1234") is None


@given(st.sampled_from(sorted(NORMALIZERS)), st.text(max_size=40))
def test_normalising_is_idempotent_and_never_raises(key: str, raw: str) -> None:
    once = normalize_field(key, raw)
    if once is not None:
        assert normalize_field(key, once) == once


@given(st.integers(min_value=0, max_value=99_999_999))
def test_boid_is_the_same_in_either_digit_script(n: int) -> None:
    ascii_digits = f"{n:08d}"
    devanagari = ascii_digits.translate(str.maketrans("0123456789", "०१२३४५६७८९"))
    assert normalize_field("boid", ascii_digits) == normalize_field("boid", devanagari)
