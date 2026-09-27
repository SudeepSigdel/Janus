"""Deterministic consequential-action detection (security invariant 4, CLAUDE.md).

Two independent triggers, either is sufficient:
  1. Form-submit semantics: a SUBMIT step is always consequential.
  2. A keyword match (English/Nepali/Hindi) against the target element's accessible name.

This is intentionally simple substring matching, not NLP: it must be predictable and
auditable, since it gates escalation (validator/action.py). A model may only add to
this via `Step.consequential_hint` (an OR, never overriding it to False) -- see
validator/plan.py.
"""

from __future__ import annotations

from janus.planner.ops import OpKind
from janus.text.nepali import normalize_text

_KEYWORDS_EN = {
    "submit",
    "confirm",
    "cancel",
    "delete",
    "remove",
    "pay",
    "payment",
    "transfer",
    "approve",
    "send",
}

_KEYWORDS_NE = {
    "पेश",  # submit
    "रद्द",  # cancel / void
    "मेटाउ",  # delete (stem: मेटाउनुहोस्)
    "पुष्टि",  # confirm
    "भुक्तानी",  # payment
    "स्वीकृत",  # approve
    "पठाउ",  # send (stem: पठाउनुहोस्)
}

_KEYWORDS_HI = {
    "सबमिट",  # submit
    "जमा करें",  # submit/deposit
    "रद्द करें",  # cancel
    "मिटाएं",  # delete
    "हटाएं",  # remove
    "पुष्टि करें",  # confirm
    "भुगतान",  # payment
    "स्वीकृत करें",  # approve
    "भेजें",  # send
}

_ALL_KEYWORDS = _KEYWORDS_EN | _KEYWORDS_NE | _KEYWORDS_HI


def matches_consequential_keyword(accessible_name: str) -> bool:
    normalized = normalize_text(accessible_name)
    return any(keyword in normalized for keyword in _ALL_KEYWORDS)


def is_consequential(op: OpKind, accessible_name: str) -> bool:
    """Deterministic classification. Never call this to downgrade a model's hint."""
    if op == "SUBMIT":
        return True
    return matches_consequential_keyword(accessible_name)
