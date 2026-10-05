"""Time helpers: tz-aware UTC now and US business-day arithmetic for SLA
clocks and condition due dates (design A §1.4, §2.1.4).

The holiday table is the US federal (OPM) observed calendar for 2026 and
2027, hard-coded and checked by rule in the unit tests: a holiday on a
Saturday is observed the Friday before, on a Sunday the Monday after.
That is why 2027-12-31 is listed: it is the observed New Year's Day 2028
(1 January 2028 is a Saturday). Dates outside the covered years fall back
to weekends-only and log a warning, so an SLA computed in 2028 is still
answered but the gap is visible in the logs.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

logger = logging.getLogger(__name__)

US_FEDERAL_HOLIDAYS: dict[date, str] = {
    # 2026
    date(2026, 1, 1): "New Year's Day",
    date(2026, 1, 19): "Birthday of Martin Luther King, Jr.",
    date(2026, 2, 16): "Washington's Birthday (Presidents Day)",
    date(2026, 5, 25): "Memorial Day",
    date(2026, 6, 19): "Juneteenth National Independence Day",
    date(2026, 7, 3): "Independence Day (observed; Jul 4 is a Saturday)",
    date(2026, 9, 7): "Labor Day",
    date(2026, 10, 12): "Columbus Day",
    date(2026, 11, 11): "Veterans Day",
    date(2026, 11, 26): "Thanksgiving Day",
    date(2026, 12, 25): "Christmas Day",
    # 2027
    date(2027, 1, 1): "New Year's Day",
    date(2027, 1, 18): "Birthday of Martin Luther King, Jr.",
    date(2027, 2, 15): "Washington's Birthday (Presidents Day)",
    date(2027, 5, 31): "Memorial Day",
    date(2027, 6, 18): "Juneteenth (observed; Jun 19 is a Saturday)",
    date(2027, 7, 5): "Independence Day (observed; Jul 4 is a Sunday)",
    date(2027, 9, 6): "Labor Day",
    date(2027, 10, 11): "Columbus Day",
    date(2027, 11, 11): "Veterans Day",
    date(2027, 11, 25): "Thanksgiving Day",
    date(2027, 12, 24): "Christmas Day (observed; Dec 25 is a Saturday)",
    date(2027, 12, 31): "New Year's Day 2028 (observed; Jan 1 is a Saturday)",
}

COVERED_YEARS = frozenset({2026, 2027})


def utcnow() -> datetime:
    """The current time as a tz-aware UTC datetime (never naive)."""
    return datetime.now(UTC)


def _as_date(value: date) -> date:
    """Accept a ``date`` or ``datetime``; a datetime is taken in UTC
    (naive datetimes are assumed to already be UTC).
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.date()
    return value


def _check_coverage(day: date) -> None:
    """Warn when ``day`` is outside the holiday table's years."""
    if day.year not in COVERED_YEARS:
        logger.warning(
            "business-day calendar has no holidays for %s; weekends only",
            day.year,
            extra={"year": day.year},
        )


def is_business_day(day: date) -> bool:
    """Monday to Friday and not a US federal (observed) holiday."""
    d = _as_date(day)
    return d.weekday() < 5 and d not in US_FEDERAL_HOLIDAYS


def business_days_between(a: date, b: date) -> int:
    """Business days in the half-open interval ``(a, b]``.

    The start day is not counted and the end day is, so the result is the
    number of business-day steps from ``a`` to ``b`` and
    ``business_days_between(a, add_business_days(a, n)) == n`` for
    ``n >= 0``. Returns a negative count when ``b`` is before ``a``.
    """
    start, end = _as_date(a), _as_date(b)
    if end < start:
        return -business_days_between(end, start)
    _check_coverage(start)
    if end.year != start.year:
        _check_coverage(end)
    count = 0
    day = start
    while day < end:
        day += timedelta(days=1)
        if is_business_day(day):
            count += 1
    return count


def add_business_days(start: date, n: int) -> date:
    """The date ``n`` business days after ``start`` (before it when ``n``
    is negative). ``n == 0`` returns ``start`` unchanged even when it is not
    itself a business day.
    """
    day = _as_date(start)
    _check_coverage(day)
    step = timedelta(days=1 if n >= 0 else -1)
    remaining = abs(n)
    while remaining:
        day += step
        if is_business_day(day):
            remaining -= 1
    return day
