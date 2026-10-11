"""Values for native date/time inputs (docs/PLAN.md B1b).

`<input type="date">` accepts only `YYYY-MM-DD` and `<input type="time">` only `HH:MM`;
Playwright raises "Malformed value" for anything else, which ended MiniWoB enter-date and
enter-time runs. This converts the common written forms deterministically (CLAUDE.md:
dates are code, not prompts). Anything it cannot read unambiguously is returned unchanged,
so the browser's own error still surfaces.

Slash dates: `A/B/YYYY` is read as month/day when B > 12 or both are <= 12 (the US order
the HTML date picker and MiniWoB use), and as day/month when A > 12.
"""

from __future__ import annotations

import re
from datetime import date

from janus.text.nepali import to_ascii_digits

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_DATE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?\s*([AaPp][Mm])?$")


def date_value(text: str) -> str:
    raw = to_ascii_digits(text.strip())
    if _ISO_DATE.match(raw):
        return raw
    m = _SLASH_DATE.match(raw)
    if not m:
        return text
    a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    month, day = (b, a) if a > 12 else (a, b)
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return text


def time_value(text: str) -> str:
    raw = to_ascii_digits(text.strip())
    m = _TIME.match(raw)
    if not m:
        return text
    hour, minute, half = int(m.group(1)), int(m.group(2)), m.group(3)
    if half:
        if not 1 <= hour <= 12:
            return text
        hour = hour % 12 + (12 if half.lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        return text
    return f"{hour:02d}:{minute:02d}"


def native_input_value(input_type: str | None, text: str) -> str:
    kind = (input_type or "").lower()
    if kind == "date":
        return date_value(text)
    if kind == "time":
        return time_value(text)
    return text
