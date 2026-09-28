"""Devanagari digits, text normalization, and Bikram Sambat (BS) <-> Gregorian (AD) dates.

The BS calendar has variable month lengths per year with no simple formula, so
conversion needs a per-year month-length table. The table below (BS 1975-2100) and
the reference point (BS 1975-01-01 = AD 1918-04-13) were cross-checked against the
`nepali_datetime` PyPI package (installed to a scratch dir for verification only;
it is not a project dependency) -- the same approach M1a used for its single
hardcoded date.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_NE_DIGITS = "०१२३४५६७८९"
_TO_NE_DIGITS = str.maketrans("0123456789", _NE_DIGITS)
_TO_ASCII_DIGITS = str.maketrans(_NE_DIGITS, "0123456789")


def to_ne_digits(value: object) -> str:
    return str(value).translate(_TO_NE_DIGITS)


def to_ascii_digits(value: object) -> str:
    return str(value).translate(_TO_ASCII_DIGITS)


def normalize_text(value: str) -> str:
    """Casefold (for Latin script) and collapse whitespace, for keyword matching."""
    return " ".join(value.split()).casefold()


# docs/PLAN.md P5: a table row's first-cell text is admitted as a `row_key` only if
# it is *entirely* digits (ASCII or Devanagari) and hyphens, at most 12 characters --
# no letter of any script ever matches. This is deliberately a closed character
# class, not a length/shape heuristic: a page-authored cell reaching the planner at
# all is a relaxation of invariant 1 (docs/ARCHITECTURE.md), and the only thing that
# keeps it safe is that nothing resembling an instruction can be expressed in it.
_ROW_KEY = re.compile(r"^[0-9०-९-]{1,12}$")


def normalize_row_key(text: str | None) -> str | None:
    """A row-id shape, normalized to ASCII digits -- or `None` if `text` is missing
    or doesn't strictly match (including any text with a letter in it)."""
    if text is None:
        return None
    stripped = text.strip()
    if not _ROW_KEY.match(stripped):
        return None
    return to_ascii_digits(stripped)


class BSDateError(ValueError):
    """Raised for an out-of-range BS year or an invalid month/day within a year."""


_MIN_BS_YEAR = 1975
_MAX_BS_YEAR = 2100
_REFERENCE_AD = date(1918, 4, 13)  # = BS 1975-01-01

# One row per BS year (1975..2100): the 12 month lengths (Baisakh..Chait).
_CALENDAR_CSV = """
1975,31,31,32,32,31,30,30,29,30,29,30,30
1976,31,32,31,32,31,30,30,30,29,29,30,31
1977,30,32,31,32,31,30,30,30,29,30,29,31
1978,31,31,32,31,31,31,30,29,30,29,30,30
1979,31,31,32,32,31,30,30,29,30,29,30,30
1980,31,32,31,32,31,30,30,30,29,29,30,31
1981,31,31,31,32,31,31,29,30,30,29,30,30
1982,31,31,32,31,31,31,30,29,30,29,30,30
1983,31,31,32,32,31,30,30,29,30,29,30,30
1984,31,32,31,32,31,30,30,30,29,29,30,31
1985,31,31,31,32,31,31,29,30,30,29,30,30
1986,31,31,32,31,31,31,30,29,30,29,30,30
1987,31,32,31,32,31,30,30,29,30,29,30,30
1988,31,32,31,32,31,30,30,30,29,29,30,31
1989,31,31,31,32,31,31,29,30,30,29,30,30
1990,31,31,32,31,31,31,30,29,30,29,30,30
1991,31,32,31,32,31,30,30,30,29,29,30,30
1992,31,32,31,32,31,30,30,30,29,30,29,31
1993,31,31,32,31,31,31,30,29,30,29,30,30
1994,31,31,32,31,31,31,30,29,30,29,30,30
1995,31,32,31,32,31,30,30,30,29,29,30,30
1996,31,32,31,32,31,30,30,30,29,30,29,31
1997,31,31,32,31,31,31,30,29,30,29,30,30
1998,31,31,32,31,31,31,30,29,30,29,30,30
1999,31,32,31,32,31,30,30,30,29,29,30,31
2000,30,32,31,32,31,30,30,30,29,30,29,31
2001,31,31,32,31,31,31,30,29,30,29,30,30
2002,31,31,32,32,31,30,30,29,30,29,30,30
2003,31,32,31,32,31,30,30,30,29,29,30,31
2004,30,32,31,32,31,30,30,30,29,30,29,31
2005,31,31,32,31,31,31,30,29,30,29,30,30
2006,31,31,32,32,31,30,30,29,30,29,30,30
2007,31,32,31,32,31,30,30,30,29,29,30,31
2008,31,31,31,32,31,31,29,30,30,29,29,31
2009,31,31,32,31,31,31,30,29,30,29,30,30
2010,31,31,32,32,31,30,30,29,30,29,30,30
2011,31,32,31,32,31,30,30,30,29,29,30,31
2012,31,31,31,32,31,31,29,30,30,29,30,30
2013,31,31,32,31,31,31,30,29,30,29,30,30
2014,31,31,32,32,31,30,30,29,30,29,30,30
2015,31,32,31,32,31,30,30,30,29,29,30,31
2016,31,31,31,32,31,31,29,30,30,29,30,30
2017,31,31,32,31,31,31,30,29,30,29,30,30
2018,31,32,31,32,31,30,30,29,30,29,30,30
2019,31,32,31,32,31,30,30,30,29,30,29,31
2020,31,31,31,32,31,31,30,29,30,29,30,30
2021,31,31,32,31,31,31,30,29,30,29,30,30
2022,31,32,31,32,31,30,30,30,29,29,30,30
2023,31,32,31,32,31,30,30,30,29,30,29,31
2024,31,31,31,32,31,31,30,29,30,29,30,30
2025,31,31,32,31,31,31,30,29,30,29,30,30
2026,31,32,31,32,31,30,30,30,29,29,30,31
2027,30,32,31,32,31,30,30,30,29,30,29,31
2028,31,31,32,31,31,31,30,29,30,29,30,30
2029,31,31,32,31,32,30,30,29,30,29,30,30
2030,31,32,31,32,31,30,30,30,29,29,30,31
2031,30,32,31,32,31,30,30,30,29,30,29,31
2032,31,31,32,31,31,31,30,29,30,29,30,30
2033,31,31,32,32,31,30,30,29,30,29,30,30
2034,31,32,31,32,31,30,30,30,29,29,30,31
2035,30,32,31,32,31,31,29,30,30,29,29,31
2036,31,31,32,31,31,31,30,29,30,29,30,30
2037,31,31,32,32,31,30,30,29,30,29,30,30
2038,31,32,31,32,31,30,30,30,29,29,30,31
2039,31,31,31,32,31,31,29,30,30,29,30,30
2040,31,31,32,31,31,31,30,29,30,29,30,30
2041,31,31,32,32,31,30,30,29,30,29,30,30
2042,31,32,31,32,31,30,30,30,29,29,30,31
2043,31,31,31,32,31,31,29,30,30,29,30,30
2044,31,31,32,31,31,31,30,29,30,29,30,30
2045,31,32,31,32,31,30,30,29,30,29,30,30
2046,31,32,31,32,31,30,30,30,29,29,30,31
2047,31,31,31,32,31,31,30,29,30,29,30,30
2048,31,31,32,31,31,31,30,29,30,29,30,30
2049,31,32,31,32,31,30,30,30,29,29,30,30
2050,31,32,31,32,31,30,30,30,29,30,29,31
2051,31,31,31,32,31,31,30,29,30,29,30,30
2052,31,31,32,31,31,31,30,29,30,29,30,30
2053,31,32,31,32,31,30,30,30,29,29,30,30
2054,31,32,31,32,31,30,30,30,29,30,29,31
2055,31,31,32,31,31,31,30,29,30,29,30,30
2056,31,31,32,31,32,30,30,29,30,29,30,30
2057,31,32,31,32,31,30,30,30,29,29,30,31
2058,30,32,31,32,31,30,30,30,29,30,29,31
2059,31,31,32,31,31,31,30,29,30,29,30,30
2060,31,31,32,32,31,30,30,29,30,29,30,30
2061,31,32,31,32,31,30,30,30,29,29,30,31
2062,31,31,31,32,31,31,29,30,29,30,29,31
2063,31,31,32,31,31,31,30,29,30,29,30,30
2064,31,31,32,32,31,30,30,29,30,29,30,30
2065,31,32,31,32,31,30,30,30,29,29,30,31
2066,31,31,31,32,31,31,29,30,30,29,29,31
2067,31,31,32,31,31,31,30,29,30,29,30,30
2068,31,31,32,32,31,30,30,29,30,29,30,30
2069,31,32,31,32,31,30,30,30,29,29,30,31
2070,31,31,31,32,31,31,29,30,30,29,30,30
2071,31,31,32,31,31,31,30,29,30,29,30,30
2072,31,32,31,32,31,30,30,29,30,29,30,30
2073,31,32,31,32,31,30,30,30,29,29,30,31
2074,31,31,31,32,31,31,30,29,30,29,30,30
2075,31,31,32,31,31,31,30,29,30,29,30,30
2076,31,32,31,32,31,30,30,30,29,29,30,30
2077,31,32,31,32,31,30,30,30,29,30,29,31
2078,31,31,31,32,31,31,30,29,30,29,30,30
2079,31,31,32,31,31,31,30,29,30,29,30,30
2080,31,32,31,32,31,30,30,30,29,29,30,30
2081,31,32,31,32,31,30,30,30,29,30,29,31
2082,31,31,32,31,31,31,30,29,30,29,30,30
2083,31,31,32,31,31,31,30,29,30,29,30,30
2084,31,31,32,31,31,30,30,30,29,30,30,30
2085,31,32,31,32,30,31,30,30,29,30,30,30
2086,30,32,31,32,31,30,30,30,29,30,30,30
2087,31,31,32,31,31,31,30,29,30,30,30,30
2088,30,31,32,32,30,31,30,30,29,30,30,30
2089,30,32,31,32,31,30,30,30,29,30,30,30
2090,30,32,31,32,31,30,30,30,29,30,30,30
2091,31,31,32,31,31,31,30,30,29,30,30,30
2092,30,31,32,32,31,30,30,30,29,30,30,30
2093,30,32,31,32,31,30,30,30,29,30,30,30
2094,31,31,32,31,31,30,30,30,29,30,30,30
2095,31,31,32,31,31,31,30,29,30,30,30,30
2096,30,31,32,32,31,30,30,29,30,29,30,30
2097,31,32,31,32,31,30,30,30,29,30,30,30
2098,31,31,32,31,31,31,29,30,29,30,29,31
2099,31,31,32,31,31,31,30,29,29,30,30,30
2100,31,32,31,32,30,31,30,29,30,29,30,30
"""


def _parse_calendar(csv_text: str) -> dict[int, tuple[int, ...]]:
    table: dict[int, tuple[int, ...]] = {}
    for line in csv_text.strip().splitlines():
        parts = [int(x) for x in line.split(",")]
        year, lengths = parts[0], tuple(parts[1:])
        if len(lengths) != 12:
            raise ValueError(f"bad calendar row for BS {year}: expected 12 months")
        table[year] = lengths
    return table


_MONTH_LENGTHS = _parse_calendar(_CALENDAR_CSV)
assert set(_MONTH_LENGTHS) == set(range(_MIN_BS_YEAR, _MAX_BS_YEAR + 1))

_DAYS_IN_YEAR = {year: sum(lengths) for year, lengths in _MONTH_LENGTHS.items()}

_DAYS_BEFORE_YEAR: dict[int, int] = {}
_running = 0
for _year in range(_MIN_BS_YEAR, _MAX_BS_YEAR + 1):
    _DAYS_BEFORE_YEAR[_year] = _running
    _running += _DAYS_IN_YEAR[_year]


def _validate_bs(year: int, month: int, day: int) -> None:
    if not (_MIN_BS_YEAR <= year <= _MAX_BS_YEAR):
        raise BSDateError(
            f"BS year {year} is outside the supported range {_MIN_BS_YEAR}-{_MAX_BS_YEAR}"
        )
    if not (1 <= month <= 12):
        raise BSDateError(f"BS month {month} is outside 1-12")
    max_day = _MONTH_LENGTHS[year][month - 1]
    if not (1 <= day <= max_day):
        raise BSDateError(f"BS day {day} is outside 1-{max_day} for {year}-{month:02d}")


def _bs_ordinal(year: int, month: int, day: int) -> int:
    _validate_bs(year, month, day)
    days_before_month = sum(_MONTH_LENGTHS[year][: month - 1])
    return _DAYS_BEFORE_YEAR[year] + days_before_month + day


def bs_to_ad(year: int, month: int, day: int) -> date:
    """Convert a BS calendar date to the equivalent Gregorian (AD) date."""
    ordinal = _bs_ordinal(year, month, day)
    return _REFERENCE_AD + timedelta(days=ordinal - 1)


def ad_to_bs(ad_date: date) -> tuple[int, int, int]:
    """Convert a Gregorian (AD) date to the equivalent BS (year, month, day)."""
    ordinal = (ad_date - _REFERENCE_AD).days + 1
    if ordinal < 1:
        raise BSDateError(f"{ad_date} is before the supported BS range (BS {_MIN_BS_YEAR}-01-01)")
    remaining = ordinal
    year = _MIN_BS_YEAR
    while remaining > _DAYS_IN_YEAR[year]:
        remaining -= _DAYS_IN_YEAR[year]
        year += 1
        if year > _MAX_BS_YEAR:
            raise BSDateError(f"{ad_date} is after the supported BS range (BS {_MAX_BS_YEAR})")
    month = 1
    for length in _MONTH_LENGTHS[year]:
        if remaining <= length:
            break
        remaining -= length
        month += 1
    return year, month, remaining
