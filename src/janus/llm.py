"""The only AI entry point: an OpenAI-compatible client for open-weight models.

Embeddings and model listing use local Ollama (loopback only). Chat may optionally use a hosted
endpoint (`Settings.chat_base_url`), but only for open-weight model families.

Output is returned as raw parsed JSON; callers must validate it (see janus/validator/).
"""

from __future__ import annotations

import base64
import ipaddress
import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from janus.config import Settings, get_settings


class NonLocalEndpointError(ValueError):
    """Raised when the configured LLM endpoint is not a loopback address."""


class ProprietaryModelError(ValueError):
    """Raised when a hosted chat model is not on the open-weight allowlist."""


# Open-weight families only (hosted chat). Extend deliberately; never add closed models.
OPEN_WEIGHT_FAMILIES = ("qwen", "gpt-oss", "llama", "gemma", "deepseek", "glm", "kimi", "phi")


def assert_open_weight(model: str) -> None:
    name = model.lower().rsplit("/", 1)[-1]
    if name.startswith("gpt-oss") or any(name.startswith(f) for f in OPEN_WEIGHT_FAMILIES):
        return
    raise ProprietaryModelError(f"refusing model not on the open-weight allowlist: {model!r}")


MAX_RATE_LIMIT_RETRIES = 8
MAX_BACKOFF_S = 65.0


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    """Seconds to wait after a 429/503: Retry-After if the host gave one, else exponential."""
    try:
        wait = float(resp.headers["retry-after"])
    except (KeyError, ValueError):
        wait = 2.0**attempt
    return min(max(wait, 1.0) + 0.5, MAX_BACKOFF_S)


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


_OCR_ENGINE: Any = None  # one engine per process: loading the models takes a second or two


def _ocr_engine() -> Any:
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        try:
            import logging

            from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
        except ImportError as exc:
            raise LLMError("OCR needs the `ui` extra (rapidocr, onnxruntime)") from exc
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        _OCR_ENGINE = RapidOCR(
            params={
                "Rec.lang_type": LangRec.DEVANAGARI,
                "Rec.model_type": ModelType.MOBILE,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
            }
        )
    return _OCR_ENGINE


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
        self._chat_http = self._http
        if self.settings.chat_base_url:
            if not self.settings.chat_model:
                raise ValueError("chat_base_url requires chat_model")
            assert_open_weight(self.settings.chat_model)
            headers = {}
            if self.settings.chat_api_key:
                headers["Authorization"] = f"Bearer {self.settings.chat_api_key}"
            self._chat_http = httpx.Client(
                base_url=self.settings.chat_base_url.rstrip("/"),
                timeout=self.settings.request_timeout_s,
                transport=transport,
                headers=headers,
            )
        # Cumulative usage/latency across every call this client has made (docs/PLAN.md
        # P1: "LLM usage/latency from LLMClient"). A caller reads these off after a run;
        # nothing here changes control flow, so it's safe with or without tracing.
        self.chat_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.elapsed_s = 0.0

    def close(self) -> None:
        self._http.close()
        if self._chat_http is not self._http:
            self._chat_http.close()

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            http = client or self._http
            resp = http.request(method, path, json=payload)
            # Hosted free tiers rate-limit (tokens/minute); wait and retry rather than fail.
            if http is not self._http:
                for attempt in range(MAX_RATE_LIMIT_RETRIES):
                    if resp.status_code not in (429, 503):
                        break
                    time.sleep(_retry_delay(resp, attempt))
                    resp = http.request(method, path, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"{path} failed: {exc}") from exc
        finally:
            self.elapsed_s += time.monotonic() - started
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
        s = self.settings
        payload: dict[str, Any] = {
            "model": model or s.chat_model or s.planner_model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": s.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema,
                    "strict": s.chat_strict_schema,
                },
            },
        }
        # Thinking off (PLAN.md): otherwise qwen3 spends the budget on reasoning.
        if s.chat_reasoning_effort:
            payload["reasoning_effort"] = s.chat_reasoning_effort
        if s.chat_base_url:
            assert_open_weight(payload["model"])
        data = self._request(
            "POST",
            "/chat/completions" if s.chat_base_url else "/v1/chat/completions",
            payload,
            self._chat_http,
        )
        self.chat_calls += 1
        usage = data.get("usage") or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        try:
            return json.loads(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"unparseable chat response: {exc}") from exc

    def chat_vision_json(
        self,
        prompt: str,
        images: list[bytes],
        schema: dict[str, Any],
        *,
        model: str | None = None,
    ) -> Any:
        """Ask the local vision model about `images`; returns parsed (unvalidated) JSON.

        Images are personal documents, so this call only ever uses the loopback Ollama client
        (`self._http`), never the hosted chat client, whatever `JANUS_CHAT_*` says."""
        name = model or self.settings.vision_model
        assert_open_weight(name)
        payload: dict[str, Any] = {
            "model": name,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0, "num_ctx": 4096},
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(image).decode("ascii") for image in images],
                }
            ],
        }
        data = self._request("POST", "/api/chat", payload, self._http)
        self.chat_calls += 1
        self.prompt_tokens += int(data.get("prompt_eval_count") or 0)
        self.completion_tokens += int(data.get("eval_count") or 0)
        try:
            return json.loads(data["message"]["content"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"unparseable vision response: {exc}") from exc

    def ocr_lines(self, image: bytes) -> list[dict[str, Any]]:
        """Text lines read off `image` by a local open-weight OCR model (PaddleOCR PP-OCRv5
        Devanagari via RapidOCR, run on this machine, no network at inference).

        Returns [{"text": str, "box": [x1, y1, x2, y2]}] in pixels, unvalidated: callers treat
        every line as untrusted text, exactly like a vision model's answer."""
        started = time.monotonic()
        try:
            result = _ocr_engine()(image)
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"OCR failed: {type(exc).__name__}") from exc
        finally:
            self.elapsed_s += time.monotonic() - started
        lines: list[dict[str, Any]] = []
        boxes = result.boxes if result.boxes is not None else ()
        for text, quad in zip(result.txts or (), boxes, strict=False):
            xs, ys = [float(pt[0]) for pt in quad], [float(pt[1]) for pt in quad]
            lines.append({"text": str(text), "box": [min(xs), min(ys), max(xs), max(ys)]})
        return lines

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
