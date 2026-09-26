"""Model smoke test: 10 structured-output prompts (en/ne) per candidate model.

Run: uv run python tests/ollama/smoke_models.py
Writes results/m0-model-smoke.json and prints a ranking.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from janus.config import get_settings
from janus.llm import LLMClient, LLMError

CANDIDATES = ("qwen3:8b", "qwen2.5:7b-instruct", "qwen3:4b")
OUT = Path(__file__).resolve().parents[2] / "results" / "m0-model-smoke.json"


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}

# (prompt, expected substring in value, case-insensitive)
PROMPTS: list[tuple[str, str]] = [
    ("Return the capital of Nepal as JSON field value.", "kathmandu"),
    ("Convert the digits ४५ to ASCII digits. Put the result in value.", "45"),
    ("Which ward select option matches 'Ward 3'? Options: Ward 1, Ward 3, Ward 5.", "ward 3"),
    ("Return the word 'submit' in value.", "submit"),
    ("What is 7 plus 5? Put the number in value.", "12"),
    ("नेपालको राजधानी के हो? value मा अंग्रेजीमा उत्तर देऊ।", "kathmandu"),
    ("८ लाई अंग्रेजी अंकमा बदल। value मा नतिजा राख।", "8"),
    ("'रद्द गर्नुहोस्' को अंग्रेजी अर्थ 'cancel' हो? value मा 'cancel' लेख।", "cancel"),
    ("'आवेदन पेश गर्नुहोस्' मा कुन शब्दले पेश गर्नु भन्ने अर्थ दिन्छ? value मा 'submit' लेख।", "submit"),
    ("Echo the Nepali word नमस्ते in value.", "नमस्ते"),
]


def run_model(client: LLMClient, model: str) -> dict:
    valid = correct = 0
    latencies: list[float] = []
    errors: list[str] = []
    for prompt, expected in PROMPTS:
        start = time.perf_counter()
        try:
            raw = client.chat_json(
                [{"role": "user", "content": prompt}], SCHEMA, model=model, schema_name="answer"
            )
            latencies.append(time.perf_counter() - start)
            answer = Answer.model_validate(raw)
            valid += 1
            if expected.lower() in answer.value.lower():
                correct += 1
        except (LLMError, ValidationError) as exc:
            latencies.append(time.perf_counter() - start)
            errors.append(str(exc)[:200])
    return {
        "model": model,
        "json_valid": valid,
        "correct": correct,
        "total": len(PROMPTS),
        "mean_latency_s": round(sum(latencies) / len(latencies), 2),
        "errors": errors,
    }


def main() -> None:
    settings = get_settings().model_copy(update={"request_timeout_s": 300.0})
    client = LLMClient(settings)
    installed = client.list_models()
    results = []
    for model in CANDIDATES:
        if model not in installed:
            print(f"skip {model}: not pulled")
            continue
        results.append(run_model(client, model))
        print(results[-1])
    client.close()
    results.sort(key=lambda r: (-r["json_valid"], -r["correct"], r["mean_latency_s"]))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    if results:
        print(f"default planner by ranking: {results[0]['model']}")


if __name__ == "__main__":
    main()
