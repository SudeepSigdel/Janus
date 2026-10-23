from __future__ import annotations

import io
from typing import Any

import pytest
from PIL import Image

from janus.extract_doc import (
    DocumentError,
    crop_field,
    extract_document,
    prepare_image,
    wanted_fields,
)
from janus.llm import LLMError


def _photo(size: tuple[int, int] = (800, 400), exif: bool = False) -> bytes:
    image = Image.new("RGB", size, (240, 236, 220))
    out = io.BytesIO()
    if exif:
        data = Image.Exif()
        data[0x010F] = "SomePhoneMaker"  # camera make
        image.save(out, "JPEG", exif=data)
    else:
        image.save(out, "JPEG")
    return out.getvalue()


class FakeLLM:
    """Read A returns `cells`; each crop read returns the next value from `crops`."""

    def __init__(
        self,
        cells: dict[str, Any],
        crops: list[str | None],
        ocr: list[dict] | None = None,
    ) -> None:
        self.cells = cells
        self.crops = list(crops)
        self.ocr = ocr  # None: the OCR reader is not installed
        self.calls: list[tuple[str, int]] = []

    def ocr_lines(self, image):
        if self.ocr is None:
            raise LLMError("OCR needs the `ui` extra")
        return self.ocr

    def chat_vision_json(self, prompt, images, schema, *, model=None):
        self.calls.append((prompt, len(images)))
        if "value" in schema["properties"] and len(schema["properties"]) == 1:
            return {"value": self.crops.pop(0)}
        return self.cells


BOX = [100, 100, 300, 140]
B = "gemma3:4b"


def test_two_matching_reads_are_agreed_with_a_normalized_value() -> None:
    llm = FakeLLM({"boid": {"value": "75513137", "box": BOX}}, ["७५५१३१३७"])
    doc = extract_document(llm, _photo(), ["boid"], model_b=B)
    field = doc.fields[0]
    assert (field.key, field.value, field.status) == ("boid", "७५५१३१३७", "agreed")
    assert field.box is not None and 0 < field.box[0] < field.box[2] <= 1


def test_disagreeing_reads_are_flagged_for_the_person_to_check() -> None:
    llm = FakeLLM({"boid": {"value": "75513137", "box": BOX}}, ["75513131"])
    field = extract_document(llm, _photo(), ["boid"], model_b=B).fields[0]
    assert field.status == "check"
    assert field.value == "७५५१३१३७"  # read A's value is the suggestion


def test_an_impossible_first_read_falls_back_to_the_valid_second_read() -> None:
    llm = FakeLLM({"boid": {"value": "7551", "box": BOX}}, ["75513137"])
    field = extract_document(llm, _photo(), ["boid"], model_b=B).fields[0]
    assert (field.status, field.value) == ("check", "७५५१३१३७")


def test_two_invalid_reads_are_missing_not_guessed() -> None:
    llm = FakeLLM({"phone": {"value": "123", "box": BOX}}, [None])
    field = extract_document(llm, _photo(), ["phone"], model_b=B).fields[0]
    assert (field.status, field.value) == ("missing", None)


def test_no_usable_box_means_a_single_read_that_is_never_agreed() -> None:
    llm = FakeLLM({"crn": {"value": "CRN-353799", "box": None}}, [])
    field = extract_document(llm, _photo(), ["crn"], model_b=B).fields[0]
    assert (field.status, field.value, field.box) == ("check", "CRN-353799", None)
    assert len(llm.calls) == 1  # no crop read was made


def test_a_degenerate_or_inverted_box_is_not_used() -> None:
    for box in ([10, 10, 12, 12], [300, 300, 100, 100]):
        llm = FakeLLM({"crn": {"value": "CRN-353799", "box": box}}, [])
        assert extract_document(llm, _photo(), ["crn"], model_b=B).fields[0].box is None


def test_a_model_error_on_read_a_gives_missing_fields() -> None:
    class Broken(FakeLLM):
        def chat_vision_json(self, *a, **k):
            raise LLMError("ollama down")

    doc = extract_document(Broken({}, []), _photo(), ["crn", "bank"], model_b=B)
    assert [f.status for f in doc.fields] == ["missing", "missing"]


def test_malformed_model_output_never_raises() -> None:
    llm = FakeLLM({"crn": "not an object", "bank": {"value": 7, "box": "x"}}, [])
    doc = extract_document(llm, _photo(), ["crn", "bank"], model_b=B)
    assert [f.status for f in doc.fields] == ["missing", "missing"]


@pytest.mark.parametrize("key", ["pin", "password", "otp"])
def test_secrets_are_never_requested_from_a_photo(key: str) -> None:
    with pytest.raises(DocumentError):
        extract_document(FakeLLM({}, []), _photo(), ["name", key], model_b=B)


def test_unknown_and_empty_requests_are_refused() -> None:
    with pytest.raises(DocumentError):
        wanted_fields(["favourite_colour"])
    with pytest.raises(DocumentError):
        wanted_fields([])
    assert wanted_fields(["name", "name", "ward"]) == ["name", "ward"]


def test_a_non_image_upload_is_refused() -> None:
    with pytest.raises(DocumentError):
        prepare_image(b"%PDF-1.4 definitely not a picture")


def test_prepare_image_strips_exif_and_downscales() -> None:
    raw = _photo((3000, 1500), exif=True)
    assert Image.open(io.BytesIO(raw)).getexif().get(0x010F) == "SomePhoneMaker"
    prepared, width, height = prepare_image(raw)
    image = Image.open(io.BytesIO(prepared))
    assert len(image.getexif()) == 0
    assert max(image.size) == 1280 and image.size == (width, height)


def test_crop_field_enlarges_and_stays_inside_the_image() -> None:
    crop = Image.open(io.BytesIO(crop_field(_photo(), (0, 0, 50, 20))))
    assert crop.width > 50 and crop.height > 20
    assert crop.width <= int((50 + 14) * 2.5) + 1


def test_without_a_second_model_nothing_is_ever_agreed() -> None:
    llm = FakeLLM({"boid": {"value": "75513137", "box": BOX}}, ["75513137"])
    field = extract_document(llm, _photo(), ["boid"]).fields[0]
    assert (field.status, field.value) == ("check", "७५५१३१३७")
    assert len(llm.calls) == 1  # no crop read was made


def _lines(label: str, value: str) -> list[dict]:
    return [
        {"text": label, "box": [100, 60, 400, 90]},
        {"text": value, "box": [100, 100, 300, 140]},
    ]


def test_ocr_and_a_vision_model_agreeing_is_agreed_and_uses_the_ocr_box() -> None:
    llm = FakeLLM(
        {"boid": {"value": "75513137", "box": [10, 10, 60, 40]}}, [None], _lines("BOID", "७५५१३१३७")
    )
    field = extract_document(llm, _photo(), ["boid"], model_b=B).fields[0]
    assert (field.status, field.value) == ("agreed", "७५५१३१३७")
    assert field.box is not None and abs(field.box[0] - 100 / 800) < 1e-6  # the OCR line's box


def test_two_vision_models_alone_do_not_agree_while_ocr_is_running() -> None:
    """They misread alike (a ward 11 read as 22 by both), so OCR must be one of the two."""
    llm = FakeLLM({"ward": {"value": "२२", "box": BOX}}, ["२२"], _lines("Ward", "११"))
    field = extract_document(llm, _photo(), ["ward"], model_b=B).fields[0]
    assert field.status == "check"
    assert field.value == "11"  # a digit field: the OCR reading is the suggestion


def test_the_ocr_reading_is_the_suggestion_for_digits_but_not_for_names() -> None:
    cells = {"name": {"value": "सीता श्रेष्ठ", "box": BOX}}
    llm = FakeLLM(cells, [None], _lines("Name", "सीता श्रेष्ठा"))
    field = extract_document(llm, _photo(), ["name"], model_b=B).fields[0]
    assert (field.status, field.value) == ("check", "सीता श्रेष्ठ")


def test_a_field_ocr_cannot_find_is_still_read_by_the_vision_models() -> None:
    llm = FakeLLM({"crn": {"value": "CRN-353799", "box": BOX}}, ["CRN-353799"], [])
    field = extract_document(llm, _photo(), ["crn"], model_b=B).fields[0]
    assert (field.status, field.value) == ("check", "CRN-353799")


def test_use_ocr_false_skips_the_ocr_reader() -> None:
    llm = FakeLLM(
        {"boid": {"value": "75513137", "box": BOX}}, ["75513137"], _lines("BOID", "७५५१३१३७")
    )
    field = extract_document(llm, _photo(), ["boid"], model_b=B, use_ocr=False).fields[0]
    assert field.status == "agreed"  # the vision pair, since no OCR reader ran
