"""Deterministic checks and canonical forms for values read off a document photo.

A vision model proposes text; nothing it says is trusted until it passes here and a person
confirms it (janus/extract_doc.py, docs/PLAN.md "Photo to fill"). Each normaliser returns the
canonical string a portal form expects, or None when the value is not a valid instance of that
field, which the caller treats as "unreadable: ask the person".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from janus.text.nepali import BSDateError, bs_to_ad, to_ascii_digits, to_ne_digits

# Never read off a photo, whatever the model says: these are typed or spoken by the person only.
NEVER_FROM_PHOTO = frozenset({"password", "pin", "otp"})

_NAME = re.compile(r"^[ऀ-ॿ]+( [ऀ-ॿ]+)*$")
_CITIZENSHIP = re.compile(r"^\d{1,3}(-\d{1,3}){1,4}(-\d{1,6})$")
_BS_DATE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_AD_DAY_FIRST = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$")
_AD_YEAR_FIRST = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_BOID = re.compile(r"^\d{8}$")
_CRN = re.compile(r"^(?:CRN-?)?(\d{6})$", re.IGNORECASE)
_PHONE = re.compile(r"^9\d{9}$")
_MIN_BS_YEAR, _MAX_BS_YEAR = 2000, 2090  # what the replica forms accept


def _squash(raw: str | None) -> str:
    return " ".join((raw or "").split())


def name(raw: str | None) -> str | None:
    text = _squash(raw)
    return text if text and len(text) <= 60 and _NAME.match(text) else None


def citizenship_no(raw: str | None) -> str | None:
    text = to_ascii_digits(_squash(raw).replace(" ", ""))
    return to_ne_digits(text) if _CITIZENSHIP.match(text) else None


def dob_bs(raw: str | None) -> str | None:
    """A real Bikram Sambat date as YYYY-MM-DD in ASCII digits (what the form takes)."""
    match = _BS_DATE.match(to_ascii_digits(_squash(raw)))
    if not match:
        return None
    year, month, day = (int(g) for g in match.groups())
    if not _MIN_BS_YEAR <= year <= _MAX_BS_YEAR:
        return None
    try:
        bs_to_ad(year, month, day)  # raises BSDateError for a month or day that does not exist
    except BSDateError:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def dob_ad(raw: str | None) -> str | None:
    """A real Gregorian date as YYYY-MM-DD. A printed DD/MM/YYYY is read day first."""
    text = to_ascii_digits(_squash(raw))
    year_first = _AD_YEAR_FIRST.match(text)
    day_first = _AD_DAY_FIRST.match(text)
    if year_first:
        year, month, day = (int(g) for g in year_first.groups())
    elif day_first:
        day, month, year = (int(g) for g in day_first.groups())
    else:
        return None
    try:
        return date(year, month, day).isoformat() if 1900 <= year <= date.today().year else None
    except ValueError:
        return None


def ward(raw: str | None) -> str | None:
    text = to_ascii_digits(_squash(raw))
    return str(int(text)) if text.isdigit() and 1 <= int(text) <= 32 else None


def boid(raw: str | None) -> str | None:
    text = to_ascii_digits(_squash(raw).replace(" ", ""))
    return to_ne_digits(text) if _BOID.match(text) else None


def crn(raw: str | None) -> str | None:
    """`CRN-dddddd`. A bare six-digit number under a CRN label is given its prefix."""
    match = _CRN.match(to_ascii_digits(_squash(raw)).replace(" ", ""))
    return f"CRN-{match.group(1)}" if match else None


def phone(raw: str | None) -> str | None:
    text = to_ascii_digits(_squash(raw).replace(" ", "").replace("-", ""))
    return text if _PHONE.match(text) else None


def bank(raw: str | None) -> str | None:
    text = _squash(raw)
    return text if text and len(text) <= 60 else None


NORMALIZERS: dict[str, Callable[[str | None], str | None]] = {
    "name": name,
    "citizenship_no": citizenship_no,
    "dob_bs": dob_bs,
    "dob_ad": dob_ad,
    "ward": ward,
    "boid": boid,
    "crn": crn,
    "phone": phone,
    "bank": bank,
}


def normalize_field(key: str, raw: str | None) -> str | None:
    """The canonical value of `key` read as `raw`, or None if invalid. Unknown keys and keys that
    may never come from a photo are always None."""
    if key in NEVER_FROM_PHOTO:
        return None
    normalizer = NORMALIZERS.get(key)
    return normalizer(raw) if normalizer else None
