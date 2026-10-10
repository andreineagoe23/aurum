"""Timeframe anchors; tests/property/test_timeframe_properties.py covers the general rules."""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from aurum.core.errors import NaiveDatetimeError
from aurum.core.timeframes import Timeframe


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_parse_durations_and_mt5_names() -> None:
    assert Timeframe.parse(" m15 ") is Timeframe.M15
    with pytest.raises(ValueError, match="unknown timeframe"):
        Timeframe.parse("H4")
    assert [tf.seconds for tf in Timeframe] == [60, 300, 900, 3600]
    assert Timeframe.M5.duration == timedelta(minutes=5)
    assert Timeframe.M5.mt5_constant_name == "TIMEFRAME_M5"


def test_master_prompt_example_decision_at_1035_sees_h1_closed_at_1000() -> None:
    now = utc(2026, 3, 10, 10, 35)
    span = Timeframe.H1.last_completed_bar(now)
    assert (span.open_time, span.close_time) == (utc(2026, 3, 10, 9, 0), utc(2026, 3, 10, 10, 0))
    assert Timeframe.M15.last_completed_bar(now).close_time == utc(2026, 3, 10, 10, 30)
    assert Timeframe.M5.last_completed_bar(now).close_time == utc(2026, 3, 10, 10, 35)


def test_bar_completes_exactly_at_its_close() -> None:
    assert Timeframe.H1.last_completed_bar(utc(2026, 3, 10, 10, 0)).close_time == utc(
        2026, 3, 10, 10, 0
    )
    one_us_before = utc(2026, 3, 10, 9, 59, 59, 999999)
    assert Timeframe.H1.last_completed_bar(one_us_before).close_time == utc(2026, 3, 10, 9, 0)
    assert Timeframe.M15.bar_open_time(utc(2026, 3, 10, 10, 44, 59)) == utc(2026, 3, 10, 10, 30)
    assert Timeframe.M15.bar_close_time(utc(2026, 3, 10, 10, 30)) == utc(2026, 3, 10, 10, 45)
    with pytest.raises(ValueError, match="not a M15 bar open time"):
        Timeframe.M15.bar_close_time(utc(2026, 3, 10, 10, 31))


def test_aware_input_is_converted_and_naive_is_rejected() -> None:
    london_summer = datetime(2026, 7, 1, 11, 35, tzinfo=ZoneInfo("Europe/London"))  # 10:35 UTC
    span = Timeframe.H1.last_completed_bar(london_summer)
    assert span.close_time == utc(2026, 7, 1, 10, 0)
    assert span.close_time.tzinfo is UTC
    plus3 = datetime(2026, 7, 1, 13, 35, tzinfo=timezone(timedelta(hours=3)))  # a GMT+3 server
    assert Timeframe.H1.last_completed_bar(plus3).close_time == utc(2026, 7, 1, 10, 0)
    naive = datetime(2026, 3, 10, 10, 35)  # noqa: DTZ001 - deliberately naive
    with pytest.raises(NaiveDatetimeError):
        Timeframe.H1.last_completed_bar(naive)
    with pytest.raises(NaiveDatetimeError):
        Timeframe.H1.bar_close_time(naive)
