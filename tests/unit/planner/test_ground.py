"""ground_ref / ground_select_value: the three-tier funnel (exact/normalized match,
bge-m3 embedding similarity, LLM enum fallback), and the sensitive-field refusal --
all against a mocked LLM transport (no real Ollama, no Playwright)."""

from __future__ import annotations

import json

import httpx
import pytest

from janus.llm import LLMClient
from janus.observer.snapshot import Element, Fingerprint, SelectOption
from janus.planner.ground import GroundingError, ground_ref, ground_select_value


def _client(handler) -> LLMClient:
    return LLMClient(transport=httpx.MockTransport(handler))


def _no_calls_handler(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no model call expected for an exact match: {request.url}")


def _element(ref: str, name: str) -> Element:
    return Element(
        ref=ref,
        tag="a",
        role="link",
        accessible_name=name,
        name_attr=None,
        form_id=None,
        fingerprint=Fingerprint(
            role="link", accessible_name=name, name_attr=None, form_id=None, tag="a"
        ),
    )


def test_ground_ref_exact_match_short_circuits() -> None:
    elements = [_element("e0", "Next"), _element("e1", "Submit application")]
    assert ground_ref("e1", elements, _client(_no_calls_handler), threshold=0.75) == "e1"


def test_ground_ref_normalized_label_match() -> None:
    elements = [_element("e0", "Next"), _element("e1", "Submit application")]
    # The model wrote the label instead of the ref, with different case/whitespace.
    ref = ground_ref("  SUBMIT APPLICATION  ", elements, _client(_no_calls_handler), threshold=0.75)
    assert ref == "e1"


def test_ground_ref_falls_back_to_embedding_similarity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        vectors = {"Next": [1.0, 0.0], "Submit application": [0.0, 1.0]}
        target_vector = [0.0, 1.0] if "submit" in texts[0].lower() else [1.0, 0.0]
        data = [{"index": 0, "embedding": target_vector}]
        for i, text in enumerate(texts[1:], start=1):
            data.append({"index": i, "embedding": vectors[text]})
        return httpx.Response(200, json={"data": data})

    elements = [_element("e0", "Next"), _element("e1", "Submit application")]
    ref = ground_ref("please submit now", elements, _client(handler), threshold=0.5)
    assert ref == "e1"


def test_ground_ref_falls_back_to_llm_enum() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/embeddings":
            n = len(json.loads(request.content)["input"])
            return httpx.Response(
                200, json={"data": [{"index": i, "embedding": [0.0]} for i in range(n)]}
            )
        schema = json.loads(request.content)["response_format"]["json_schema"]["schema"]
        assert set(schema["properties"]["choice"]["enum"]) == {"e0", "e1"}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"choice": "e1"})}}]}
        )

    elements = [_element("e0", "Next"), _element("e1", "Submit application")]
    ref = ground_ref("finish and send it off", elements, _client(handler), threshold=0.99)
    assert ref == "e1"


def test_ground_ref_raises_when_nothing_resolves() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/embeddings":
            n = len(json.loads(request.content)["input"])
            return httpx.Response(
                200, json={"data": [{"index": i, "embedding": [0.0]} for i in range(n)]}
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"choice": "not-a-ref"})}}]}
        )

    elements = [_element("e0", "Next")]
    with pytest.raises(GroundingError):
        ground_ref("nothing like this", elements, _client(handler), threshold=0.99)


def test_ground_select_value_exact_match_short_circuits() -> None:
    options = [SelectOption(value="5", label="Ward 5")]
    value = ground_select_value(
        "$inputs.ward",
        {"ward": "5"},
        options,
        _client(_no_calls_handler),
        sensitive=False,
        threshold=0.75,
    )
    assert value == "$inputs.ward"


def test_ground_select_value_normalizes_devanagari_digits() -> None:
    options = [SelectOption(value="5", label="वडा ५")]
    value = ground_select_value(
        "५", {}, options, _client(_no_calls_handler), sensitive=False, threshold=0.75
    )
    assert value == "५"


def test_ground_select_value_refuses_to_rewrite_a_sensitive_field() -> None:
    options = [SelectOption(value="5", label="Ward 5")]
    with pytest.raises(GroundingError):
        ground_select_value(
            "$inputs.ward",
            {"ward": "wrong"},
            options,
            _client(_no_calls_handler),
            sensitive=True,
            threshold=0.75,
        )


def test_ground_select_value_rewrites_a_non_sensitive_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"choice": "5"})}}]}
        )

    options = [SelectOption(value="5", label="Ward 5")]
    value = ground_select_value(
        "Ward five", {}, options, _client(handler), sensitive=False, threshold=0.99
    )
    assert value == "5"
