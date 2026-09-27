"""Settings: model tags, local Ollama URL, limits."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ollama_base_url: str = "http://localhost:11434"
    planner_model: str = "janus-planner"
    base_model: str = "qwen3:8b"
    fallback_models: tuple[str, ...] = ("qwen2.5:7b-instruct", "qwen3:4b")
    embedding_model: str = "bge-m3"
    request_timeout_s: float = 120.0
    max_output_tokens: int = 1024

    observer_max_elements: int = 200
    observer_max_label_chars: int = 80
    observer_max_untrusted_chars: int = 4000

    @property
    def required_models(self) -> tuple[str, ...]:
        return (self.planner_model, self.base_model, self.embedding_model, *self.fallback_models)


def get_settings() -> Settings:
    return Settings()
