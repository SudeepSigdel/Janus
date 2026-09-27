"""Per-task policy and capability monotonicity (security invariant 2, CLAUDE.md).

A capability is (op kind, origin, form id): the minimal description of "what a plan
can do" needed to check that a replan never asks for more than what was already
committed.
"""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from janus.observer.snapshot import PageSnapshot
from janus.planner.ops import OpKind, Plan

Capability = tuple[str, str | None, str | None]


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_origins: frozenset[str]
    allowed_ops: frozenset[OpKind]
    max_steps: int
    sensitive_fields: frozenset[str] = frozenset()


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
        else:  # EXTRACT, DONE
            capabilities.add((step.op, page_origin, None))
    return frozenset(capabilities)


def is_capability_subset(new: frozenset[Capability], committed: frozenset[Capability]) -> bool:
    """Invariant 2: capabilities(new) must be a subset of capabilities(committed)."""
    return new <= committed
