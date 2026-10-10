"""Market hours, anchored on what Phase 2 measured from Fusion ticks (reports/phase_2.md)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from aurum.data.market_hours import MarketHours
from aurum.settings import AppConfig, MarketHoursConfig


@pytest.fixture
def hours(app_config: AppConfig) -> MarketHours:
    return MarketHours(app_config.data.market)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_session_bounds(hours: MarketHours) -> None:
    summer = hours.session(date(2026, 4, 14))  # Tuesday, US summer time (UTC-4)
    assert summer is not None
    assert summer.open == utc(2026, 4, 13, 22, 0)  # Monday 18:00 New York
    assert summer.close == utc(2026, 4, 14, 20, 59)  # last tick measured at 20:58:59
    winter = hours.session(date(2026, 1, 13))  # US winter time (UTC-5)
    assert winter is not None
    assert (winter.open, winter.close) == (utc(2026, 1, 12, 23, 0), utc(2026, 1, 13, 21, 59))
    monday = hours.session(date(2026, 4, 13))
    assert monday is not None
    assert monday.open == utc(2026, 4, 12, 22, 0)  # first tick measured at 22:00:00.850
    # Independence Day observed was an early close (measured: last bar 13:00 New York).
    july3 = hours.session(date(2026, 7, 3))
    assert july3 is not None
    assert july3.close == utc(2026, 7, 3, 17, 1)
    assert hours.session(date(2026, 4, 11)) is None  # Saturday
    assert hours.session(date(2026, 4, 12)) is None  # Sunday
    assert hours.session(date(2026, 4, 3)) is None  # Good Friday (measured: no ticks)


def test_is_open(hours: MarketHours) -> None:
    for t, is_open in [
        (utc(2026, 4, 14, 20, 58, 59), True),
        (utc(2026, 4, 14, 20, 59), False),  # daily break starts
        (utc(2026, 4, 14, 22, 0), True),  # reopens 18:00 New York
        (utc(2026, 4, 10, 21, 30), False),  # Friday evening
        (utc(2026, 4, 11, 12, 0), False),  # Saturday
        (utc(2026, 4, 2, 22, 30), False),  # evening before Good Friday
    ]:
        assert hours.is_open(t) is is_open, t


def test_expected_data_seconds(hours: MarketHours) -> None:
    # Measured break: last tick 20:58:59.299, next 22:00:02.056 (2026-04-14).
    assert hours.expected_data_seconds(
        utc(2026, 4, 14, 20, 58, 59), utc(2026, 4, 14, 22, 0, 2)
    ) == (pytest.approx(1.0))
    # Sunday opens are often first ticked at 18:01:00 New York: the grace excuses that.
    assert hours.expected_data_seconds(utc(2026, 5, 8, 20, 58), utc(2026, 5, 10, 22, 1)) == (
        pytest.approx(60.0)
    )
    week = hours.expected_data_seconds(utc(2026, 4, 12), utc(2026, 4, 19))
    per_day = timedelta(hours=22, minutes=59) - hours.grace
    assert week == pytest.approx(5 * per_day.total_seconds())


def test_trading_dates(hours: MarketHours) -> None:
    assert hours.trading_dates(date(2026, 3, 30), date(2026, 4, 6)) == [
        date(2026, 3, 30),
        date(2026, 3, 31),
        date(2026, 4, 1),
        date(2026, 4, 2),
        date(2026, 4, 6),
    ]


def test_early_close(app_config: AppConfig) -> None:
    cfg = MarketHoursConfig.model_validate(
        {
            **app_config.data.market.model_dump(),
            "early_closes": {"2026-11-27": {"close": "13:15", "reason": "Thanksgiving Friday"}},
        }
    )
    s = MarketHours(cfg).session(date(2026, 11, 27))
    assert s is not None
    assert s.close == utc(2026, 11, 27, 18, 15)


def test_invalid_market_config(app_config: AppConfig) -> None:
    cases: list[tuple[dict[str, object], str]] = [
        ({"close": "18:30"}, "close must be before open"),
        ({"closed_dates": {"2026-04-04": "Saturday"}}, "weekend"),
        (
            {"early_closes": {"2026-04-14": {"close": "17:30", "reason": "x"}}},
            "not before the normal close",
        ),
        (
            {
                "closed_dates": {"2026-04-14": "x"},
                "early_closes": {"2026-04-14": {"close": "12:00", "reason": "x"}},
            },
            "both closed and an early close",
        ),
        ({"timezone": "Mars/Olympus"}, "unknown timezone"),
    ]
    for changes, match in cases:
        with pytest.raises(ValueError, match=match):
            MarketHoursConfig.model_validate({**app_config.data.market.model_dump(), **changes})
