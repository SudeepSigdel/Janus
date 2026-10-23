"""Read the fields a form needs off a photo of a document (docs/PLAN.md "Photo to fill").

The vision model is an untrusted proposer. It never decides anything: every value it returns is
checked by janus/text/fields.py, marked agreed / check / missing, and shown to the person next to
the photo; nothing becomes a task input until they confirm it (janus/ui). Two reads per field:
read A is the whole page, read B is a 2.5x crop around the box read A gave. Both come from the
local model (`LLMClient.chat_vision_json`, loopback Ollama only). A field is `agreed` only when
both reads normalize to the same valid value; the same model can repeat a misread, so `agreed`
means "worth pre-filling", never "correct".

Document images and the values read from them are personal data: nothing in this module traces
or logs them, and the prepared image is returned to the caller to hold in memory only.
"""

from __future__ import annotations

import io
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from janus.llm import LLMClient, LLMError
from janus.ocr_fields import read_ocr_fields
from janus.text.fields import NEVER_FROM_PHOTO, NORMALIZERS, normalize_field

MAX_SIDE = 1280  # larger photos are downscaled: the model's box coordinates then match the image
CROP_PAD = 14
CROP_SCALE = 2.5

FIELD_HINTS = {
    "name": "the person's full name",
    "citizenship_no": "the citizenship number (digits and hyphens)",
    "dob_bs": "the date of birth in the Bikram Sambat calendar",
    "dob_ad": "the date of birth",
    "ward": "the ward number",
    "boid": "the BOID number",
    "crn": "the CRN",
    "bank": "the bank name",
    "phone": "the mobile number",
}

Status = Literal["agreed", "check", "missing"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Cell(_Model):
    value: str | None = None
    box: list[float] | None = None


class _Crop(_Model):
    value: str | None = None


class DocField(_Model):
    key: str
    # The canonical suggestion (or None). Never auto-accepted: the person confirms or retypes.
    value: str | None
    status: Status
    # x1, y1, x2, y2 as fractions of the prepared image, or None when the model gave no usable box.
    box: tuple[float, float, float, float] | None = None


class ExtractedDocument(_Model):
    fields: list[DocField]
    width: int
    height: int
    # The prepared (EXIF-free, downscaled) photo for the confirm screen. Held in memory only.
    image: bytes


class DocumentError(ValueError):
    """The upload is not an image Janus can read, or asks for a field it must not read."""


def wanted_fields(keys: list[str]) -> list[str]:
    """`keys` checked against what a photo may supply, order kept, duplicates dropped."""
    seen: list[str] = []
    for key in keys:
        if key in NEVER_FROM_PHOTO:
            raise DocumentError(f"{key!r} is never read from a photo; the person supplies it")
        if key not in NORMALIZERS:
            raise DocumentError(f"unknown document field {key!r}")
        if key not in seen:
            seen.append(key)
    if not seen:
        raise DocumentError("no fields requested")
    return seen


def prepare_image(raw: bytes) -> tuple[bytes, int, int]:
    """Orient, flatten to RGB, downscale, and re-encode as JPEG. Re-encoding drops EXIF data
    (GPS position, device, time) so only pixels are kept."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise DocumentError("that file is not a readable image") from exc
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=90)
    return out.getvalue(), image.width, image.height


def crop_field(image: bytes, box: tuple[float, float, float, float]) -> bytes:
    """The region of `image` inside `box` (pixels), padded and enlarged for a second read."""
    from PIL import Image

    source = Image.open(io.BytesIO(image)).convert("RGB")
    x1, y1, x2, y2 = box
    crop = source.crop(
        (
            max(0, int(x1) - CROP_PAD),
            max(0, int(y1) - CROP_PAD),
            min(source.width, int(x2) + CROP_PAD),
            min(source.height, int(y2) + CROP_PAD),
        )
    )
    crop = crop.resize(
        (max(1, int(crop.width * CROP_SCALE)), max(1, int(crop.height * CROP_SCALE))),
        Image.LANCZOS,
    )
    out = io.BytesIO()
    crop.save(out, "PNG")
    return out.getvalue()


def _read_a_schema(keys: list[str]) -> dict[str, Any]:
    cell = {
        "type": "object",
        "properties": {
            "value": {"type": ["string", "null"]},
            "box": {
                "type": ["array", "null"],
                "items": {"type": "number"},
                "minItems": 4,
                "maxItems": 4,
            },
        },
        "required": ["value", "box"],
    }
    return {
        "type": "object",
        "properties": {k: cell for k in keys},
        "required": keys,
        "additionalProperties": False,
    }


_CROP_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": ["string", "null"]}},
    "required": ["value"],
    "additionalProperties": False,
}


def _valid_box(raw: list[float] | None, width: int, height: int) -> tuple[float, ...] | None:
    if raw is None or len(raw) != 4:
        return None
    x1, y1, x2, y2 = raw
    x1, x2 = max(0.0, min(x1, width)), max(0.0, min(x2, width))
    y1, y2 = max(0.0, min(y1, height)), max(0.0, min(y2, height))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    return (x1, y1, x2, y2)


def _read_a(llm: LLMClient, image: bytes, keys: list[str], model: str | None) -> dict[str, _Cell]:
    prompt = (
        "Photo of a document. For each field, copy the printed value exactly as written (keep "
        "Devanagari digits) and give the pixel box [x1,y1,x2,y2] of the value. Use null if the "
        "field is not on the document. Fields: "
        + "; ".join(f"{k} = {FIELD_HINTS[k]}" for k in keys)
        + "."
    )
    try:
        raw = llm.chat_vision_json(prompt, [image], _read_a_schema(keys), model=model)
    except LLMError:
        return {}
    if not isinstance(raw, dict):
        return {}
    cells: dict[str, _Cell] = {}
    for key in keys:
        try:
            cells[key] = _Cell.model_validate(raw.get(key) or {})
        except ValidationError:
            cells[key] = _Cell()
    return cells


def _read_b(
    llm: LLMClient, image: bytes, key: str, box: tuple[float, ...], model: str | None
) -> str | None:
    prompt = (
        f"This is a cropped photo of one printed field: {FIELD_HINTS[key]}. Copy the printed "
        "text exactly, keeping Devanagari digits. Use null if it is unreadable."
    )
    try:
        crop = crop_field(image, (box[0], box[1], box[2], box[3]))
        raw = llm.chat_vision_json(prompt, [crop], _CROP_SCHEMA, model=model)
        return _Crop.model_validate(raw).value
    except (LLMError, ValidationError, OSError):
        return None


# Fields where the OCR reader is trusted ahead of the vision models when nothing agrees: it reads
# long digit strings that they misread. Names and bank names are the other way round.
_OCR_FIRST = frozenset({"citizenship_no", "dob_bs", "dob_ad", "ward", "boid", "crn", "phone"})


def _read_c(llm: LLMClient, image: bytes, keys: list[str]) -> dict[str, tuple[str | None, Any]]:
    """Read C: a local OCR model, label-anchored (janus/ocr_fields.py). {} if unavailable."""
    try:
        return read_ocr_fields(llm.ocr_lines(image), keys)
    except (LLMError, AttributeError):
        return {}


def _decide(
    key: str, a: str | None, b: str | None, c: str | None, ocr_ran: bool
) -> tuple[str | None, Status, str]:
    """(value, status, which reader supplied it) from the three normalized reads.

    Agreement needs two readers on the same valid value. The two vision models misread alike
    (measured: both read a ward "११" as "२२" and the same wrong name), so while the OCR reader is
    running it must be one of the two; a vision-only pair counts only when there is no OCR reader.
    One reader alone is only ever a `check` suggestion."""
    valid = [(name, v) for name, v in (("c", c), ("a", a), ("b", b)) if v is not None]
    for _, value in valid:
        supporters = [name for name, v in valid if v == value]
        if len(supporters) >= 2 and (not ocr_ran or "c" in supporters):
            return value, "agreed", "c" if "c" in supporters else supporters[0]
    if not valid:
        return None, "missing", ""
    order = ("c", "a", "b") if key in _OCR_FIRST else ("a", "b", "c")
    name, value = min(valid, key=lambda item: order.index(item[0]))
    return value, "check", name


def extract_document(
    llm: LLMClient,
    photo: bytes,
    keys: list[str],
    *,
    model_a: str | None = None,
    model_b: str | None = None,
    use_ocr: bool = True,
) -> ExtractedDocument:
    """Candidate values for `keys` read off `photo`, each marked agreed / check / missing.

    Up to three independent readers: A (a vision model on the whole photo), B (a different vision
    model re-reading a crop; None skips it) and C (a local OCR model; `use_ocr=False` skips it).
    A field is `agreed` only when two of them give the same valid value."""
    keys = wanted_fields(keys)
    image, width, height = prepare_image(photo)
    cells = _read_a(llm, image, keys, model_a)
    ocr = _read_c(llm, image, keys) if use_ocr else {}
    fields: list[DocField] = []
    for key in keys:
        cell = cells.get(key, _Cell())
        box = _valid_box(cell.box, width, height)
        a = normalize_field(key, cell.value)
        b = (
            normalize_field(key, _read_b(llm, image, key, box, model_b))
            if box and model_b
            else None
        )
        raw_c, box_c = ocr.get(key, (None, None))
        c = normalize_field(key, raw_c)
        value, status, source = _decide(key, a, b, c, ocr_ran=bool(ocr))
        # The OCR line's box is the exact place the value was printed; prefer it for the close-up.
        shown = _valid_box(box_c, width, height) if c is not None and source == "c" else box
        fields.append(
            DocField(
                key=key,
                value=value,
                status=status,
                box=None
                if shown is None
                else (shown[0] / width, shown[1] / height, shown[2] / width, shown[3] / height),
            )
        )
    return ExtractedDocument(fields=fields, width=width, height=height, image=image)
