from datetime import timedelta
from pathlib import Path

import pytest

from aurum.data.market_hours import MarketHours
from aurum.monitoring.watchdog import Heartbeat, Verdict, check_heartbeat, must_run, read_heartbeat
from aurum.sessions import TradingCalendar
from aurum.settings import AppConfig
from tests.simdata import at

MAX = timedelta(seconds=120)


@pytest.fixture
def calendar(app_config: AppConfig) -> TradingCalendar:
    return TradingCalendar(app_config.sessions, MarketHours(app_config.data.market))


def test_trading_window_runs_from_pre_session_to_half_an_hour_after_cutoff(
    calendar: TradingCalendar,
) -> None:
    assert not must_run(calendar, at("05:29"))  # pre-session starts 05:30 UTC
    assert must_run(calendar, at("05:30"))
    assert must_run(calendar, at("19:29"))  # cutoff 19:00 + 30 min
    assert not must_run(calendar, at("19:30"))
    assert not must_run(calendar, at("12:00") - timedelta(days=2))  # Sunday


def test_heartbeat_round_trip_and_verdicts(tmp_path: Path, calendar: TradingCalendar) -> None:
    path = tmp_path / "state" / "trader.heartbeat.json"
    assert check_heartbeat(path, at("08:00"), max_age=MAX, calendar=calendar).verdict is (
        Verdict.MISSING
    )
    assert check_heartbeat(path, at("04:00"), max_age=MAX, calendar=calendar).verdict is (
        Verdict.IDLE
    )
    Heartbeat(path, "trader").beat(at("08:00"), "step ok")
    beat = read_heartbeat(path)
    assert beat is not None
    assert (beat.time, beat.process, beat.detail) == (at("08:00"), "trader", "step ok")
    ok = check_heartbeat(path, at("08:02"), max_age=MAX, calendar=calendar)
    assert (ok.verdict, ok.alarm) == (Verdict.OK, False)
    stale = check_heartbeat(path, at("08:02:01"), max_age=MAX, calendar=calendar)
    assert (stale.verdict, stale.alarm) == (Verdict.STALE, True)
    after = check_heartbeat(path, at("20:00"), max_age=MAX, calendar=calendar)
    assert after.verdict is Verdict.IDLE
    assert not list(path.parent.glob("*.tmp"))
