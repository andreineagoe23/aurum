"""PolicyRiskEngine: sizing with costs, and every limit rejects on its own."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from aurum.core.enums import AccountTradeMode, DealEntry, OrderType, Side
from aurum.core.models import (
    AccountSnapshot,
    DealRecord,
    OrderState,
    PositionState,
    Signal,
    Tick,
)
from aurum.data.market_hours import MarketHours
from aurum.risk.base import RiskContext
from aurum.risk.policy import PolicyRiskEngine, losing_streak, size_volume
from aurum.sessions import TradingCalendar
from aurum.sessions.news import NewsBlackout
from aurum.settings import AppConfig
from tests.simdata import at, spec

NOW = at("08:00")  # 2026-09-15 09:00 London: london_morning, 04:00 New York (no news)
SL = 1995.10  # 5.00 below the ask 2000.10 = 500 points


@pytest.fixture
def engine(app_config: AppConfig) -> PolicyRiskEngine:
    return PolicyRiskEngine(
        app_config.risk,
        commission_per_lot_side=2.25,
        stop_slippage_points=5,
        news=NewsBlackout(app_config.risk.news_blackout),
    )


@pytest.fixture
def calendar(app_config: AppConfig) -> TradingCalendar:
    return TradingCalendar(app_config.sessions, MarketHours(app_config.data.market))


def account(equity: float = 10_000.0, balance: float | None = None, free: float = 9_000.0):  # type: ignore[no-untyped-def]
    return AccountSnapshot(
        login=1,
        server="demo",
        currency="USD",
        trade_mode=AccountTradeMode.DEMO,
        balance=equity if balance is None else balance,
        equity=equity,
        margin_free=free,
        leverage=500,
        time=NOW,
    )


def ctx(calendar: TradingCalendar, now: datetime = NOW, **kw: object) -> RiskContext:
    sched = calendar.schedule_for(now.date())
    assert sched is not None
    base = RiskContext(
        now=now,
        session=calendar.state_at(now),
        account=account(),
        spec=spec(),
        positions=(),
        orders=(),
        quote=Tick(symbol="XAUUSD", time=now - timedelta(seconds=1), bid=2000.00, ask=2000.10),
        connected=True,
        m15_atr=3.00,
        deals=(),
        day_start=sched.day_start,
        week_start=sched.day_start - timedelta(days=1),  # Monday 2026-09-14
        session_start=sched.sessions[0].start,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def signal(stop: float = SL, side: Side = Side.BUY, now: datetime = NOW) -> Signal:
    return Signal(
        strategy_id="t",
        symbol="XAUUSD",
        side=side,
        time=now,
        data_as_of=now,
        stop_loss=stop,
        reasons=("test",),
    )


def deal(
    pos: int, entry: DealEntry, t: datetime, profit: float = 0.0, commission: float = -0.45
) -> DealRecord:
    return DealRecord(
        deal_id=pos * 10 + (entry is DealEntry.OUT),
        order_id=None,
        position_id=pos,
        symbol="XAUUSD",
        side=Side.BUY if entry is DealEntry.IN else Side.SELL,
        entry=entry,
        volume=0.2,
        price=2000.0,
        time=t,
        commission=commission,
        profit=profit,
        idempotency_key=f"AU{pos}",
        magic=7020261,
    )


def trade(pos: int, opened: datetime, closed: datetime, profit: float) -> list[DealRecord]:
    return [deal(pos, DealEntry.IN, opened), deal(pos, DealEntry.OUT, closed, profit)]


def rejected(engine: PolicyRiskEngine, c: RiskContext, s: Signal | None = None) -> str:
    d = engine.evaluate(s or signal(), c)
    assert not d.approved
    return "; ".join(d.reasons)


# ------------------------------------------------------------------------------ sizing


def test_sizing_counts_commission_and_stop_slippage(
    engine: PolicyRiskEngine, calendar: TradingCalendar
) -> None:
    d = engine.evaluate(signal(), ctx(calendar))
    assert d.approved, d.reasons
    # budget 25.00; per lot 500 x 1.00 + 2 x 2.25 + 5 points x 1.00 = 509.50 -> 0.049 -> 0.04
    assert d.volume == 0.04
    assert d.risk_amount == pytest.approx(20.38)
    assert d.stop_distance_points == pytest.approx(500)


def test_size_volume_floors_to_step_and_caps_at_max() -> None:
    lots, per_lot = size_volume(
        budget=1000.0,
        stop_distance=1.0,
        spec=spec(),
        commission_per_lot_side=0.0,
        stop_slippage_points=0,
    )
    assert (lots, per_lot) == (10.0, 100.0)
    huge, _ = size_volume(
        budget=1e9,
        stop_distance=1.0,
        spec=spec(),
        commission_per_lot_side=0,
        stop_slippage_points=0,
    )
    assert huge == 100.0  # volume_max


def test_too_small_for_volume_min(engine: PolicyRiskEngine, calendar: TradingCalendar) -> None:
    # 0.25% of 1000 = 2.50 < one 0.01 lot of a 500-point stop (5.10)
    assert "< volume_min 0.01" in rejected(engine, ctx(calendar, account=account(1000.0)))


def test_insufficient_free_margin(engine: PolicyRiskEngine, calendar: TradingCalendar) -> None:
    # 0.04 lots x 100 oz x 2000.10 / 500 = 16.00 margin
    assert "free margin" in rejected(engine, ctx(calendar, account=account(free=10.0)))


def test_stop_must_be_beyond_the_executable_price(
    engine: PolicyRiskEngine, calendar: TradingCalendar
) -> None:
    assert "not beyond reference price 2000.1" in rejected(
        engine, ctx(calendar), signal(stop=2000.20)
    )


# ------------------------------------------------------------------------------ gates


def test_session_cutoff_and_news(engine: PolicyRiskEngine, calendar: TradingCalendar) -> None:
    late = at("18:30")  # 30 min before the 19:00 UTC cutoff, < 45
    assert "before cutoff" in rejected(engine, ctx(calendar, late), signal(now=late))
    nfp = at("12:30")  # 08:30 New York
    assert "news blackout 08:20-08:45" in rejected(engine, ctx(calendar, nfp), signal(now=nfp))
    closed = at("04:00")
    assert "no new entries" in rejected(engine, ctx(calendar, closed), signal(now=closed))


def test_market_data_gates(engine: PolicyRiskEngine, calendar: TradingCalendar) -> None:
    old = Tick(symbol="XAUUSD", time=NOW - timedelta(seconds=30), bid=2000.0, ask=2000.10)
    assert "stale quote: 30.0 s old" in rejected(engine, ctx(calendar, quote=old))
    assert "broker disconnected" in rejected(engine, ctx(calendar, connected=False))
    assert "no quote" in rejected(engine, ctx(calendar, quote=None))
    wide = Tick(symbol="XAUUSD", time=NOW, bid=2000.0, ask=2000.25)
    assert "spread 25 points > 20" in rejected(engine, ctx(calendar, quote=wide))
    assert "x M15 ATR 0.50" in rejected(engine, ctx(calendar, m15_atr=0.50))  # 0.10 > 0.075
    assert "M15 ATR unavailable" in rejected(engine, ctx(calendar, m15_atr=None))


def test_uncalibrated_spread_cap_rejects(app_config: AppConfig, calendar: TradingCalendar) -> None:
    risk = app_config.risk.model_copy(update={"max_spread_points": None})
    engine = PolicyRiskEngine(
        risk,
        commission_per_lot_side=2.25,
        stop_slippage_points=5,
        news=NewsBlackout(risk.news_blackout),
    )
    assert "not calibrated" in rejected(engine, ctx(calendar))


def test_one_position_at_a_time(engine: PolicyRiskEngine, calendar: TradingCalendar) -> None:
    pos = PositionState(
        position_id=1,
        symbol="XAUUSD",
        side=Side.BUY,
        volume=0.1,
        open_price=2000.0,
        open_time=NOW,
        stop_loss=1990.0,
    )
    order = OrderState(
        order_id=2,
        symbol="XAUUSD",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        volume=0.1,
        price=1990.0,
        stop_loss=1980.0,
        created_at=NOW,
    )
    assert "one position at a time" in rejected(engine, ctx(calendar, positions=(pos,)))
    assert "one position at a time" in rejected(engine, ctx(calendar, orders=(order,)))


# ------------------------------------------------------------------------------ limits


def test_max_trades_per_day(engine: PolicyRiskEngine, calendar: TradingCalendar) -> None:
    day = [d for n in range(3) for d in trade(n, at("07:00"), at("07:10"), 1.0)]
    assert "3 trades today >= max 3" in rejected(engine, ctx(calendar, deals=day))
    yesterday = [
        d
        for n in range(3)
        for d in trade(n, at("07:00") - timedelta(days=1), at("07:10") - timedelta(days=1), 1.0)
    ]
    assert engine.evaluate(signal(), ctx(calendar, deals=yesterday)).approved


def test_daily_loss_counts_open_pnl(engine: PolicyRiskEngine, calendar: TradingCalendar) -> None:
    # Start-of-day balance = 9,950 + 49.10 = 9,999.10; limit 1% = 99.99.
    closed = trade(1, at("06:10"), at("06:20"), -49.10 + 0.90)  # net -49.10 after commission
    acct = account(equity=9_950.0, balance=9_950.0)
    assert engine.evaluate(signal(), ctx(calendar, deals=closed, account=acct)).approved
    losing = PositionState(
        position_id=9,
        symbol="XAUUSD",
        side=Side.BUY,
        volume=0.2,
        open_price=2000.0,
        open_time=NOW,
        stop_loss=1990.0,
        unrealized_pnl=-51.0,
    )
    why = rejected(engine, ctx(calendar, deals=closed, account=acct, positions=(losing,)))
    assert "daily loss 100.10 >= limit 99.99 (1.0%)" in why


def test_weekly_loss_limit(engine: PolicyRiskEngine, calendar: TradingCalendar) -> None:
    monday = at("13:00") - timedelta(days=1)
    week = trade(1, monday, monday + timedelta(minutes=5), -250.0)  # net -250.90
    acct = account(equity=9_749.10, balance=9_749.10)
    why = rejected(engine, ctx(calendar, deals=week, account=acct))
    assert "weekly loss 250.90 >= limit 250.00 (2.5%)" in why
    assert "daily loss" not in why  # it was yesterday


def test_consecutive_losses_lock_the_rest_of_the_session(
    engine: PolicyRiskEngine, calendar: TradingCalendar
) -> None:
    two = trade(1, at("06:05"), at("06:30"), -5.0) + trade(2, at("06:40"), at("07:00"), -5.0)
    assert "2 consecutive losses this session" in rejected(engine, ctx(calendar, deals=two))
    # The afternoon window starts a new count.
    pm = at("13:00")  # 14:00 London (ldn_ny_overlap), 09:00 New York
    sched = calendar.schedule_for(pm.date())
    assert sched is not None
    after = ctx(calendar, pm, deals=two, session_start=sched.sessions[1].start)
    decision = engine.evaluate(signal(now=pm), after)
    assert decision.approved, decision.reasons
    assert losing_streak(two, since=sched.sessions[1].start) == 0


def test_a_win_breaks_the_streak() -> None:
    deals = (
        trade(1, at("06:05"), at("06:10"), -5.0)
        + trade(2, at("06:15"), at("06:20"), 9.0)
        + trade(3, at("06:25"), at("06:30"), -5.0)
    )
    assert losing_streak(deals, since=at("06:00")) == 1
    # A trade that only breaks even after commission is a loss.
    assert losing_streak(trade(4, at("06:35"), at("06:40"), 0.90), since=at("06:00")) == 0
    assert losing_streak(trade(5, at("06:35"), at("06:40"), 0.89), since=at("06:00")) == 1


def test_every_failure_is_listed(engine: PolicyRiskEngine, calendar: TradingCalendar) -> None:
    d = engine.evaluate(signal(), ctx(calendar, connected=False, quote=None, m15_atr=None))
    assert not d.approved
    assert {
        "broker disconnected",
        "no quote",
        "no reference price to measure the stop distance",
    } <= set(d.reasons)
