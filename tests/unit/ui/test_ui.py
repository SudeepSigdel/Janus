"""The web UI's session and HTTP API, with a fake runner (no browser, no model)."""

from __future__ import annotations

import threading
import time

import pytest

from janus.planner.ops import ClickStep
from janus.ui.session import RunSession, describe

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from janus.ui.app import create_app  # noqa: E402

REASON = (
    "CLICK is consequential and matches no remaining approval; target 'Submit application' at "
    "http://127.0.0.1:8101/review; untouched pre-filled fields: Alternate mobile='9800000000'"
)


def test_describe_splits_the_agent_reason_for_display() -> None:
    a = describe(ClickStep(ref="e3"), REASON)
    assert a.op == "CLICK" and a.label == "Submit application"
    assert a.url == "http://127.0.0.1:8101/review"
    assert a.prefilled == "Alternate mobile='9800000000'"
    assert a.reason.startswith("CLICK is consequential")


def test_escalate_waits_for_the_answer() -> None:
    session = RunSession(goal="g", url="http://x")
    result: list[bool] = []
    worker = threading.Thread(
        target=lambda: result.append(session.escalate(ClickStep(ref="e3"), REASON))
    )
    worker.start()
    for _ in range(100):
        if session.snapshot()["pending"]:
            break
        time.sleep(0.01)
    assert session.snapshot()["status"] == "waiting"
    assert session.answer(True)
    worker.join(2)
    assert result == [True]
    assert session.snapshot()["pending"] is None
    assert not session.answer(True)  # nothing is waiting any more


def test_unanswered_approval_is_a_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janus.ui.session.APPROVAL_TIMEOUT_S", 0.05)
    session = RunSession(goal="g", url="http://x")
    assert session.escalate(ClickStep(ref="e3"), REASON) is False


def _fake_runner(session, settings, headed) -> None:
    session.trace("snapshot", {"url": session.url, "title": "Services", "elements": 3})
    approved = session.escalate(ClickStep(ref="e3"), REASON)
    session.status = "completed" if approved else "blocked"
    session.report_html = "<html>report</html>"


def test_api_runs_a_goal_and_takes_an_approval() -> None:
    client = TestClient(create_app(headed=False, runner=_fake_runner))
    assert "JANUS" in client.get("/").text
    run_id = client.post(
        "/api/runs", json={"goal": "Submit it", "url": "http://127.0.0.1:8101/services"}
    ).json()["id"]
    for _ in range(100):
        state = client.get(f"/api/runs/{run_id}").json()
        if state["pending"]:
            break
        time.sleep(0.01)
    assert state["pending"]["label"] == "Submit application"
    # a second run cannot start while one is waiting
    busy = client.post("/api/runs", json={"goal": "Other", "url": "http://127.0.0.1:8101/x"})
    assert busy.status_code == 409
    assert client.post(f"/api/runs/{run_id}/answer", json={"approved": True}).json() == {"ok": True}
    for _ in range(100):
        state = client.get(f"/api/runs/{run_id}").json()
        if state["has_report"]:
            break
        time.sleep(0.01)
    assert state["status"] == "completed"
    assert client.get(f"/runs/{run_id}/report").text == "<html>report</html>"


def test_api_rejects_bad_input() -> None:
    client = TestClient(create_app(headed=False, runner=_fake_runner))
    assert client.post("/api/runs", json={"goal": "x", "url": "ftp://a"}).status_code == 422
    assert client.get("/api/runs/nope").status_code == 404


def test_model_problem_explains_a_missing_server_and_model() -> None:
    from janus.config import Settings
    from janus.llm import LLMError
    from janus.ui.runner import model_problem

    class Down:
        def list_models(self) -> list[str]:
            raise LLMError("refused")

    class Missing:
        def list_models(self) -> list[str]:
            return ["bge-m3:latest"]

    class Ready:
        def list_models(self) -> list[str]:
            return ["janus-planner:latest"]

    settings = Settings().model_copy(update={"chat_base_url": None})
    assert "not reachable" in model_problem(Down(), settings)  # type: ignore[arg-type]
    assert "not installed" in model_problem(Missing(), settings)  # type: ignore[arg-type]
    assert model_problem(Ready(), settings) == ""  # type: ignore[arg-type]
