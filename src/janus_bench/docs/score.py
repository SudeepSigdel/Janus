"""Score `janus.extract_doc.extract_document` against the generated documents.

The headline is not raw accuracy but what a person would see on the confirm screen: how many
fields come pre-filled and agreed, how many of those are right, and how many right values sit
behind a "check" flag. A field that is agreed and wrong is the case that must stay rare.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from janus.config import Settings
from janus.extract_doc import extract_document
from janus.llm import LLMClient
from janus.text.fields import normalize_field
from janus_bench.docs.make import WANT


def score_documents(
    docs: Path,
    settings: Settings,
    model_a: str | None = None,
    model_b: str | None = None,
    use_ocr: bool | None = None,
) -> tuple[Counter, list[dict]]:
    truth = json.loads((docs / "truth.json").read_text(encoding="utf-8"))
    tally: Counter = Counter()
    rows: list[dict] = []
    llm = LLMClient(settings)
    try:
        for name, want in truth.items():
            doc = extract_document(
                llm,
                (docs / name).read_bytes(),
                WANT[want["kind"]],
                model_a=model_a or settings.vision_model,
                model_b=model_b if model_b is not None else settings.vision_model_b,
                use_ocr=settings.ocr_enabled if use_ocr is None else use_ocr,
            )
            for field in doc.fields:
                expected = normalize_field(field.key, want[field.key])
                correct = field.value is not None and field.value == expected
                tally["fields"] += 1
                tally[f"status_{field.status}"] += 1
                if field.status == "agreed":
                    tally["agreed_correct" if correct else "agreed_wrong"] += 1
                elif correct:
                    tally["flagged_but_suggestion_correct"] += 1
                tally["suggestion_correct"] += correct
                rows.append(
                    {
                        "doc": name,
                        "key": field.key,
                        "status": field.status,
                        "correct": correct,
                        "value": field.value,  # synthetic documents only: safe to keep
                        "expected": expected,
                    }
                )
    finally:
        llm.close()
    return tally, rows


def format_report(tally: Counter) -> str:
    n = tally["fields"] or 1
    return "\n".join(
        [
            f"fields scored:                {tally['fields']}",
            f"suggestion correct (any):     {tally['suggestion_correct']} "
            f"({tally['suggestion_correct'] / n:.0%})",
            f"agreed:                       {tally['status_agreed']} "
            f"(correct {tally['agreed_correct']}, WRONG {tally['agreed_wrong']})",
            f"check (amber):                {tally['status_check']} "
            f"(of all flagged, suggestion correct {tally['flagged_but_suggestion_correct']})",
            f"missing (no valid read):      {tally['status_missing']}",
            f"pre-filled and right:         {tally['agreed_correct'] / n:.0%} of fields",
        ]
    )
