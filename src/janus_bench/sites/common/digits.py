"""Devanagari digit helpers for the replica sites (kept separate from the runtime's text/)."""

from __future__ import annotations

_NE = "०१२३४५६७८९"
_TO_NE = str.maketrans("0123456789", _NE)
_TO_ASCII = str.maketrans(_NE, "0123456789")


def to_ne_digits(value: object) -> str:
    return str(value).translate(_TO_NE)


def to_ascii_digits(value: object) -> str:
    return str(value).translate(_TO_ASCII)
