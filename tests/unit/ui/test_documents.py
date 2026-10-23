"""Photo to fill in the web UI: upload, read, confirm, use once. No browser and no model."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from janus.config import Settings
from janus.extract_doc import DocField
from janus.ui import documents, runner
from janus.ui.documents import DocSession, DocumentStore
from janus.ui.session import RunSession

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from janus.ui.app import create_app  # noqa: E402

BOID = "७५५१३१३७"
IMAGE = b"\xff\xd8fake-jpeg"


def fake_reader(doc: DocSession, photo: bytes, settings: Settings) -> None:
    doc.fields = [
        DocField(key="boid", value=BOID, status="agreed", box=(0.1, 0.2, 0.5, 0.3)),
        DocField(key="crn", value="CRN-353799", status="check", box=None),
        DocField(key="bank", value=None, status="missing", box=None),
        DocField(key="name", value=None, status="missing", box=None),
    ]
    doc.image, doc.width, doc.height, doc.status = IMAGE, 1000, 600, "ready"


def _recording_runner(seen: list[dict[str, str]]):
    def run(session, settings, headed) -> None:
        seen.append(dict(session.doc_inputs))
        session.status, session.report_html = "completed", "<html></html>"

    return run


def _upload(client: TestClient, kind: str = "demat") -> str:
    reply = client.post(
        "/api/documents", data={"kind": kind}, files={"file": ("slip.jpg", b"not read here")}
    )
    assert reply.status_code == 200
    doc_id = reply.json()["id"]
    for _ in range(100):
        if client.get(f"/api/documents/{doc_id}").json()["status"] != "reading":
            break
        time.sleep(0.01)
    return doc_id


def _client(seen: list | None = None) -> TestClient:
    return TestClient(
        create_app(
            Settings(),
            headed=False,
            runner=_recording_runner(seen if seen is not None else []),
            reader=fake_reader,
        )
    )


def test_a_read_document_shows_labelled_candidates_and_the_photo() -> None:
    client = _client()
    doc_id = _upload(client)
    state = client.get(f"/api/documents/{doc_id}").json()
    assert state["status"] == "ready"
    boid = next(f for f in state["fields"] if f["key"] == "boid")
    assert boid["status"] == "agreed" and boid["value"] == BOID and "BOID" in boid["label"]
    image = client.get(f"/api/documents/{doc_id}/image")
    assert image.content == IMAGE and image.headers["cache-control"] == "no-store"


def test_unknown_kind_and_empty_upload_are_refused() -> None:
    client = _client()
    assert (
        client.post("/api/documents", data={"kind": "passport"}, files={"file": ("a", b"x")})
    ).status_code == 422
    assert (
        client.post("/api/documents", data={"kind": "demat"}, files={"file": ("a", b"")})
    ).status_code == 413


def test_confirming_nothing_is_refused() -> None:
    client = _client()
    doc_id = _upload(client)
    for values in ({}, {"boid": "  ", "crn": ""}):
        reply = client.post(f"/api/documents/{doc_id}/confirm", json={"values": values})
        assert reply.status_code == 422


def test_confirmed_values_are_checked_again_and_unknown_fields_refused() -> None:
    client = _client()
    doc_id = _upload(client)
    reply = client.post(
        f"/api/documents/{doc_id}/confirm",
        json={"values": {"boid": "123", "phone": "9841234567"}},
    )
    assert reply.status_code == 422
    problems = reply.json()["detail"]["problems"]
    assert "boid" in problems and "phone" in problems  # bad format; not a field of a demat slip


def test_unconfirmed_values_never_reach_a_run() -> None:
    seen: list[dict[str, str]] = []
    client = _client(seen)
    doc_id = _upload(client)
    reply = client.post(
        "/api/runs",
        json={"goal": "Apply", "url": "http://127.0.0.1:8102/login", "document_id": doc_id},
    )
    assert reply.status_code == 409 and seen == []


def test_confirmed_values_reach_the_run_once_in_canonical_form() -> None:
    seen: list[dict[str, str]] = []
    client = _client(seen)
    doc_id = _upload(client)
    reply = client.post(
        f"/api/documents/{doc_id}/confirm",
        json={"values": {"boid": "75513137", "crn": "353799", "bank": ""}},
    )
    assert reply.json() == {
        "ok": True,
        "keys": ["boid", "crn"],
        "values": {"boid": BOID, "crn": "CRN-353799"},
    }
    run = client.post(
        "/api/runs",
        json={"goal": "Apply", "url": "http://127.0.0.1:8102/login", "document_id": doc_id},
    )
    assert run.status_code == 200
    for _ in range(100):
        if seen:
            break
        time.sleep(0.01)
    assert seen == [{"boid": BOID, "crn": "CRN-353799"}]
    # single use: the document and its values are gone
    assert client.get(f"/api/documents/{doc_id}").status_code == 404
    again = client.post(
        "/api/runs",
        json={"goal": "Apply", "url": "http://127.0.0.1:8102/login", "document_id": doc_id},
    )
    assert again.status_code == 409


def test_deleting_a_document_wipes_it() -> None:
    client = _client()
    doc_id = _upload(client)
    assert client.delete(f"/api/documents/{doc_id}").json() == {"deleted": True}
    assert client.get(f"/api/documents/{doc_id}/image").status_code == 404
    assert client.delete(f"/api/documents/{doc_id}").json() == {"deleted": False}


def test_old_documents_expire_and_are_wiped(monkeypatch: pytest.MonkeyPatch) -> None:
    store = DocumentStore()
    old = DocSession(kind="demat")
    old.image = IMAGE
    store.add(old)
    monkeypatch.setattr(documents, "TTL_S", -1)
    assert store.get(old.id) is None
    assert old.image == b""


def test_the_store_keeps_at_most_a_few_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    store = DocumentStore()
    docs = [DocSession(kind="demat") for _ in range(documents.MAX_DOCUMENTS + 2)]
    for doc in docs:
        doc.image = IMAGE
        store.add(doc)
    assert store.get(docs[0].id) is None and docs[0].image == b""
    assert store.get(docs[-1].id) is docs[-1]


def test_the_run_traces_only_the_keys_of_document_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """The photo's values are personal data: not in the trace, the report or the session after."""
    captured: dict[str, dict[str, str]] = {}

    class FakeLLM:
        def __init__(self, settings) -> None: ...
        def list_models(self) -> list[str]:
            return []

        def close(self) -> None: ...

    class FakePlaywright:
        def __enter__(self):
            chromium = SimpleNamespace(launch=lambda headless: SimpleNamespace(close=lambda: None))
            return SimpleNamespace(chromium=chromium)

        def __exit__(self, *exc) -> None: ...

    def fake_run_task(page, **kwargs):
        captured["inputs"] = dict(kwargs["inputs"])
        kwargs["trace"]("snapshot", {"url": "http://x", "title": "Apply", "elements": 3})
        return SimpleNamespace(status="completed", steps_run=1)

    monkeypatch.setattr(runner, "LLMClient", FakeLLM)
    monkeypatch.setattr(runner, "extract_inputs", lambda goal, llm: {"bank": "nabil"})
    monkeypatch.setattr(runner, "sync_playwright", lambda: FakePlaywright())
    monkeypatch.setattr(
        runner.sessions, "open_context", lambda *a: SimpleNamespace(new_page=lambda: object())
    )
    monkeypatch.setattr(runner, "run_task", fake_run_task)

    session = RunSession(goal="Apply", url="http://127.0.0.1:8102/login")
    session.doc_inputs = {"boid": BOID, "crn": "CRN-353799"}
    settings = Settings(chat_base_url="https://example.invalid/v1", chat_model="qwen/qwen3-32b")
    runner.execute(session, settings, headed=False)

    assert session.status == "completed"
    assert captured["inputs"]["boid"] == BOID  # the run used them
    events = json.dumps(session.events, ensure_ascii=False)
    assert BOID not in events and "CRN-353799" not in events
    assert BOID not in session.report_html and "CRN-353799" not in session.report_html
    doc_event = next(e for e in session.events if e["stage"] == "doc_inputs")
    assert doc_event["data"] == {"keys": ["boid", "crn"]}
    assert session.doc_inputs == {}
