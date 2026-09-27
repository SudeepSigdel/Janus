"""BS<->AD date conversion.

The month-length table and reference point were cross-checked against the
`nepali_datetime` PyPI package during development (scratch install, not a
project dependency); these tests pin the fixed points and edge cases that
check covered.
"""

from __future__ import annotations

from datetime import date

import pytest

from janus.text.nepali import BSDateError, ad_to_bs, bs_to_ad


def test_known_fixed_point_ad_to_bs() -> None:
    assert ad_to_bs(date(2000, 1, 1)) == (2056, 9, 17)


def test_known_fixed_point_bs_to_ad() -> None:
    assert bs_to_ad(2056, 9, 17) == date(2000, 1, 1)


def test_reference_epoch_round_trips() -> None:
    assert ad_to_bs(date(1918, 4, 13)) == (1975, 1, 1)
    assert bs_to_ad(1975, 1, 1) == date(1918, 4, 13)


@pytest.mark.parametrize(
    "bs_date",
    [
        (1975, 1, 1),
        (2000, 1, 30),  # BS 2000 Baisakh has 30 days, not the usual 31/32
        (2056, 9, 17),
        (2090, 12, 30),
        (2100, 12, 30),
    ],
)
def test_bs_to_ad_and_back_round_trips(bs_date: tuple[int, int, int]) -> None:
    assert ad_to_bs(bs_to_ad(*bs_date)) == bs_date


def test_ad_to_bs_before_supported_range_raises() -> None:
    with pytest.raises(BSDateError):
        ad_to_bs(date(1918, 4, 12))


def test_bs_to_ad_year_out_of_range_raises() -> None:
    with pytest.raises(BSDateError):
        bs_to_ad(1974, 1, 1)
    with pytest.raises(BSDateError):
        bs_to_ad(2101, 1, 1)


def test_bs_to_ad_invalid_day_for_month_raises() -> None:
    with pytest.raises(BSDateError):
        bs_to_ad(2000, 1, 31)  # BS 2000 Baisakh only has 30 days


def test_bs_to_ad_invalid_month_raises() -> None:
    with pytest.raises(BSDateError):
        bs_to_ad(2056, 13, 1)
