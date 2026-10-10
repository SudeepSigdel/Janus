"""Hermetic settings: a developer's .env / JANUS_CHAT_* must not leak into tests."""

from __future__ import annotations

import pytest

import janus.config


@pytest.fixture(autouse=True)
def _isolate_chat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "JANUS_CHAT_BASE_URL",
        "JANUS_CHAT_MODEL",
        "JANUS_CHAT_API_KEY",
        "JANUS_CHAT_REASONING_EFFORT",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(janus.config, "_load_dotenv", lambda path: {})
