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

    # Plan-commit retries (validator errors fed back to the model) and mid-run
    # replans (a page transition needs a fresh plan against a new snapshot).
    max_plan_retries: int = 2
    max_replan_attempts: int = 6
    # Grounding (planner/ground.py): a bge-m3 cosine-similarity match below this is
    # not trusted; grounding falls through to the constrained-LLM tier instead.
    grounding_similarity_threshold: float = 0.75

    @property
    def required_models(self) -> tuple[str, ...]:
        return (self.planner_model, self.base_model, self.embedding_model, *self.fallback_models)


def get_settings() -> Settings:
    return Settings()
