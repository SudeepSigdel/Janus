"""Act-time fingerprint re-resolution (security invariant 5, CLAUDE.md/docs/PLAN.md).

A step's target was identified on a snapshot that may be stale by the time the
executor gets to act on it. `resolve_element` re-finds the element on the *live*
page by recomputing fingerprints fresh -- never by trusting the ref's index alone --
and raises if the fingerprint the step was authorized against no longer appears
anywhere on the page. The caller (executor.py) turns that into a blocked step.
"""

from __future__ import annotations

import re

from playwright.sync_api import ElementHandle, Page

from janus.observer.extract import extract_raw_elements, handle_at_index
from janus.observer.fingerprint import compute_fingerprint
from janus.observer.snapshot import Element, Fingerprint

_REF_INDEX = re.compile(r"^e(\d+)$")


class ResolutionError(RuntimeError):
    """The element a step targets could not be re-found on the live page."""


def _ref_index(ref: str) -> int | None:
    match = _REF_INDEX.match(ref)
    return int(match.group(1)) if match else None


def _fingerprint_of(raw: dict[str, object]) -> Fingerprint:
    return compute_fingerprint(
        role=raw["role"],  # type: ignore[arg-type]
        accessible_name=raw["accessible_name"],  # type: ignore[arg-type]
        name_attr=raw["name_attr"],  # type: ignore[arg-type]
        form_id=raw["form_id"],  # type: ignore[arg-type]
        tag=raw["tag"],  # type: ignore[arg-type]
    )


def resolve_element(page: Page, element: Element) -> ElementHandle:
    """Re-find `element` on `page` by fingerprint, immediately before acting.

    Checks the element's original position first (the common case: the page hasn't
    changed), then falls back to a full scan so a page that merely reordered its
    elements still resolves. Raises `ResolutionError` if the fingerprint is genuinely
    gone -- a step must never act on a guess.
    """
    raw_elements = extract_raw_elements(page)
    fingerprints = [_fingerprint_of(raw) for raw in raw_elements]

    expected_index = _ref_index(element.ref)
    if expected_index is not None and expected_index < len(fingerprints):
        if fingerprints[expected_index] == element.fingerprint:
            handle = handle_at_index(page, expected_index)
            if handle is not None:
                return handle

    for index, fingerprint in enumerate(fingerprints):
        if fingerprint == element.fingerprint:
            handle = handle_at_index(page, index)
            if handle is not None:
                return handle

    raise ResolutionError(
        f"element {element.ref!r} (fingerprint {element.fingerprint!r}) "
        "was not found on the live page"
    )
