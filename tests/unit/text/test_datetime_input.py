"""janus.text.datetime_input (docs/PLAN.md B1b)."""

from __future__ import annotations

import pytest

from janus.text.datetime_input import date_value, native_input_value, time_value


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10/11/2011", "2011-10-11"),  # MiniWoB enter-date: month/day
        ("03/05/2016", "2016-03-05"),
        ("25/12/2020", "2020-12-25"),  # day > 12: day/month
        ("2011-10-11", "2011-10-11"),
        ("१०/११/२०११", "2011-10-11"),  # Devanagari digits
        ("13/13/2020", "13/13/2020"),  # invalid: unchanged
        ("2/30/2020", "2/30/2020"),  # no Feb 30: unchanged
        ("next tuesday", "next tuesday"),
    ],
)
def test_date_value(text: str, expected: str) -> None:
    assert date_value(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("8:04 PM", "20:04"),
        ("11:34 AM", "11:34"),
        ("12:05 AM", "00:05"),
        ("12:30 PM", "12:30"),
        ("6:01 am", "06:01"),
        ("20:15", "20:15"),
        ("13:00 PM", "13:00 PM"),  # invalid 12-hour: unchanged
        ("25:00", "25:00"),
    ],
)
def test_time_value(text: str, expected: str) -> None:
    assert time_value(text) == expected


def test_only_native_date_and_time_inputs_are_converted() -> None:
    assert native_input_value("date", "10/11/2011") == "2011-10-11"
    assert native_input_value("TIME", "8:04 PM") == "20:04"
    assert native_input_value("text", "10/11/2011") == "10/11/2011"
    assert native_input_value(None, "8:04 PM") == "8:04 PM"
