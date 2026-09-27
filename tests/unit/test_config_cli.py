from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from janus import cli
from janus.config import Settings, get_settings
from janus.llm import LLMError


def test_defaults() -> None:
    s = get_settings()
    assert s.base_model == "qwen3:8b"
    assert s.embedding_model == "bge-m3"
    assert "janus-planner" in s.required_models


def test_settings_forbid_extra() -> None:
    with pytest.raises(ValidationError):
        Settings(api_key="x")  # type: ignore[call-arg]


def test_has_model_accepts_latest_suffix() -> None:
    assert cli.has_model(["bge-m3:latest"], "bge-m3")
    assert cli.has_model(["qwen3:8b"], "qwen3:8b")
    assert not cli.has_model(["qwen3:4b"], "qwen3:8b")


class _FakeClient:
    models: list[str] = []
    fail = False

    def __init__(self, settings=None) -> None:
        pass

    def list_models(self) -> list[str]:
        if self.fail:
            raise LLMError("down")
        return self.models

    def close(self) -> None:
        pass


def test_doctor_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.fail = True
    monkeypatch.setattr(cli, "LLMClient", _FakeClient)
    assert cli.doctor() == 1


def test_doctor_missing_and_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.fail = False
    monkeypatch.setattr(cli, "LLMClient", _FakeClient)
    _FakeClient.models = ["qwen3:8b"]
    assert cli.doctor() == 1
    _FakeClient.models = list(get_settings().required_models)
    assert cli.doctor() == 0


def test_main_dispatches_run_with_the_task_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: dict[str, Path] = {}

    def fake_run(task_path: Path) -> int:
        called["path"] = task_path
        return 0

    monkeypatch.setattr(cli, "run", fake_run)
    task_path = tmp_path / "t01.yaml"
    assert cli.main(["run", "--task", str(task_path)]) == 0
    assert called["path"] == task_path
