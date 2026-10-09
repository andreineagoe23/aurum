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


def test_summer_session_bounds(hours: MarketHours) -> None:
    s = hours.session(date(2026, 4, 14))  # Tuesday, US summer time (UTC-4)
    assert s is not None
    assert s.open == utc(2026, 4, 13, 22, 0)  # Monday 18:00 New York
    assert s.close == utc(2026, 4, 14, 20, 59)  # last tick measured at 20:58:59


def test_winter_session_bounds(hours: MarketHours) -> None:
    s = hours.session(date(2026, 1, 13))  # US winter time (UTC-5)
    assert s is not None
    assert s.open == utc(2026, 1, 12, 23, 0)
    assert s.close == utc(2026, 1, 13, 21, 59)


def test_monday_opens_sunday_evening(hours: MarketHours) -> None:
    s = hours.session(date(2026, 4, 13))
    assert s is not None
    assert s.open == utc(2026, 4, 12, 22, 0)  # first tick measured at 22:00:00.850


def test_weekends_and_closed_dates_have_no_session(hours: MarketHours) -> None:
    assert hours.session(date(2026, 4, 11)) is None  # Saturday
    assert hours.session(date(2026, 4, 12)) is None  # Sunday
    assert hours.session(date(2026, 4, 3)) is None  # Good Friday (measured: no ticks)
    assert hours.session(date(2026, 7, 3)) is None  # Independence Day observed


@pytest.mark.parametrize(
    ("t", "is_open"),
    [
        (utc(2026, 4, 14, 20, 58, 59), True),
        (utc(2026, 4, 14, 20, 59), False),  # daily break starts
        (utc(2026, 4, 14, 21, 59), False),
        (utc(2026, 4, 14, 22, 0), True),  # reopens 18:00 New York
        (utc(2026, 4, 10, 21, 30), False),  # Friday evening
        (utc(2026, 4, 11, 12, 0), False),  # Saturday
        (utc(2026, 4, 2, 22, 30), False),  # evening before Good Friday
        (utc(2026, 4, 2, 12, 0), True),
    ],
)
def test_is_open(hours: MarketHours, t: datetime, is_open: bool) -> None:
    assert hours.is_open(t) is is_open


def test_daily_break_expects_no_data(hours: MarketHours) -> None:
    # Measured break: last tick 20:58:59.299, next 22:00:02.056 (2026-04-14).
    start, end = utc(2026, 4, 14, 20, 58, 59), utc(2026, 4, 14, 22, 0, 2)
    assert hours.expected_data_seconds(start, end) == pytest.approx(1.0)


def test_open_grace_excuses_a_late_first_tick(hours: MarketHours) -> None:
    # Sunday opens are often first ticked at 18:01:00 New York.
    assert hours.expected_data_seconds(utc(2026, 5, 8, 20, 58), utc(2026, 5, 10, 22, 1)) == (
        pytest.approx(60.0)
    )


def test_expected_seconds_of_a_full_week(hours: MarketHours) -> None:
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


@pytest.mark.parametrize(
    ("changes", "match"),
    [
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
    ],
)
def test_invalid_market_config(
    app_config: AppConfig, changes: dict[str, object], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        MarketHoursConfig.model_validate({**app_config.data.market.model_dump(), **changes})
