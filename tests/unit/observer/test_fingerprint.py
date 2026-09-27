"""Fingerprints are deterministic and sensitive to each identifying field."""

from __future__ import annotations

from janus.observer.fingerprint import compute_fingerprint

BASE = dict(
    role="textbox", accessible_name="Full name", name_attr="name_ne", form_id=None, tag="input"
)


def test_same_inputs_produce_equal_fingerprints() -> None:
    assert compute_fingerprint(**BASE) == compute_fingerprint(**BASE)


def test_different_accessible_name_changes_fingerprint() -> None:
    other = dict(BASE, accessible_name="Mobile number")
    assert compute_fingerprint(**BASE) != compute_fingerprint(**other)


def test_different_role_changes_fingerprint() -> None:
    other = dict(BASE, role="combobox")
    assert compute_fingerprint(**BASE) != compute_fingerprint(**other)


def test_different_name_attr_changes_fingerprint() -> None:
    other = dict(BASE, name_attr="phone")
    assert compute_fingerprint(**BASE) != compute_fingerprint(**other)


def test_different_form_id_changes_fingerprint() -> None:
    other = dict(BASE, form_id="apply-form")
    assert compute_fingerprint(**BASE) != compute_fingerprint(**other)


def test_different_tag_changes_fingerprint() -> None:
    other = dict(BASE, tag="textarea")
    assert compute_fingerprint(**BASE) != compute_fingerprint(**other)


def test_fingerprint_is_hashable_and_frozen() -> None:
    fp = compute_fingerprint(**BASE)
    assert {fp, compute_fingerprint(**BASE)} == {fp}
