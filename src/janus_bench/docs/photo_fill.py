"""Photo to fill, end to end on a replica site (docs/PLAN.md "Photo to fill").

A fictional document photo is read by the vision models, a simulated careful person compares each
value with the photo and corrects the ones that are wrong (the confirm screen), and the confirmed
values drive the same run `janus ui` makes. Success is the replica's own state, never a model's
opinion. The simulated person is the point: the models misread digits, so a person always
confirms, and the number of corrections is reported next to the outcome.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from janus.config import Settings
from janus.extract_doc import extract_document
from janus.llm import LLMClient
from janus.text.fields import normalize_field
from janus.ui.documents import DocSession
from janus.ui.runner import execute
from janus.ui.session import RunSession
from janus_bench.docs.make import WANT

SHARESEWA_GOAL = (
    "Log in and apply for the NIC Asia Debenture 2083 issue, 200 kitta. "
    "My bank, CRN and BOID are on my demat slip."
)
NAGARPALIKA_GOAL = (
    "Apply for a residence recommendation. My name, citizenship number, date of birth and ward "
    "are on my citizenship certificate."
)


@dataclass
class FillResult:
    kind: str
    fields: int = 0
    agreed: int = 0
    corrections: int = 0  # fields the simulated person had to fix or fill in
    status: str = ""
    error: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    stages: list[str] = field(default_factory=list)  # trace stage names and blocking gates only


def read_and_confirm(
    llm: LLMClient, photo: bytes, kind_keys: list[str], truth: dict[str, str], settings: Settings
) -> tuple[dict[str, str], FillResult]:
    """Extract, then let a careful person correct every field to what the photo shows."""
    doc = extract_document(
        llm, photo, kind_keys, model_a=settings.vision_model, model_b=settings.vision_model_b
    )
    result = FillResult(kind="", fields=len(doc.fields))
    session = DocSession(kind="demo")
    session.fields, session.image, session.status = doc.fields, doc.image, "ready"
    values: dict[str, str] = {}
    for f in doc.fields:
        want = truth[f.key]
        result.agreed += f.status == "agreed"
        if normalize_field(f.key, want) != f.value:
            result.corrections += 1
        values[f.key] = want
    problems = session.confirm(values)
    if problems:
        raise RuntimeError(f"the simulated person's values were refused: {problems}")
    return dict(session.confirmed), result


def stage_summary(session: RunSession) -> list[str]:
    """Stage names, plus the gate and first error of a block or refused plan. These are validator
    messages (element names and input keys, never input values), so they are safe to print."""
    out = []
    for event in session.events:
        data, stage = event["data"], event["stage"]
        errors = data.get("errors") or [data.get("error")]
        if stage == "blocked":
            out.append(f"blocked:{data.get('gate')}:{str(errors[0])[:160]}")
        elif stage == "plan_attempt" and not data.get("ok"):
            out.append(f"plan_refused:{str(errors[0])[:160]}")
        else:
            out.append(stage)
    return out


def run_session(session: RunSession, settings: Settings, answers: dict[str, str]) -> None:
    """Run `session` like the web UI does, approving every consequential step (as a person
    pressing Approve would) and answering questions from `answers` (keyword -> reply)."""

    def approve_and_answer() -> None:
        while session.status not in ("completed", "partial", "blocked", "error"):
            if session.pending is not None:
                session.answer(True)
            question = session.question
            if question is not None:
                text = question.text.lower()
                reply = next((v for k, v in answers.items() if k in text), None)
                session.reply(reply)
            time.sleep(0.05)

    helper = threading.Thread(target=approve_and_answer, daemon=True)
    helper.start()
    execute(session, settings, headed=False)
    helper.join(timeout=2)


def sharesewa_case(photo: Path, truth: dict[str, str], settings: Settings, base: str) -> FillResult:
    llm = LLMClient(settings)
    try:
        confirmed, result = read_and_confirm(llm, photo.read_bytes(), WANT["boid"], truth, settings)
    finally:
        llm.close()
    result.kind = "demat -> ShareSewa apply"
    session = RunSession(
        goal=SHARESEWA_GOAL,
        url=f"{base}/login",
        username=truth["boid"],
        password="Sajilo@123",
        pin="१२३४",
        doc_inputs=confirmed,
    )
    run_session(session, settings, answers={})
    result.status, result.error = session.status, session.error
    result.stages = stage_summary(session)
    result.state = httpx.get(f"{base}/__bench/state", timeout=5).json()
    return result


def sharesewa_applied(result: FillResult, truth: dict[str, str]) -> bool:
    """A new application with the document's CRN, 200 kitta, submitted."""
    applications = result.state.get("applications", {})
    return any(
        a.get("crn") == truth["crn"] and a.get("kitta") == 200 and a.get("status") == "submitted"
        for a in applications.values()
    )


NAGARPALIKA_PHONE = "9841234567"  # not on the certificate: the simulated person gives it when asked


def nagarpalika_case(
    photo: Path, truth: dict[str, str], settings: Settings, base: str
) -> FillResult:
    """Citizenship certificate -> residence recommendation. The phone number is not on the
    document, so the form's required field makes Janus ask for it."""
    llm = LLMClient(settings)
    try:
        confirmed, result = read_and_confirm(
            llm, photo.read_bytes(), WANT["citizen"], truth, settings
        )
    finally:
        llm.close()
    result.kind = "citizenship certificate -> Nagarpalika residence recommendation"
    session = RunSession(
        goal=NAGARPALIKA_GOAL,
        url=f"{base}/services",
        doc_inputs=confirmed,
    )
    run_session(
        session, settings, answers={"mobile": NAGARPALIKA_PHONE, "phone": NAGARPALIKA_PHONE}
    )
    result.status, result.error = session.status, session.error
    result.stages = stage_summary(session)
    result.state = httpx.get(f"{base}/__bench/state", timeout=5).json()
    return result


def nagarpalika_applied(result: FillResult, truth: dict[str, str]) -> bool:
    """A new application carrying the certificate's number and the phone the person gave."""
    applications = result.state.get("applications", {})
    return any(
        a.get("citizenship_no") == truth["citizenship_no"] and a.get("phone") == NAGARPALIKA_PHONE
        for a in applications.values()
    )
