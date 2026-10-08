from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from aurum.core.errors import NaiveDatetimeError
from aurum.core.timeframes import Timeframe


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("tf", "minutes"),
    [(Timeframe.M1, 1), (Timeframe.M5, 5), (Timeframe.M15, 15), (Timeframe.H1, 60)],
)
def test_durations(tf: Timeframe, minutes: int) -> None:
    assert tf.duration == timedelta(minutes=minutes)
    assert tf.seconds == minutes * 60


def test_parse_is_case_insensitive_and_strict() -> None:
    assert Timeframe.parse(" m15 ") is Timeframe.M15
    with pytest.raises(ValueError, match="unknown timeframe"):
        Timeframe.parse("H4")


def test_mt5_constant_name() -> None:
    assert Timeframe.M5.mt5_constant_name == "TIMEFRAME_M5"


def test_master_prompt_example_decision_at_1035_sees_h1_closed_at_1000() -> None:
    span = Timeframe.H1.last_completed_bar(utc(2026, 3, 10, 10, 35))
    assert span.open_time == utc(2026, 3, 10, 9, 0)
    assert span.close_time == utc(2026, 3, 10, 10, 0)


def test_bar_closing_exactly_now_is_completed() -> None:
    span = Timeframe.H1.last_completed_bar(utc(2026, 3, 10, 10, 0))
    assert span.close_time == utc(2026, 3, 10, 10, 0)


def test_one_microsecond_before_close_the_bar_is_still_forming() -> None:
    now = utc(2026, 3, 10, 9, 59, 59, 999999)
    assert Timeframe.H1.last_completed_bar(now).close_time == utc(2026, 3, 10, 9, 0)


def test_m15_and_m5_examples() -> None:
    now = utc(2026, 3, 10, 10, 35)
    assert Timeframe.M15.last_completed_bar(now).close_time == utc(2026, 3, 10, 10, 30)
    assert Timeframe.M5.last_completed_bar(now).close_time == utc(2026, 3, 10, 10, 35)
    assert Timeframe.M5.last_completed_bar(now).open_time == utc(2026, 3, 10, 10, 30)


def test_bar_open_time_is_the_forming_bar() -> None:
    assert Timeframe.M15.bar_open_time(utc(2026, 3, 10, 10, 44, 59)) == utc(2026, 3, 10, 10, 30)


def test_bar_close_time_requires_aligned_open() -> None:
    assert Timeframe.M15.bar_close_time(utc(2026, 3, 10, 10, 30)) == utc(2026, 3, 10, 10, 45)
    with pytest.raises(ValueError, match="not a M15 bar open time"):
        Timeframe.M15.bar_close_time(utc(2026, 3, 10, 10, 31))


def test_non_utc_aware_input_is_converted() -> None:
    london_summer = datetime(2026, 7, 1, 11, 35, tzinfo=ZoneInfo("Europe/London"))  # 10:35 UTC
    span = Timeframe.H1.last_completed_bar(london_summer)
    assert span.close_time == utc(2026, 7, 1, 10, 0)
    assert span.close_time.tzinfo is UTC
    # Fixed offsets too (e.g. a broker server at GMT+3)
    plus3 = datetime(2026, 7, 1, 13, 35, tzinfo=timezone(timedelta(hours=3)))
    assert Timeframe.H1.last_completed_bar(plus3).close_time == utc(2026, 7, 1, 10, 0)


def test_naive_datetimes_are_rejected() -> None:
    naive = datetime(2026, 3, 10, 10, 35)  # noqa: DTZ001 - deliberately naive
    with pytest.raises(NaiveDatetimeError):
        Timeframe.H1.last_completed_bar(naive)
    with pytest.raises(NaiveDatetimeError):
        Timeframe.H1.bar_close_time(naive)
