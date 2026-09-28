"""Deterministic element fingerprints (role, accessible name, name attribute, form id,
tag, row key).

Fingerprinting only; re-resolving a fingerprint against a live page is M4's job.
"""

from __future__ import annotations

from janus.observer.snapshot import Fingerprint, Role


def compute_fingerprint(
    *,
    role: Role,
    accessible_name: str,
    name_attr: str | None,
    form_id: str | None,
    tag: str,
    row_key: str | None = None,
) -> Fingerprint:
    return Fingerprint(
        role=role,
        accessible_name=accessible_name,
        name_attr=name_attr,
        form_id=form_id,
        tag=tag,
        row_key=row_key,
    )
