"""Phase 4 fault injection: the engine with the real risk policy over a SimBroker whose
connection fails on demand. Each test asserts that limits hold, nothing is ever doubled, and
force-flat still happens.

Day 2026-09-15 (BST): london_morning 06:00-10:00 UTC, overlap 12:00-15:30, cutoff 19:00.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest

from aurum.backtest.broker import SimBroker
from aurum.backtest.market import HistoricalMarket
from aurum.core.clock import ManualClock
from aurum.core.enums import ExitReason, Side
from aurum.core.timeframes import Timeframe
from aurum.data.market_hours import MarketHours
from aurum.execution.engine import Engine, JournalEvent
from aurum.execution.intents import IntentStatus, IntentStore
from aurum.execution.killswitch import KillSwitch
from aurum.risk.policy import PolicyRiskEngine
from aurum.sessions import TradingCalendar
from aurum.sessions.news import NewsBlackout
from aurum.settings import AppConfig
from tests.fault.faulty import FaultyBroker, as_port
from tests.simdata import DAY, BarRow, Plan, ScriptedStrategy, TickRow, at, flat_m1, market, sim

MAGIC = 7020261


def m15_rows() -> list[BarRow]:
    """M15 bars with a 6.00 range (ATR 6.00): the spread/ATR gate passes."""
    rows: list[BarRow] = []
    for minutes in range(0, 21 * 60, 15):
        rows.append((f"{minutes // 60:02d}:{minutes % 60:02d}", 2000.0, 2003.0, 1997.0, 2000.0, 10))
    return rows


@dataclass
class Rig:
    engine: Engine
    sim: SimBroker
    broker: FaultyBroker
    clock: ManualClock
    market: HistoricalMarket
    cfg: AppConfig
    store: IntentStore
    kill: KillSwitch | None = None
    plans: dict = field(default_factory=dict)  # type: ignore[type-arg]

    def step(self, hms: str) -> None:
        t = at(hms)
        self.clock.set(t)
        self.sim.advance_to(t)  # the market moves on whether or not we are connected
        self.engine.step(self.market.store.view_at(t))

    def restart(self, store: IntentStore | None = None) -> Engine:
        """A new engine process on the same broker (and, if given, the same store)."""
        self.store = store or IntentStore()
        self.engine = make_engine(self.cfg, self.broker, self.plans, self.store, self.kill)
        return self.engine

    def events(self, *kinds: JournalEvent) -> list[str]:
        return [e.detail for e in self.engine.journal if e.event in kinds]


def make_engine(
    cfg: AppConfig,
    broker: FaultyBroker,
    plans: dict,  # type: ignore[type-arg]
    store: IntentStore,
    kill: KillSwitch | None,
    max_age: timedelta = timedelta(seconds=60),
) -> Engine:
    return Engine(
        broker=as_port(broker),
        strategy=ScriptedStrategy(plans=plans),
        risk=PolicyRiskEngine(
            cfg.risk,
            commission_per_lot_side=2.25,
            stop_slippage_points=5,
            news=NewsBlackout(cfg.risk.news_blackout),
        ),
        calendar=TradingCalendar(cfg.sessions, MarketHours(cfg.data.market)),
        symbol="XAUUSD",
        magic=MAGIC,
        store=store,
        kill_switch=kill,
        max_intent_age=max_age,
    )


def rig(
    cfg: AppConfig,
    plans: dict,  # type: ignore[type-arg]
    *,
    ticks: list[TickRow] | None = None,
    m1: list[BarRow] | None = None,
    store: IntentStore | None = None,
    kill: KillSwitch | None = None,
    max_age: timedelta = timedelta(seconds=60),
) -> Rig:
    higher = {Timeframe.M15: m15_rows()}
    mkt = (
        market(ticks={DAY: ticks}, higher=higher)
        if ticks
        else market(m1=m1 or flat_m1("05:00", "20:59"), higher=higher)
    )
    inner, clock = sim(mkt, start="05:00", market_hours=MarketHours(cfg.data.market))
    broker = FaultyBroker(inner)
    store = store or IntentStore()
    engine = make_engine(cfg, broker, plans, store, kill, max_age)
    return Rig(engine, inner, broker, clock, mkt, cfg, store, kill, plans)


def buy(**kw: object) -> Plan:
    return Plan(Side.BUY, 1990.0, **kw)  # type: ignore[arg-type]


# ------------------------------------------------------------------ disconnect mid-order


def test_reply_lost_mid_order_is_reconciled_never_doubled(app_config: AppConfig) -> None:
    r = rig(app_config, {at("08:00"): buy(), at("08:05"): buy()})
    r.broker.lose_next_reply = True
    r.step("08:00")
    assert r.events(JournalEvent.ORDER_UNKNOWN)  # the engine did not see the fill
    (pos,) = r.sim.positions()  # ... but the broker has it
    record = r.store.get(pos.idempotency_key or "")
    assert record is not None
    assert record.status is IntentStatus.FILLED  # reconciled in the same step
    r.step("08:05")
    assert r.broker.sends == 1
    assert len(r.sim.positions()) == 1
    assert any("one position at a time" in d for d in r.events(JournalEvent.RISK_REJECTED))


def test_reply_lost_then_offline_blocks_entries_until_reconciled(app_config: AppConfig) -> None:
    r = rig(app_config, {at("08:00"): buy(max_hold=timedelta(minutes=20)), at("08:10"): buy()})
    r.broker.lose_next_reply = True

    real_send = r.broker.send_order

    def send_then_drop(intent):  # type: ignore[no-untyped-def]
        try:
            return real_send(intent)
        finally:
            r.broker.offline = True  # the line stays down after the lost reply

    r.broker.send_order = send_then_drop  # type: ignore[method-assign]
    r.step("08:00")
    r.step("08:05")
    assert len(r.events(JournalEvent.DISCONNECTED)) == 2
    r.broker.offline = False
    r.broker.send_order = real_send  # type: ignore[method-assign]
    r.step("08:10")  # back online: reconcile first, then the 08:10 signal meets one position
    assert r.broker.sends == 1
    assert any(d.startswith("confirmed") for d in r.events(JournalEvent.RECONCILE))
    r.step("08:20")  # the restored signal still times out at 08:00 + 20 min
    assert list(r.engine.exit_reasons.values()) == [ExitReason.TIMEOUT]


def test_send_that_never_arrived_expires_and_trading_resumes(app_config: AppConfig) -> None:
    r = rig(app_config, {at("08:00"): buy(), at("08:05"): buy()})
    r.broker.fail_next_send = True
    r.step("08:00")
    assert r.sim.positions() == []
    records = r.store.records(frozenset({IntentStatus.UNKNOWN}))
    assert len(records) == 1  # < 60 s old: not yet known to be absent
    r.step("08:05")  # reconcile expires it (not at the broker after 5 min), then 08:05 trades
    assert r.store.get(records[0].key).status is IntentStatus.EXPIRED  # type: ignore[union-attr]
    assert r.broker.sends == 1
    assert len(r.sim.positions()) == 1


def test_unresolved_outcome_blocks_a_new_entry(app_config: AppConfig) -> None:
    # With a 10-minute intent age, the failed 08:00 send is still unresolved at 08:05.
    r = rig(app_config, {at("08:00"): buy(), at("08:05"): buy()}, max_age=timedelta(minutes=10))
    r.broker.fail_next_send = True
    r.step("08:00")
    r.step("08:05")
    assert any("unresolved" in d for d in r.events(JournalEvent.ENTRY_BLOCKED))
    assert r.broker.sends == 0
    assert r.sim.positions() == []


# ------------------------------------------------------------------ stale data


def test_stale_quote_rejects_entry(app_config: AppConfig) -> None:
    tape: list[TickRow] = [
        ("07:55:00.000", 1999.90, 2000.00),
        ("07:59:30.000", 2000.00, 2000.10),  # last quote 30 s before the decision
        ("08:01:00.000", 2000.00, 2000.10),
    ]
    r = rig(app_config, {at("08:00"): buy()}, ticks=tape)
    r.step("08:00")
    assert any("stale quote: 30.0 s old" in d for d in r.events(JournalEvent.RISK_REJECTED))
    assert r.sim.positions() == []


# ------------------------------------------------------------------ restarts


def test_restart_with_open_position_keeps_its_timeout(
    app_config: AppConfig, tmp_path: Path
) -> None:
    path = tmp_path / "state" / "intents.sqlite"
    r = rig(app_config, {at("08:00"): buy(max_hold=timedelta(minutes=30))}, store=IntentStore(path))
    r.step("08:00")
    (pos,) = r.sim.positions()
    r.store.close()
    engine = r.restart(IntentStore(path))  # new process, same SQLite file
    assert engine.signal_for(pos.idempotency_key) is not None
    r.step("08:15")
    assert len(r.sim.positions()) == 1
    # Signal-to-exit (D6 b): the deadline is Signal.time + 30 min = 08:30. Down past it, the
    # first step after it closes the position; the clock never restarts.
    r.step("08:45")
    assert r.sim.positions() == []
    assert engine.exit_reasons == {pos.position_id: ExitReason.TIMEOUT}


def test_restart_without_the_store_adopts_and_still_force_flats(app_config: AppConfig) -> None:
    r = rig(app_config, {at("15:00"): buy()})
    r.step("15:00")
    (pos,) = r.sim.positions()
    r.restart(IntentStore())  # the state file was lost
    r.step("15:05")
    assert any(d.startswith("adopted") for d in r.events(JournalEvent.RECONCILE))
    r.step("19:00")
    assert r.sim.positions() == []
    assert r.engine.exit_reasons == {pos.position_id: ExitReason.FORCE_FLAT}


def test_restart_inside_force_flat_flattens_first_thing(app_config: AppConfig) -> None:
    r = rig(app_config, {at("15:00"): buy()})
    r.step("15:00")
    r.restart(IntentStore())
    r.step("19:20")  # first step of the new process is already past the cutoff
    assert r.sim.positions() == []


# ------------------------------------------------------------------ cutoff during disconnect


def test_cutoff_during_disconnect_flattens_on_reconnect(app_config: AppConfig) -> None:
    r = rig(app_config, {at("15:00"): buy()})
    r.step("15:00")
    (pos,) = r.sim.positions()
    r.broker.offline = True
    r.step("19:00")
    r.step("19:05")
    assert len(r.events(JournalEvent.DISCONNECTED)) == 2
    assert len(r.sim.positions()) == 1  # nothing could be done while offline
    r.broker.offline = False
    r.step("19:10")
    assert r.sim.positions() == []
    assert r.engine.exit_reasons == {pos.position_id: ExitReason.FORCE_FLAT}


# ------------------------------------------------------------------ kill switch


def test_kill_switch_flattens_and_blocks_until_cleared(
    app_config: AppConfig, tmp_path: Path
) -> None:
    kill = KillSwitch(tmp_path / "KILL")
    plans = {at("08:00"): buy(), at("08:15"): buy(), at("08:25"): buy()}
    r = rig(app_config, plans, kill=kill)
    r.step("08:00")
    (pos,) = r.sim.positions()
    kill.engage("operator test", at("08:10"))
    r.step("08:10")
    assert r.sim.positions() == []
    assert r.engine.exit_reasons == {pos.position_id: ExitReason.KILL_SWITCH}
    r.step("08:15")
    assert r.sim.positions() == []
    assert kill.clear()
    r.step("08:25")
    assert len(r.sim.positions()) == 1


# ------------------------------------------------------------------ limits cannot be bypassed


def test_a_trade_that_would_overrun_the_daily_limit_is_rejected(app_config: AppConfig) -> None:
    """0.9% lost, then a signal: 0.9% + its 0.2% risk could end the day past 1%, so it is
    rejected even though the loss so far is under the limit (owner decision 2026-10-10)."""
    gap = flat_m1("05:00", "08:04") + flat_m1("08:05", "20:59", price=1955.0)
    plans = {at("08:00"): buy(), at("12:05"): Plan(Side.BUY, 1945.0)}
    r = rig(app_config, plans, m1=gap)
    r.step("08:00")  # 0.02 lots, stop 1990 (~20 at risk); at 08:05 it gaps 45 through the stop
    r.step("12:05")  # afternoon window: no losing-streak lockout, 1 trade so far
    (trade,) = r.sim.trades
    assert -(trade.profit + trade.commission) == pytest.approx(90.28)  # 0.90% of 10,000
    assert r.broker.sends == 1
    (why,) = r.events(JournalEvent.RISK_REJECTED)
    assert (
        "daily budget: limit 100.00 (1.0%) - loss to date 90.28 - open risk 0.00 = "
        "remaining 9.72 < new trade risk 20.39"
    ) in why


def test_a_strategy_signalling_all_day_cannot_exceed_the_limits(app_config: AppConfig) -> None:
    """A signal at every M5 close, each held 5 minutes. Flat market: every trade loses its
    costs. The session lockout (2 losses) and max 3 trades/day hold; every sent intent carries
    the approved volume."""
    plans = {
        at("06:00") + timedelta(minutes=5 * n): buy(max_hold=timedelta(minutes=5))
        for n in range(160)
    }
    r = rig(app_config, plans)
    sent = []
    real_send = r.sim.send_order

    def record(intent):  # type: ignore[no-untyped-def]
        sent.append(intent)
        return real_send(intent)

    r.sim.send_order = record  # type: ignore[method-assign]
    t = at("05:30")
    while t <= at("19:00"):
        r.step(t.strftime("%H:%M"))
        t += timedelta(minutes=5)
    assert 1 <= len(sent) <= app_config.risk.max_trades_per_day
    for intent in sent:
        assert intent.risk_decision.approved
        assert intent.volume == intent.risk_decision.volume
    rejected = " ".join(r.events(JournalEvent.RISK_REJECTED))
    assert "consecutive losses this session" in rejected
    assert "trades today >= max 3" in rejected
