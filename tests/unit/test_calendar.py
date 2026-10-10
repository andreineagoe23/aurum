"""TradingCalendar: session phases with closed dates, early closes and the daily break."""

from datetime import UTC, date, datetime, time

import pytest

from aurum.core.enums import SessionPhase
from aurum.data.market_hours import MarketHours
from aurum.sessions import SessionStateMachine, TradingCalendar
from aurum.settings import AppConfig, EarlyClose, MarketHoursConfig, SessionsConfig


def calendar(
    cfg: AppConfig,
    *,
    buffer: int | None = None,
    sessions: dict[str, object] | None = None,
    early: dict[date, str] | None = None,
) -> TradingCalendar:
    s = cfg.sessions
    update: dict[str, object] = dict(sessions or {})
    if buffer is not None:
        update["market_close_buffer_minutes"] = buffer
    s = SessionsConfig.model_validate({**s.model_dump(), **update})
    market: MarketHoursConfig = cfg.data.market
    if early:
        closes = dict(market.early_closes)
        closes.update({d: EarlyClose(close=c, reason="test") for d, c in early.items()})  # type: ignore[arg-type]
        market = market.model_copy(update={"early_closes": closes})
    return TradingCalendar(s, MarketHours(market))


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_normal_day_keeps_configured_cutoff(app_config: AppConfig) -> None:
    sched = calendar(app_config).schedule_for(date(2026, 9, 15))  # BST, EDT
    assert sched is not None
    assert sched.force_flat_at == utc(2026, 9, 15, 19, 0)  # 20:00 London
    assert sched.no_new_entries_after == utc(2026, 9, 15, 18, 30)
    assert sched.market_close == utc(2026, 9, 15, 20, 59)  # 16:59 New York
    assert not sched.early_close
    assert [w.name for w in sched.sessions] == ["london_morning", "ldn_ny_overlap"]


@pytest.mark.parametrize(
    ("buffer", "cutoff"),
    [(None, utc(2026, 7, 3, 16, 46)), (0, utc(2026, 7, 3, 17, 1))],
)
def test_early_close_moves_cutoff_by_buffer(
    app_config: AppConfig, buffer: int | None, cutoff: datetime
) -> None:
    # 2026-07-03: early close 13:01 New York = 17:01 UTC = 18:01 London.
    assert app_config.sessions.market_close_buffer_minutes == 15  # the default (None)
    cal = calendar(app_config, buffer=buffer)
    sched = cal.schedule_for(date(2026, 7, 3))
    assert sched is not None
    assert sched.early_close
    assert sched.force_flat_at == cutoff
    assert sched.no_new_entries_after == cutoff  # 19:30 London is later than the cutoff
    assert cal.state_at(cutoff).phase is SessionPhase.FORCE_FLAT
    before = cal.state_at(cutoff.replace(minute=cutoff.minute - 1))
    assert before.phase is SessionPhase.NO_NEW_ENTRIES  # overlap ended 16:30 London
    assert before.force_flat_at == cutoff


def test_closed_date_is_closed_all_day(app_config: AppConfig) -> None:
    cal = calendar(app_config)
    assert cal.schedule_for(date(2026, 4, 3)) is None  # Good Friday
    state = cal.state_at(utc(2026, 4, 3, 9, 0))
    assert state.phase is SessionPhase.CLOSED
    assert not state.entries_allowed
    assert state.must_be_flat
    # A restart after an early-close cutoff starts in FORCE_FLAT.
    first = SessionStateMachine(calendar(app_config)).update(utc(2026, 7, 3, 17, 0))
    assert first is not None
    assert first.phase is SessionPhase.FORCE_FLAT


def test_daily_break_caps_a_late_force_flat(app_config: AppConfig) -> None:
    # force_flat_at 23:30 London would be inside the daily break (16:59-18:00 New York =
    # 21:59-23:00 London): the calendar flattens at 16:44 New York instead.
    cal = calendar(app_config, sessions={"no_new_entries_after": "23:00", "force_flat_at": "23:30"})
    sched = cal.schedule_for(date(2026, 9, 15))
    assert sched is not None
    assert sched.force_flat_at == utc(2026, 9, 15, 20, 44)
    assert sched.no_new_entries_after == sched.force_flat_at  # EXIT_ONLY has zero length
    assert cal.state_at(utc(2026, 9, 15, 20, 43)).phase is SessionPhase.NO_NEW_ENTRIES
    assert cal.state_at(utc(2026, 9, 15, 20, 44)).phase is SessionPhase.FORCE_FLAT
    # In the break itself the day is already FORCE_FLAT.
    assert cal.state_at(utc(2026, 9, 15, 21, 30)).phase is SessionPhase.FORCE_FLAT


def test_early_close_clips_and_drops_sessions(app_config: AppConfig) -> None:
    # Winter: 10:00 New York = 15:00 UTC/London -> cutoff 14:45; overlap clipped to 13:00-14:45.
    cal = calendar(app_config, early={date(2026, 12, 2): "10:00", date(2026, 12, 3): "08:00"})
    clipped = cal.schedule_for(date(2026, 12, 2))
    assert clipped is not None
    assert [(w.name, w.end) for w in clipped.sessions] == [
        ("london_morning", utc(2026, 12, 2, 11, 0)),
        ("ldn_ny_overlap", utc(2026, 12, 2, 14, 45)),
    ]
    assert cal.state_at(utc(2026, 12, 2, 14, 44)).phase is SessionPhase.OPEN
    assert cal.state_at(utc(2026, 12, 2, 14, 45)).phase is SessionPhase.FORCE_FLAT
    # 08:00 New York = 13:00 London -> cutoff 12:45: the overlap session disappears.
    dropped = cal.schedule_for(date(2026, 12, 3))
    assert dropped is not None
    assert [w.name for w in dropped.sessions] == ["london_morning"]
    assert cal.state_at(utc(2026, 12, 3, 12, 50)).phase is SessionPhase.FORCE_FLAT


def test_no_session_left_means_no_entries(app_config: AppConfig) -> None:
    # Early close 01:00 New York (06:00 London): nothing opens; flat from 05:45 London.
    cal = calendar(app_config, early={date(2026, 12, 4): "01:00"})
    sched = cal.schedule_for(date(2026, 12, 4))
    assert sched is not None
    assert sched.sessions == ()
    phases = {
        cal.state_at(datetime.combine(date(2026, 12, 4), time(h, m), tzinfo=UTC)).phase
        for h in range(24)
        for m in (0, 30)
    }
    assert SessionPhase.OPEN not in phases
    assert cal.state_at(utc(2026, 12, 4, 5, 44)).phase is SessionPhase.CLOSED
    assert cal.state_at(utc(2026, 12, 4, 5, 45)).phase is SessionPhase.FORCE_FLAT
