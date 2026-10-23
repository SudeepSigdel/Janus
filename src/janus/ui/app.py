"""`janus ui`: a local web page for giving Janus a goal, watching it work and approving actions.

It listens on 127.0.0.1 only. One run at a time. Needs the `ui` extra (FastAPI, uvicorn).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from janus import sessions
from janus.config import Settings, get_settings
from janus.llm import LLMClient
from janus.ui.documents import (
    DOCUMENT_KINDS,
    MAX_PHOTO_BYTES,
    DocSession,
    DocumentStore,
    read_document,
)
from janus.ui.runner import execute, model_problem
from janus.ui.session import RunSession, SessionStore

PAGE = (Path(__file__).parent / "page.html").read_text(encoding="utf-8")
DEFAULT_URL = "http://127.0.0.1:8101/services"


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=3, max_length=1000)
    url: str = Field(min_length=8, max_length=500, pattern=r"^https?://")
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=200, repr=False)
    pin: str = Field(default="", max_length=50, repr=False)
    remember: bool = False
    # A confirmed document (see /api/documents); its values become task inputs, once.
    document_id: str | None = Field(default=None, max_length=40)


class ReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str | None = Field(default=None, max_length=200)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, str] = Field(max_length=12)


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool


def create_app(
    settings: Settings | None = None, headed: bool = True, runner=execute, reader=read_document
) -> FastAPI:
    app = FastAPI(title="Janus")
    store = SessionStore()
    docs = DocumentStore()
    cfg = settings or get_settings()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE.replace("__DEFAULT_URL__", DEFAULT_URL)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        llm = LLMClient(cfg)
        try:
            return {"problem": model_problem(llm, cfg)}
        finally:
            llm.close()

    @app.post("/api/runs")
    def start(body: StartRequest) -> dict[str, str]:
        if store.busy():
            raise HTTPException(409, "a run is already in progress")
        doc_inputs: dict[str, str] = {}
        if body.document_id:
            confirmed = docs.take_confirmed(body.document_id)
            if confirmed is None:
                raise HTTPException(409, "confirm the document first")
            doc_inputs = confirmed
        session = RunSession(
            goal=body.goal.strip(),
            url=body.url.strip(),
            username=body.username.strip(),
            password=body.password,
            pin=body.pin,
            remember=body.remember,
            doc_inputs=doc_inputs,
        )
        store.add(session)
        threading.Thread(target=runner, args=(session, cfg, headed), daemon=True).start()
        return {"id": session.id}

    @app.get("/api/document-kinds")
    def document_kinds() -> dict[str, str]:
        return {kind: spec["label"] for kind, spec in DOCUMENT_KINDS.items()}

    @app.post("/api/documents")
    async def upload_document(
        kind: Annotated[str, Form()], file: Annotated[UploadFile, File()]
    ) -> dict[str, str]:
        if kind not in DOCUMENT_KINDS:
            raise HTTPException(422, "unknown document kind")
        photo = await file.read(MAX_PHOTO_BYTES + 1)
        if not photo or len(photo) > MAX_PHOTO_BYTES:
            raise HTTPException(413, "the photo is empty or larger than 12 MB")
        doc = DocSession(kind=kind)
        docs.add(doc)
        threading.Thread(target=reader, args=(doc, photo, cfg), daemon=True).start()
        return {"id": doc.id}

    @app.get("/api/documents/{doc_id}")
    def document(doc_id: str) -> dict:
        doc = docs.get(doc_id)
        if doc is None:
            raise HTTPException(404, "no such document")
        return doc.public()

    @app.get("/api/documents/{doc_id}/image")
    def document_image(doc_id: str) -> Response:
        doc = docs.get(doc_id)
        if doc is None or not doc.image:
            raise HTTPException(404, "no such document")
        return Response(doc.image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/documents/{doc_id}/confirm")
    def confirm_document(doc_id: str, body: ConfirmRequest) -> dict:
        doc = docs.get(doc_id)
        if doc is None:
            raise HTTPException(404, "no such document")
        problems = doc.confirm(body.values)
        if problems:
            raise HTTPException(422, {"problems": problems})
        return {"ok": True, "keys": sorted(doc.confirmed), "values": dict(doc.confirmed)}

    @app.delete("/api/documents/{doc_id}")
    def delete_document(doc_id: str) -> dict[str, bool]:
        return {"deleted": docs.delete(doc_id)}

    @app.get("/api/runs/{run_id}")
    def poll(run_id: str, since: int = 0) -> dict:
        session = store.get(run_id)
        if session is None:
            raise HTTPException(404, "no such run")
        return session.snapshot(max(0, since))

    @app.post("/api/runs/{run_id}/answer")
    def answer(run_id: str, body: AnswerRequest) -> dict[str, bool]:
        session = store.get(run_id)
        if session is None:
            raise HTTPException(404, "no such run")
        if not session.answer(body.approved):
            raise HTTPException(409, "nothing is waiting for approval")
        return {"ok": True}

    @app.post("/api/runs/{run_id}/reply")
    def reply(run_id: str, body: ReplyRequest) -> dict[str, bool]:
        session = store.get(run_id)
        if session is None:
            raise HTTPException(404, "no such run")
        if not session.reply(body.answer):
            raise HTTPException(409, "nothing is waiting for an answer")
        return {"ok": True}

    @app.post("/api/sessions/forget")
    def forget(body: StartRequest) -> dict[str, bool]:
        return {"forgotten": sessions.forget(body.url)}

    @app.get("/runs/{run_id}/report", response_class=HTMLResponse)
    def report(run_id: str) -> str:
        session = store.get(run_id)
        if session is None or not session.report_html:
            raise HTTPException(404, "no report yet")
        return session.report_html

    return app


def serve(port: int = 8300, headed: bool = True) -> None:
    import uvicorn

    print(f"Janus UI: http://127.0.0.1:{port}")
    uvicorn.run(create_app(headed=headed), host="127.0.0.1", port=port, log_level="warning")
