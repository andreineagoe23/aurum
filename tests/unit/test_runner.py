"""Backtest runner: wake-up schedule and the replay loop."""

from datetime import date, timedelta

from aurum.backtest.runner import BacktestResult, run_backtest, schedule
from aurum.core.enums import ExitReason, Side
from aurum.data.market_hours import MarketHours
from aurum.execution.engine import Engine
from aurum.risk.fixed import FixedVolumeRisk
from aurum.sessions import TradingCalendar
from aurum.settings import AppConfig
from tests.simdata import DAY, Plan, ScriptedStrategy, at, flat_m1, market, sim


def calendar(cfg: AppConfig) -> TradingCalendar:
    return TradingCalendar(cfg.sessions, MarketHours(cfg.data.market))


def test_schedule_covers_phase_boundaries_and_m5_grid(app_config: AppConfig) -> None:
    times = list(schedule(calendar(app_config), DAY, DAY))
    assert times == sorted(set(times))
    assert times[0] == at("05:30")  # pre-session 06:30 London
    assert times[-1] == at("19:00")  # force-flat 20:00 London
    for boundary in ("06:00", "10:00", "12:00", "15:30", "18:30"):
        assert at(boundary) in times
    assert at("08:05") in times
    assert len([t for t in times if t.minute % 5]) == 0


def test_schedule_skips_closed_dates_and_meets_early_closes(app_config: AppConfig) -> None:
    cal = calendar(app_config)
    assert list(schedule(cal, date(2026, 4, 3), date(2026, 4, 3))) == []  # Good Friday
    early = list(schedule(cal, date(2026, 7, 3), date(2026, 7, 3)))
    assert early[-1] == at("16:46", date(2026, 7, 3))  # 13:01 New York - 15 minutes


def replay(cfg: AppConfig, plans: dict, last_bar: str = "20:59") -> BacktestResult:  # type: ignore[type-arg]
    mkt = market(m1=flat_m1("05:00", last_bar))
    broker, clock = sim(mkt, start="00:00", latency_ms=200)
    cal = calendar(cfg)
    engine = Engine(
        broker=broker,
        strategy=ScriptedStrategy(plans=plans),
        risk=FixedVolumeRisk(0.10),
        calendar=cal,
        symbol="XAUUSD",
        magic=1,
    )
    return run_backtest(
        engine=engine, broker=broker, market=mkt, calendar=cal, clock=clock, first=DAY, last=DAY
    )


def test_max_hold_deadline_gets_its_own_wake_up(app_config: AppConfig) -> None:
    result = replay(
        app_config, {at("08:00"): Plan(Side.BUY, 1990.0, max_hold=timedelta(minutes=7))}
    )
    (trade,) = result.trades
    # Not the next M5 close (08:10). Bar day: stamped at arrival (+200 ms latency).
    assert trade.exit_time == at("08:07:00.200")
    assert result.exit_reasons[trade.position_id] is ExitReason.TIMEOUT
    assert result.open_at_end == 0
    assert set(result.signals) == {trade.idempotency_key}


def test_equity_curve_and_force_flat(app_config: AppConfig) -> None:
    result = replay(app_config, {at("15:00"): Plan(Side.BUY, 1990.0)})
    (trade,) = result.trades
    assert result.exit_reasons[trade.position_id] is ExitReason.FORCE_FLAT
    times = [p.time for p in result.equity]
    assert times == sorted(times)
    assert times[0] == at("05:30")
    holding = [p for p in result.equity if p.open_positions]
    assert holding[0].time == at("15:00")
    assert holding[-1].time < at("19:00")
    assert result.equity[-1].balance == result.equity[-1].equity


def test_exposure_left_after_cutoff_is_reported(app_config: AppConfig) -> None:
    # No quotes after 18:59: the force-flat close at 19:00 cannot execute. The runner keeps
    # waking every 5 minutes while exposed, then reports the position as still open.
    result = replay(app_config, {at("15:00"): Plan(Side.BUY, 1990.0)}, last_bar="18:59")
    assert result.trades == []
    assert result.open_at_end == 1
    wakes_after_cutoff = [p.time for p in result.equity if p.time > at("19:00")]
    assert wakes_after_cutoff[:2] == [at("19:05"), at("19:10")]
