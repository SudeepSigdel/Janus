from __future__ import annotations

import itertools
import json

import httpx
import pytest

import janus.llm as llm_module
from janus.config import Settings
from janus.llm import LLMClient, LLMError, NonLocalEndpointError, assert_local_url


def _fake_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real request against an in-memory MockTransport can complete faster than
    the platform clock's resolution, making `elapsed_s > 0` flaky. Tests that need
    a measurable elapsed time control the clock instead of trusting wall time."""
    ticks = itertools.count(step=0.5)
    monkeypatch.setattr(llm_module.time, "monotonic", lambda: next(ticks))


@pytest.mark.parametrize(
    "url", ["http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"]
)
def test_local_urls_allowed(url: str) -> None:
    assert_local_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://10.0.0.5:11434",
        "http://localhost.evil.com:11434",
        "http://example.com",
        "not a url",
    ],
)
def test_non_local_urls_refused(url: str) -> None:
    with pytest.raises(NonLocalEndpointError):
        assert_local_url(url)


def test_client_refuses_non_local_settings() -> None:
    with pytest.raises(NonLocalEndpointError):
        LLMClient(Settings(ollama_base_url="https://example.com"))


def _client(handler) -> LLMClient:
    return LLMClient(transport=httpx.MockTransport(handler))


def test_chat_json_request_shape_and_parse() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"a": 1}'}}]})

    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    out = _client(handler).chat_json([{"role": "user", "content": "hi"}], schema)
    assert out == {"a": 1}
    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"]["model"] == "janus-planner"
    assert seen["body"]["reasoning_effort"] == "none"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["response_format"]["json_schema"]["schema"] == schema


def test_chat_json_accumulates_usage_and_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_clock(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    client = _client(handler)
    client.chat_json([], {})
    client.chat_json([], {})
    assert client.chat_calls == 2
    assert client.prompt_tokens == 200
    assert client.completion_tokens == 40
    assert client.elapsed_s > 0


def test_chat_json_missing_usage_defaults_to_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = _client(handler)
    client.chat_json([], {})
    assert client.chat_calls == 1
    assert client.prompt_tokens == 0
    assert client.completion_tokens == 0


def test_bad_content_still_counts_the_call_and_elapsed_time() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    client = _client(handler)
    with pytest.raises(LLMError):
        client.chat_json([], {})
    assert client.chat_calls == 1


def test_http_error_still_counts_elapsed_time(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_clock(monkeypatch)
    client = _client(lambda r: httpx.Response(500, json={}))
    with pytest.raises(LLMError):
        client.chat_json([], {})
    assert client.chat_calls == 0  # the request itself failed, never became a chat call
    assert client.elapsed_s > 0


def test_chat_json_bad_content_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    with pytest.raises(LLMError):
        _client(handler).chat_json([], {})


def test_http_error_raises() -> None:
    with pytest.raises(LLMError):
        _client(lambda r: httpx.Response(500, json={})).chat_json([], {})


def test_embed_orders_by_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        assert json.loads(request.content)["model"] == "bge-m3"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    assert _client(handler).embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]


def test_list_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

    assert _client(handler).list_models() == ["qwen3:8b"]
