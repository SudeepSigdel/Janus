"""The only AI entry point: a local Ollama OpenAI-compatible client.

Output is returned as raw parsed JSON; callers must validate it (see janus/validator/).
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any
from urllib.parse import urlparse

import httpx

from janus.config import Settings, get_settings


class NonLocalEndpointError(ValueError):
    """Raised when the configured LLM endpoint is not a loopback address."""


class LLMError(RuntimeError):
    """Raised for transport or response-shape failures."""


def assert_local_url(url: str) -> None:
    host = urlparse(url).hostname
    if host is None:
        raise NonLocalEndpointError(f"no host in URL: {url!r}")
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise NonLocalEndpointError(f"refusing non-local LLM endpoint: {host}")


class LLMClient:
    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        assert_local_url(self.settings.ollama_base_url)
        self._http = httpx.Client(
            base_url=self.settings.ollama_base_url,
            timeout=self.settings.request_timeout_s,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        try:
            resp = self._http.request(method, path, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"{path} failed: {exc}") from exc
        if not isinstance(data, dict):
            raise LLMError(f"{path} returned non-object JSON")
        return data

    def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        *,
        model: str | None = None,
        schema_name: str = "response",
    ) -> Any:
        """Chat with a json_schema response_format; returns parsed (unvalidated) JSON."""
        data = self._request(
            "POST",
            "/v1/chat/completions",
            {
                "model": model or self.settings.planner_model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": self.settings.max_output_tokens,
                # Thinking off (PLAN.md): otherwise qwen3 spends the budget on reasoning.
                "reasoning_effort": "none",
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                },
            },
        )
        try:
            return json.loads(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"unparseable chat response: {exc}") from exc

    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        data = self._request(
            "POST",
            "/v1/embeddings",
            {"model": model or self.settings.embedding_model, "input": texts},
        )
        try:
            items = sorted(data["data"], key=lambda d: d["index"])
            return [[float(x) for x in item["embedding"]] for item in items]
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError(f"unparseable embeddings response: {exc}") from exc

    def list_models(self) -> list[str]:
        """Model tags known to the local server (native /api/tags)."""
        data = self._request("GET", "/api/tags", None)
        try:
            return [m["name"] for m in data["models"]]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"unparseable /api/tags: {exc}") from exc
