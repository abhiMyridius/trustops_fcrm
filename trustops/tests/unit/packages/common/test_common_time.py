"""fcrm_common.time: UTC now and US business-day arithmetic."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fcrm_common.time import (
    US_FEDERAL_HOLIDAYS,
    add_business_days,
    business_days_between,
    is_business_day,
    utcnow,
)

# OPM observed dates, as data (verified by rule when the table was written).
EXPECTED_HOLIDAYS = [
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 10, 12),
    date(2026, 11, 11),
    date(2026, 11, 26),
    date(2026, 12, 25),
    date(2027, 1, 1),
    date(2027, 1, 18),
    date(2027, 2, 15),
    date(2027, 5, 31),
    date(2027, 6, 18),
    date(2027, 7, 5),
    date(2027, 9, 6),
    date(2027, 10, 11),
    date(2027, 11, 11),
    date(2027, 11, 25),
    date(2027, 12, 24),
    date(2027, 12, 31),
]


def test_utcnow_is_tz_aware_utc():
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_holiday_table_matches_expected_dates():
    assert sorted(US_FEDERAL_HOLIDAYS) == EXPECTED_HOLIDAYS


def test_holidays_all_fall_on_weekdays():
    assert all(d.weekday() < 5 for d in US_FEDERAL_HOLIDAYS)


def test_is_business_day_excludes_weekends_and_holidays():
    assert is_business_day(date(2026, 10, 9))  # Friday, submission day
    assert not is_business_day(date(2026, 10, 10))  # Saturday
    assert not is_business_day(date(2026, 10, 12))  # Columbus Day


def test_add_business_days_skips_observed_independence_day():
    # Thu 2 Jul 2026 + 1 -> Fri 3 Jul is the observed holiday -> Mon 6 Jul.
    assert add_business_days(date(2026, 7, 2), 1) == date(2026, 7, 6)


def test_add_business_days_across_thanksgiving():
    # Wed 25 Nov 2026 + 2 -> skips Thu 26 (Thanksgiving) -> Fri 27, Mon 30.
    assert add_business_days(date(2026, 11, 25), 2) == date(2026, 11, 30)


def test_add_business_days_across_year_end_2027():
    # Thu 23 Dec 2027 + 1 -> Fri 24 (Christmas observed) -> Mon 27.
    assert add_business_days(date(2027, 12, 23), 1) == date(2027, 12, 27)
    # Thu 30 Dec 2027 + 1 -> Fri 31 (New Year 2028 observed) -> Mon 3 Jan.
    assert add_business_days(date(2027, 12, 30), 1) == date(2028, 1, 3)


def test_add_business_days_negative_and_zero():
    assert add_business_days(date(2026, 7, 6), -1) == date(2026, 7, 2)
    assert add_business_days(date(2026, 10, 10), 0) == date(2026, 10, 10)


def test_business_days_between_across_holiday():
    # Fri 2 Oct 2026 -> Fri 16 Oct 2026: 10 weekdays minus Columbus Day.
    assert business_days_between(date(2026, 10, 2), date(2026, 10, 16)) == 9


def test_business_days_between_is_antisymmetric_and_zero_on_same_day():
    a, b = date(2026, 12, 21), date(2027, 1, 5)
    assert business_days_between(a, b) == -business_days_between(b, a)
    assert business_days_between(a, a) == 0


@pytest.mark.parametrize("n", [0, 1, 5, 9, 23, 60])
def test_business_days_between_inverts_add_business_days(n):
    start = date(2026, 11, 20)
    assert business_days_between(start, add_business_days(start, n)) == n


def test_business_day_math_accepts_datetimes_in_utc():
    late_thursday_utc = datetime(2026, 7, 2, 23, 30, tzinfo=UTC)
    assert add_business_days(late_thursday_utc, 1) == date(2026, 7, 6)


def test_outside_covered_years_falls_back_to_weekends_with_warning(caplog):
    with caplog.at_level("WARNING", logger="fcrm_common.time"):
        assert add_business_days(date(2028, 7, 3), 1) == date(2028, 7, 4)
    assert any("2028" in r.getMessage() for r in caplog.records)
