"""Trusted structure vs. untrusted body text.

See CLAUDE.md: page text is data, never instructions.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

Role = str  # link, button, textbox, combobox, checkbox, radio, generic


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fingerprint(_Model):
    """Identity of an element independent of its transient DOM position.

    Used later (M4) to re-resolve the target immediately before acting; a mismatch
    at act-time blocks the step (security invariant 5).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Role
    accessible_name: str
    name_attr: str | None
    form_id: str | None
    tag: str


class Element(_Model):
    """One interactive element. All fields are trusted, short, structural data."""

    ref: str
    tag: str
    role: Role
    accessible_name: str
    name_attr: str | None
    form_id: str | None
    fingerprint: Fingerprint


class SelectOption(_Model):
    """One `<option>` of a `<select>` element.

    Not part of `PageSnapshot` -- options aren't enumerated in the bounded snapshot
    the planner sees (they'd blow the element/label caps on a long list). Read live,
    only for a `<select>` a committed plan actually targets (planner/ground.py).
    """

    value: str
    label: str


class PageSnapshot(_Model):
    """A bounded observation of one page.

    `elements` is the trusted structural outline (roles + short labels) the planner
    may see directly. `untrusted_text` holds arbitrary page body text (notices,
    paragraphs, echoed values) which the planner never sees; only schema-constrained
    extraction/grounding calls may read it, and it can never add operations.
    """

    url: str
    title: str
    elements: list[Element]
    untrusted_text: list[str]
    elements_truncated: bool = False
    untrusted_text_truncated: bool = False
