"""Candidate field values from OCR lines, found by the printed label next to them.

The OCR model (`LLMClient.ocr_lines`) returns text lines with boxes and knows nothing about
fields. Here, deterministic code finds each wanted field's printed label (English or Devanagari),
takes the value on the same line after a colon or on the line just below, and keeps it only if it
passes `janus/text/fields.py`. A label with no valid value next to it gives nothing, never a guess.
Every line is untrusted text; the result is a proposal like any other reader's.
"""

from __future__ import annotations

from typing import Any

from janus.text.fields import normalize_field

# Lowercased fragments that identify a field's label. Devanagari spellings include the common OCR
# slips; English words are the most stable anchors on a bilingual document.
ANCHORS: dict[str, tuple[str, ...]] = {
    "name": ("name", "नाम"),
    "citizenship_no": ("citizenship", "नागरिकता", "नागरकिता", "नागर"),
    "dob_bs": ("birth", "जन्म"),
    "dob_ad": ("birth", "जन्म"),
    "ward": ("ward", "वडा"),
    "boid": ("boid", "वीओआईडी", "बीओआईडी"),
    "crn": ("crn", "सीआरएन"),
    "bank": ("bank", "बैंक", "बैङ्क"),
    "phone": ("mobile", "मोबाइल", "phone"),
}
# A value is searched for on the next few lines only, and only if they are close below the label.
MAX_LINES_BELOW = 2
MAX_GAP_FACTOR = 2.5  # of the label's height


def _is_label(text: str) -> bool:
    lowered = text.lower()
    return any(anchor in lowered for anchors in ANCHORS.values() for anchor in anchors)


def read_ocr_fields(
    lines: list[dict[str, Any]], keys: list[str]
) -> dict[str, tuple[str | None, list[float] | None]]:
    """{key: (value as printed, its box)} for each key with a valid value next to its label."""
    ordered = sorted(
        (
            line
            for line in lines
            if isinstance(line.get("text"), str) and len(line.get("box", [])) == 4
        ),
        key=lambda line: (line["box"][1], line["box"][0]),
    )
    found: dict[str, tuple[str | None, list[float] | None]] = {}
    for key in keys:
        found[key] = (None, None)
        for index, line in enumerate(ordered):
            lowered = line["text"].lower()
            if not any(anchor in lowered for anchor in ANCHORS.get(key, ())):
                continue
            candidates: list[tuple[str, list[float]]] = []
            if ":" in line["text"]:  # "Name: Sita" on one line
                candidates.append((line["text"].rsplit(":", 1)[1], line["box"]))
            height = max(1.0, line["box"][3] - line["box"][1])
            for below in ordered[index + 1 : index + 1 + MAX_LINES_BELOW]:
                if below["box"][1] - line["box"][3] > MAX_GAP_FACTOR * height:
                    break
                if normalize_field(key, below["text"]) is None and _is_label(below["text"]):
                    break  # the next field's label: this field has no value printed
                candidates.append((below["text"], below["box"]))
            valid = next(
                ((text, box) for text, box in candidates if normalize_field(key, text) is not None),
                None,
            )
            if valid is not None:
                found[key] = (valid[0], [float(v) for v in valid[1]])
                break
    return found
