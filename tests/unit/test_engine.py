"""Engine: session gating, exits (timeout, invalidation, force-flat), ownership, journal.

Day: 2026-09-15 (BST). Sessions in UTC: london_morning 06:00-10:00, ldn_ny_overlap
12:00-15:30, no new entries from 18:30, force-flat 19:00.
"""

from dataclasses import dataclass
from datetime import timedelta

import pytest

from aurum.backtest.broker import SimBroker
from aurum.backtest.market import HistoricalMarket
from aurum.core.clock import ManualClock
from aurum.core.enums import ExecutionStatus, ExitReason, OrderType, Side
from aurum.core.models import OrderIntent, Signal
from aurum.core.timeframes import Timeframe
from aurum.data.market_hours import MarketHours
from aurum.execution.engine import Engine, JournalEvent
from aurum.features.view import MarketView
from aurum.risk.fixed import FixedVolumeRisk
from aurum.sessions import TradingCalendar
from aurum.settings import AppConfig
from tests.factories import approve, signal
from tests.simdata import Plan, ScriptedStrategy, at, flat_m1, market, sim, spec

MAGIC = 7020261


@dataclass
class Rig:
    engine: Engine
    broker: SimBroker
    clock: ManualClock
    market: HistoricalMarket
    strategy: ScriptedStrategy

    def step(self, hms: str) -> None:
        t = at(hms)
        self.clock.set(t)
        self.broker.advance_to(t)
        self.engine.step(self.market.store.view_at(t))

    def events(self) -> list[JournalEvent]:
        return [e.event for e in self.engine.journal if e.event is not JournalEvent.SESSION]


def rig(
    cfg: AppConfig,
    strategy: ScriptedStrategy | None = None,
    *,
    start: str = "05:00",
    **sim_kwargs: object,
) -> Rig:
    mkt = market(m1=flat_m1("05:00", "20:59"))
    broker, clock = sim(mkt, start=start, **sim_kwargs)  # type: ignore[arg-type]
    strat = strategy or ScriptedStrategy()
    engine = Engine(
        broker=broker,
        strategy=strat,
        risk=FixedVolumeRisk(0.10, 2.25),
        calendar=TradingCalendar(cfg.sessions, MarketHours(cfg.data.market)),
        symbol="XAUUSD",
        magic=MAGIC,
    )
    return Rig(engine, broker, clock, mkt, strat)


def buy(sl: float = 1990.0, **kw: object) -> Plan:
    return Plan(Side.BUY, sl, **kw)  # type: ignore[arg-type]


def test_entry_is_filled_and_journaled(app_config: AppConfig) -> None:
    r = rig(app_config, ScriptedStrategy(plans={at("08:00"): buy(take_profit=2010.0)}))
    r.step("08:00")
    (pos,) = r.broker.positions()
    assert pos.magic == MAGIC
    assert pos.idempotency_key is not None
    assert pos.idempotency_key.startswith("AU")
    assert r.events() == [JournalEvent.SIGNAL, JournalEvent.ORDER_FILLED]
    assert r.engine.signal_for(pos.idempotency_key) is not None


def test_strategy_not_asked_outside_open_or_off_timeframe(app_config: AppConfig) -> None:
    strat = ScriptedStrategy(
        plans={at("11:00"): buy(), at("08:05"): buy()}, timeframe=Timeframe.M15
    )
    r = rig(app_config, strat)
    r.step("08:05")  # OPEN, but not an M15 close
    r.step("11:00")  # M15 close, but between sessions (NO_NEW_ENTRIES)
    assert strat.seen == []
    assert r.broker.positions() == []


def test_timeout_closes_at_max_hold(app_config: AppConfig) -> None:
    r = rig(app_config, ScriptedStrategy(plans={at("08:00"): buy(max_hold=timedelta(minutes=10))}))
    r.step("08:00")
    assert r.engine.next_deadline_after(at("08:00")) == at("08:10")
    r.step("08:05")
    assert len(r.broker.positions()) == 1
    r.step("08:10")
    assert r.broker.positions() == []
    (trade,) = r.broker.trades
    assert r.engine.exit_reasons[trade.position_id] is ExitReason.TIMEOUT
    assert trade.exit_time == at("08:10")


def test_invalidation_closes(app_config: AppConfig) -> None:
    strat = ScriptedStrategy(
        plans={at("08:00"): buy()}, invalidate={at("08:15"): "m15 close 1995.00 < 1996.00"}
    )
    r = rig(app_config, strat)
    for t in ("08:00", "08:05", "08:10", "08:15"):
        r.step(t)
    (trade,) = r.broker.trades
    assert r.engine.exit_reasons[trade.position_id] is ExitReason.INVALIDATION
    close = next(e for e in r.engine.journal if e.event is JournalEvent.CLOSE)
    assert close.detail == "m15 close 1995.00 < 1996.00"


def test_force_flat_closes_at_cutoff(app_config: AppConfig) -> None:
    r = rig(app_config, ScriptedStrategy(plans={at("15:00"): buy()}))
    r.step("15:00")
    r.step("18:55")  # NO_NEW_ENTRIES / EXIT_ONLY: managed, not closed
    assert len(r.broker.positions()) == 1
    r.step("19:00")
    assert r.broker.positions() == []
    (trade,) = r.broker.trades
    assert r.engine.exit_reasons[trade.position_id] is ExitReason.FORCE_FLAT
    assert trade.exit_time == at("19:00")


def test_pending_entry_cancelled_when_entries_stop(app_config: AppConfig) -> None:
    plan = buy(sl=1980.0, entry_type=OrderType.LIMIT, entry_price=1990.0)
    r = rig(app_config, ScriptedStrategy(plans={at("15:25"): plan}))
    r.step("15:25")
    assert len(r.broker.orders()) == 1
    r.step("15:30")  # overlap ends: NO_NEW_ENTRIES
    assert r.broker.orders() == []
    assert JournalEvent.CANCEL in r.events()


def test_one_position_at_a_time_is_enforced_by_risk(app_config: AppConfig) -> None:
    r = rig(app_config, ScriptedStrategy(plans={at("08:00"): buy(), at("08:05"): buy()}))
    r.step("08:00")
    r.step("08:05")
    assert len(r.broker.positions()) == 1
    rejected = next(e for e in r.engine.journal if e.event is JournalEvent.RISK_REJECTED)
    assert "one position at a time" in rejected.detail


def test_broker_rejection_is_journaled(app_config: AppConfig) -> None:
    r = rig(
        app_config,
        ScriptedStrategy(plans={at("08:00"): buy(sl=1995.0)}),
        symbol_spec=spec(stops_level=1000),
    )
    r.step("08:00")
    rejected = next(e for e in r.engine.journal if e.event is JournalEvent.ORDER_REJECTED)
    assert "invalid stops" in rejected.detail
    assert r.broker.positions() == []


def owned_intent(magic: int = MAGIC) -> OrderIntent:
    sig = signal(Side.BUY, t=at("18:00"), stop_loss=1990.0)
    return OrderIntent.from_signal(sig, approve(sig, 0.10), created_at=at("18:00"), magic=magic)


def test_restart_inside_force_flat_flattens_owned_only(app_config: AppConfig) -> None:
    r = rig(app_config, start="18:00")
    assert r.broker.send_order(owned_intent()).status is ExecutionStatus.FILLED
    manual = owned_intent(magic=0).model_copy(update={"idempotency_key": "manual_trade_1"})
    assert r.broker.send_order(manual).status is ExecutionStatus.FILLED
    r.step("19:30")  # first step after a "restart", already past the cutoff
    first = r.engine.journal[0]
    assert first.event is JournalEvent.SESSION
    assert first.detail == "start -> FORCE_FLAT"
    remaining = r.broker.positions()
    assert [p.magic for p in remaining] == [0]  # the manual trade is never touched
    assert r.engine.exit_reasons == {r.broker.trades[0].position_id: ExitReason.FORCE_FLAT}


def test_close_without_quote_is_retried(app_config: AppConfig) -> None:
    r = rig(app_config, start="18:00")
    r.broker.send_order(owned_intent())
    r.step("21:05")  # daily break: no quote, the close fails and stays open
    assert len(r.broker.positions()) == 1
    assert JournalEvent.CLOSE_FAILED in r.events()


def test_signal_for_another_instant_is_a_bug(app_config: AppConfig) -> None:
    class Late(ScriptedStrategy):
        def on_bar(self, view: MarketView) -> Signal | None:
            return signal(Side.BUY, t=view.now - timedelta(minutes=5))

    r = rig(app_config, Late())
    with pytest.raises(ValueError, match="expected XAUUSD at"):
        r.step("08:00")


def test_strategy_never_sees_unfinished_bars(app_config: AppConfig) -> None:
    strat = ScriptedStrategy(plans={at("08:00"): buy()}, invalidate={})
    r = rig(app_config, strat)
    for minute in range(0, 60, 5):
        r.step(f"08:{minute:02d}")
    assert strat.seen
    assert all(last is not None and last <= now for now, last in strat.seen)
