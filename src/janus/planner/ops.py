"""Closed op vocabulary for plans (security invariants 1 & 3, CLAUDE.md).

Every step is `extra="forbid"`; the op vocabulary is closed via a `Literal`
discriminant, so an unrecognized op or an extra field fails at parse time --
before any validator logic runs.

Field values are plain strings: either a literal, or `$inputs.<key>` which binds
to a value the task declared rather than text the model invented. Sensitive
fields must use the latter (validator/policy.py's `sensitive_fields`, checked by
validator/plan.py).
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

OpKind = Literal["NAVIGATE", "FILL_FORM", "SELECT", "CLICK", "SUBMIT", "EXTRACT", "DONE"]

_INPUT_REF = re.compile(r"^\$inputs\.([A-Za-z_][A-Za-z0-9_]*)$")


def input_ref_key(value: str) -> str | None:
    """Return the `$inputs.<key>` key if `value` is a reference, else None (a literal)."""
    match = _INPUT_REF.match(value)
    return match.group(1) if match else None


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldValue(_Model):
    ref: str
    value: str


class _Step(_Model):
    # A model may set this to upgrade a step to consequential; it can never
    # downgrade one the deterministic classifier already flagged (validator/consequential.py).
    consequential_hint: bool = False


class NavigateStep(_Step):
    op: Literal["NAVIGATE"] = "NAVIGATE"
    url: str


class FillFormStep(_Step):
    op: Literal["FILL_FORM"] = "FILL_FORM"
    fields: list[FieldValue]


class SelectStep(_Step):
    op: Literal["SELECT"] = "SELECT"
    ref: str
    value: str


class ClickStep(_Step):
    op: Literal["CLICK"] = "CLICK"
    ref: str


class SubmitStep(_Step):
    op: Literal["SUBMIT"] = "SUBMIT"
    ref: str


class ExtractStep(_Step):
    op: Literal["EXTRACT"] = "EXTRACT"
    field: str


class DoneStep(_Step):
    op: Literal["DONE"] = "DONE"
    status: Literal["completed", "blocked", "partial"]
    message: str = ""


Step = Annotated[
    NavigateStep | FillFormStep | SelectStep | ClickStep | SubmitStep | ExtractStep | DoneStep,
    Field(discriminator="op"),
]


class Plan(_Model):
    task_id: str
    steps: list[Step]
