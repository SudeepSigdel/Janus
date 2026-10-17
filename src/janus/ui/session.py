"""One Janus run driven from the local web UI (`janus ui`).

The run itself is the same `run_task` loop as `janus run --goal`. This module only adds what a
browser front end needs: the run happens on a worker thread, its trace events are collected for
polling, and each consequential action waits for the person to press Approve or Deny instead of
typing at a terminal prompt. Nothing here authorizes anything; an unanswered approval is a denial.
"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from janus.planner.ops import Step

APPROVAL_TIMEOUT_S = 300.0
_TARGET = re.compile(r"target '(?P<label>.*?)' at (?P<url>\S+?)(?:;|$)")
_PREFILL = re.compile(r"untouched pre-filled fields: (?P<fields>.+)$")


@dataclass
class Approval:
    op: str
    label: str
    url: str
    prefilled: str
    reason: str
    decided: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


def describe(step: Step, reason: str) -> Approval:
    """Split the agent's approval reason into what the UI shows: the action, the button or link
    it targets, the page, and any pre-filled fields the run did not set."""
    target = _TARGET.search(reason)
    prefill = _PREFILL.search(reason)
    return Approval(
        op=step.op,
        label=target.group("label") if target else "",
        url=target.group("url") if target else "",
        prefilled=prefill.group("fields") if prefill else "",
        reason=reason.split(";")[0],
    )


@dataclass
class Question:
    text: str
    secret: bool
    decided: threading.Event = field(default_factory=threading.Event)
    answer: str | None = None


@dataclass
class RunSession:
    goal: str
    url: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "starting"  # starting | running | waiting | completed | partial | blocked | error
    events: list[dict[str, Any]] = field(default_factory=list)
    pending: Approval | None = None
    question: Question | None = None
    # Login details typed into the UI, never into the goal, so no model ever sees them.
    username: str = ""
    password: str = field(default="", repr=False)
    pin: str = field(default="", repr=False)
    error: str = ""
    report_html: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def trace(self, stage: str, data: dict[str, Any]) -> None:
        with self._lock:
            self.events.append({"stage": stage, "data": data})

    def escalate(self, step: Step, reason: str) -> bool:
        """Block the run until the person answers in the UI (or the timeout denies it)."""
        approval = describe(step, reason)
        with self._lock:
            self.pending = approval
            self.status = "waiting"
            self.events.append(
                {
                    "stage": "approval_requested",
                    "data": {"op": approval.op, "label": approval.label, "url": approval.url},
                }
            )
        approval.decided.wait(APPROVAL_TIMEOUT_S)
        with self._lock:
            self.pending = None
            self.status = "running"
            self.events.append(
                {
                    "stage": "approval_answered",
                    "data": {"op": approval.op, "label": approval.label, "ok": approval.approved},
                }
            )
        return approval.approved

    def ask(self, text: str, secret: bool) -> str | None:
        """Block the run until the person types an answer (a one-time code, say)."""
        question = Question(text=text, secret=secret)
        with self._lock:
            self.question = question
            self.status = "waiting"
        question.decided.wait(APPROVAL_TIMEOUT_S)
        with self._lock:
            self.question = None
            self.status = "running"
        return question.answer

    def reply(self, answer: str | None) -> bool:
        with self._lock:
            question = self.question
        if question is None:
            return False
        question.answer = answer
        question.decided.set()
        return True

    def answer(self, approved: bool) -> bool:
        with self._lock:
            pending = self.pending
        if pending is None:
            return False
        pending.approved = approved
        pending.decided.set()
        return True

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        with self._lock:
            pending = self.pending
            question = self.question
            return {
                "question": None
                if question is None
                else {"text": question.text, "secret": question.secret},
                "id": self.id,
                "status": self.status,
                "error": self.error,
                "events": self.events[since:],
                "next": len(self.events),
                "pending": None
                if pending is None
                else {
                    "op": pending.op,
                    "label": pending.label,
                    "url": pending.url,
                    "prefilled": pending.prefilled,
                    "reason": pending.reason,
                },
                "has_report": bool(self.report_html),
            }


class SessionStore:
    """At most one run at a time: there is one browser and one person approving."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sessions: dict[str, RunSession] = {}
        self.active: RunSession | None = None

    def busy(self) -> bool:
        with self._lock:
            return self.active is not None and self.active.status in (
                "starting",
                "running",
                "waiting",
            )

    def add(self, session: RunSession) -> None:
        with self._lock:
            self.sessions[session.id] = session
            self.active = session

    def get(self, session_id: str) -> RunSession | None:
        with self._lock:
            return self.sessions.get(session_id)
