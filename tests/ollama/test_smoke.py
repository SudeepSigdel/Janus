from __future__ import annotations

import pytest

from janus.llm import LLMClient, LLMError

pytestmark = pytest.mark.ollama


def test_planner_returns_valid_json() -> None:
    client = LLMClient()
    try:
        client.list_models()
    except LLMError:
        pytest.fail("Ollama is not reachable; start it and run `janus doctor`")
    out = client.chat_json(
        [{"role": "user", "content": "Return the word ok in field value."}],
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        model=client.settings.base_model,
    )
    assert isinstance(out["value"], str)


def test_embeddings_have_dimension() -> None:
    vecs = LLMClient().embed(["नमस्ते", "hello"])
    assert len(vecs) == 2 and len(vecs[0]) == len(vecs[1]) > 0
