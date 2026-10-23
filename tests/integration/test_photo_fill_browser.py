"""Photo to fill on a replica: real vision models, real planner, real browser.

Needs local Ollama with the vision models and the planner (`uv run pytest -m "browser and ollama"`).
The simulated person corrects every field against the photo before confirming, so this checks that
confirmed document values drive the run to the right state, not that the models read well (that is
`janus-bench docs score`).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from janus.config import get_settings
from janus_bench.docs.make import make_documents
from janus_bench.docs.photo_fill import (
    nagarpalika_applied,
    nagarpalika_case,
    sharesewa_applied,
    sharesewa_case,
)
from janus_bench.harness.server import running_site

NL = chr(10)
pytestmark = [pytest.mark.browser, pytest.mark.ollama]


def test_demat_slip_photo_to_a_submitted_share_application(tmp_path: Path) -> None:
    make_documents(tmp_path, seed=7)
    truth = json.loads((tmp_path / "truth.json").read_text(encoding="utf-8"))
    name, want = next((n, t) for n, t in truth.items() if t["kind"] == "boid")
    with running_site("sharesewa") as base:
        result = sharesewa_case(tmp_path / name, want, get_settings(), base)
    print(ascii(result.status), *map(ascii, result.stages), sep=NL)
    assert result.status == "completed", result
    assert sharesewa_applied(result, want), result.state


def test_citizenship_photo_to_a_residence_recommendation_asking_for_the_phone(
    tmp_path: Path,
) -> None:
    make_documents(tmp_path, seed=7)
    truth = json.loads((tmp_path / "truth.json").read_text(encoding="utf-8"))
    name, want = next((n, t) for n, t in truth.items() if t["kind"] == "citizen")
    with running_site("nagarpalika") as base:
        httpx.post(f"{base}/__bench/reset", json={"variant": "required_fields"}, timeout=5)
        result = nagarpalika_case(tmp_path / name, want, get_settings(), base)
    print(ascii(result.status), *map(ascii, result.stages), sep=NL)
    assert result.status == "completed", result
    assert nagarpalika_applied(result, want), result.state
