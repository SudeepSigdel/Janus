"""Settings: model tags, local Ollama URL, optional hosted open-weight chat endpoint, limits."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ollama_base_url: str = "http://localhost:11434"
    planner_model: str = "janus-planner"
    base_model: str = "qwen3:8b"
    fallback_models: tuple[str, ...] = ("qwen2.5:7b-instruct", "qwen3:4b")
    embedding_model: str = "bge-m3"
    # Optional hosted OpenAI-compatible endpoint for chat only (open-weight models only,
    # enforced in llm.assert_open_weight). Embeddings always stay on local Ollama.
    chat_base_url: str | None = None
    chat_model: str | None = None
    chat_api_key: str | None = Field(default=None, repr=False)
    # "none" suits qwen3 on Ollama; hosted gpt-oss needs low|medium|high. None omits the field.
    chat_reasoning_effort: str | None = "none"
    # Some hosts' strict mode demands every property in `required`; Janus's plan schema
    # has optional fields. Output is re-validated by pydantic/validator either way.
    chat_strict_schema: bool = True
    request_timeout_s: float = 120.0
    max_output_tokens: int = 1024

    observer_max_elements: int = 200
    observer_max_label_chars: int = 80
    observer_max_untrusted_chars: int = 4000

    # Plan-commit retries (validator errors fed back to the model) and mid-run
    # replans (a page transition needs a fresh plan against a new snapshot).
    max_plan_retries: int = 2
    max_replan_attempts: int = 6
    # Grounding (planner/ground.py): a bge-m3 cosine-similarity match below this is
    # not trusted; grounding falls through to the constrained-LLM tier instead.
    grounding_similarity_threshold: float = 0.75

    @property
    def required_models(self) -> tuple[str, ...]:
        if self.chat_base_url:
            return (self.embedding_model,)
        return (self.planner_model, self.base_model, self.embedding_model, *self.fallback_models)


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines of a .env file (no export, no interpolation)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip("\"'")
    return values


def get_settings() -> Settings:
    """Defaults, plus JANUS_CHAT_{BASE_URL,MODEL,API_KEY,REASONING_EFFORT} from the environment."""
    env = {**_load_dotenv(Path.cwd() / ".env"), **os.environ}  # real env wins over .env
    overrides: dict[str, str | None] = {}
    for field, var in (
        ("chat_base_url", "JANUS_CHAT_BASE_URL"),
        ("chat_model", "JANUS_CHAT_MODEL"),
        ("chat_api_key", "JANUS_CHAT_API_KEY"),
    ):
        if env.get(var):
            overrides[field] = env[var]
    if "JANUS_CHAT_REASONING_EFFORT" in env:
        overrides["chat_reasoning_effort"] = env["JANUS_CHAT_REASONING_EFFORT"] or None
    if "JANUS_CHAT_STRICT" in env:
        return Settings(
            **overrides, chat_strict_schema=env["JANUS_CHAT_STRICT"] not in ("0", "false")
        )
    return Settings(**overrides)
