"""Per-task policy and capability monotonicity (security invariant 2, CLAUDE.md).

A capability is (op kind, origin, form id): the minimal description of "what a plan
can do" needed to check that a replan never asks for more than what was already
committed.
"""

from __future__ import annotations

from typing import Literal, get_args
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from janus.observer.snapshot import PageSnapshot
from janus.planner.ops import OpKind, Plan

Capability = tuple[str, str | None, str | None]

# Benchmark oracle endpoints (security invariant 7, docs/AUDIT.md S7): never reachable
# by a plan or by the page, even on an allowed origin.
DENIED_PATH_PREFIXES: tuple[str, ...] = ("/__bench",)


def is_denied_path(path: str, prefixes: tuple[str, ...] = DENIED_PATH_PREFIXES) -> bool:
    path = path or "/"
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def allowed_ops_for(
    allow_navigate: bool, allow_read: bool = False, allow_ask: bool = False
) -> frozenset[OpKind]:
    """Every op kind, minus `NAVIGATE` unless a task explicitly opts in (docs/PLAN.md
    P3): the runtime already opens `start_url` itself, and no dev task needs a
    mid-task NAVIGATE. Dropping it from the caller's `Policy.allowed_ops` also drops
    it from the planner's decoding schema (`planner/plan.py::_plan_schema`), so the
    model cannot even emit the op, not just have it rejected after the fact."""
    ops = set(get_args(OpKind))
    if not allow_navigate:
        ops.discard("NAVIGATE")
    if not allow_read:
        ops.discard("READ")
    if not allow_ask:
        ops.discard("ASK_USER")
    return frozenset(ops)


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_origins: frozenset[str]
    allowed_ops: frozenset[OpKind]
    max_steps: int
    sensitive_fields: frozenset[str] = frozenset()
    denied_path_prefixes: tuple[str, ...] = DENIED_PATH_PREFIXES
    # Optional text fields that open pre-filled and that the run never touched
    # (docs/LIMITATIONS.md, Q5). "allow" ignores them; "flag" discloses them to the human at
    # the approval prompt and in the trace; "clear" also empties them before a consequential
    # click (for unattended runs, where there is no human to ask).
    prefill: Literal["allow", "flag", "clear"] = "allow"
    # Input keys holding the user's secrets (a password typed into the UI or at a prompt,
    # never in the goal text, so never seen by a model). A secret may only be typed into a
    # password field, and a password field only takes a secret: a page cannot get the
    # password typed into a search box, and the model cannot put a guess in a password box.
    secret_inputs: frozenset[str] = frozenset()


def origin_of(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _element_form_id(snapshot: PageSnapshot, ref: str) -> str | None:
    for element in snapshot.elements:
        if element.ref == ref:
            return element.form_id
    return None


def capabilities_of(plan: Plan, snapshot: PageSnapshot) -> frozenset[Capability]:
    """The set of (op, origin, form) `plan` touches, evaluated against `snapshot`.

    `snapshot` is whatever page is live when the plan is committed or replanned;
    steps that act on an element (FILL_FORM/SELECT/CLICK/SUBMIT) are attributed to
    that page's origin and the target element's form.
    """
    page_origin = origin_of(snapshot.url)
    capabilities: set[Capability] = set()
    for step in plan.steps:
        if step.op == "NAVIGATE":
            capabilities.add((step.op, origin_of(step.url), None))
        elif step.op == "FILL_FORM":
            for field_value in step.fields:
                capabilities.add(
                    (step.op, page_origin, _element_form_id(snapshot, field_value.ref))
                )
        elif step.op in ("SELECT", "CLICK", "SUBMIT"):
            capabilities.add((step.op, page_origin, _element_form_id(snapshot, step.ref)))
        elif step.op in ("DONE", "ASK_USER"):
            # B4b: DONE ends the run and touches nothing, so it grants no capability;
            # counting it let a ceiling committed without DONE refuse a finished replan.
            # ASK_USER is the same: the human answers, nothing on a page is touched, and the
            # answer only reaches a page through later steps that are validated on their own.
            continue
        else:  # EXTRACT
            capabilities.add((step.op, page_origin, None))
    return frozenset(capabilities)


def is_capability_subset(new: frozenset[Capability], committed: frozenset[Capability]) -> bool:
    """Invariant 2: capabilities(new) must be a subset of capabilities(committed)."""
    return new <= committed
