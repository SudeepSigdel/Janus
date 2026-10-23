"""Label-anchored values from OCR lines (janus/ocr_fields.py). Pure text and boxes, no model."""

from __future__ import annotations

from janus.ocr_fields import read_ocr_fields


def line(text: str, y: int, x: int = 100, h: int = 30) -> dict:
    return {"text": text, "box": [x, y, x + 300, y + h]}


SLIP = [
    line("डमियाट खाता वविरण (नमूना / SAMPLE)", 90),
    line("नाम/ Name", 180),
    line("वीओआईडी / BOID", 250),
    line("७५५१३१३७", 286),
    line("सीआरएन / CRN", 328),
    line("CRN-353799", 366),
    line("बैंक /Bank", 407),
    line("नबिल बैंक", 440),
    line("खाता खोलेको मिति: २०८०-०१-०९ फोन: ९८७५११६३७२", 483),
]


def test_a_value_below_its_label_is_found_even_if_it_contains_the_label_word() -> None:
    found = read_ocr_fields(SLIP, ["boid", "crn", "bank"])
    assert found["boid"][0] == "७५५१३१३७"
    assert found["crn"][0] == "CRN-353799"  # "CRN" appears in the value itself
    assert found["bank"][0] == "नबिल बैंक"
    assert found["crn"][1] == [100.0, 366.0, 400.0, 396.0]  # the value line's own box


def test_a_label_with_no_value_printed_gives_nothing_not_the_next_label() -> None:
    found = read_ocr_fields(SLIP, ["name"])  # "Name" is followed directly by the BOID label
    assert found["name"] == (None, None)


def test_a_value_on_the_same_line_after_a_colon() -> None:
    lines = [line("Mobile: 98-7511-6372", 100)]
    assert read_ocr_fields(lines, ["phone"])["phone"][0] == " 98-7511-6372"


def test_an_invalid_value_is_never_returned() -> None:
    lines = [line("मोबाइल / Mobile", 100), line("९८७५११", 140)]  # too short for a phone number
    assert read_ocr_fields(lines, ["phone"])["phone"] == (None, None)
    lines = [line("जन्म मिति / Date of birth (BS)", 100), line("८८-१०-०८०८", 140)]
    assert read_ocr_fields(lines, ["dob_bs"])["dob_bs"] == (None, None)


def test_a_value_far_below_its_label_is_not_attached_to_it() -> None:
    lines = [line("मोबाइल / Mobile", 100), line("९८७५११६३७२", 400)]
    assert read_ocr_fields(lines, ["phone"])["phone"] == (None, None)


def test_the_birth_label_serves_both_calendars_and_the_validators_choose() -> None:
    lines = [line("जन्म मिति / Date of birth", 100), line("२०४०-०७-१४", 140)]
    found = read_ocr_fields(lines, ["dob_bs", "dob_ad"])
    assert found["dob_bs"][0] == "२०४०-०७-१४"
    assert found["dob_ad"] == (None, None)  # a BS year is not a plausible Gregorian date


def test_malformed_lines_are_ignored() -> None:
    lines = [{"text": 5, "box": [0, 0, 1, 1]}, {"text": "Name", "box": [1, 2]}, line("Ward", 50)]
    assert read_ocr_fields(lines, ["ward"])["ward"] == (None, None)
