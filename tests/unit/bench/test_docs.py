from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from janus import extract_doc
from janus.config import Settings
from janus.text.fields import normalize_field
from janus_bench.docs import make, score
from janus_bench.docs.make import WANT, make_documents
from janus_bench.docs.score import format_report, score_documents


def _has_devanagari_font() -> bool:
    try:
        make._font(10)
    except FileNotFoundError:
        return False
    return True


needs_font = pytest.mark.skipif(
    not _has_devanagari_font(),
    reason="no Devanagari font; install fonts-noto-core or set JANUS_DOC_FONT",
)


@needs_font
def test_documents_are_deterministic_and_their_truth_is_valid(tmp_path: Path) -> None:
    first = make_documents(tmp_path / "a", seed=3)
    second = make_documents(tmp_path / "b", seed=3)
    assert first == second
    assert len(first) == 20
    for name, want in first.items():
        assert (tmp_path / "a" / name).is_file()
        for key in WANT[want["kind"]]:
            assert normalize_field(key, want[key]) is not None, (name, key, want[key])


def test_every_document_is_marked_as_a_fictional_sample() -> None:
    for maker in make._MAKERS.values():
        title, *_ = maker(random.Random(1))
        assert "नमूना" in title and "SAMPLE" in title


@needs_font
def test_scoring_counts_agreed_wrong_separately(tmp_path: Path, monkeypatch) -> None:
    make_documents(tmp_path, seed=1)
    truth = json.loads((tmp_path / "truth.json").read_text(encoding="utf-8"))
    by_photo = {(tmp_path / name).read_bytes(): want for name, want in truth.items()}
    wrong_once = {"done": False}

    def fake_extract(llm, photo, keys, *, model_a=None, model_b=None, use_ocr=True):
        """Agrees on the right value for every field, except one agreed field that is wrong."""
        want = by_photo[photo]
        fields = []
        for key in keys:
            value = normalize_field(key, want[key])
            if not wrong_once["done"]:
                wrong_once["done"], value = True, "wrong"
            fields.append(extract_doc.DocField(key=key, value=value, status="agreed"))
        return extract_doc.ExtractedDocument(fields=fields, width=1, height=1, image=b"")

    class NoLLM:
        def close(self) -> None:
            pass

    monkeypatch.setattr(score, "extract_document", fake_extract)
    monkeypatch.setattr(score, "LLMClient", lambda settings: NoLLM())
    tally, _ = score_documents(tmp_path, Settings())
    assert tally["fields"] == sum(len(WANT[t["kind"]]) for t in truth.values())
    assert tally["agreed_wrong"] == 1
    assert tally["agreed_correct"] == tally["fields"] - 1
    assert "WRONG 1" in format_report(tally)
