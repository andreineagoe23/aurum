"""The decision loop shared by every mode: session -> exits -> strategy -> risk -> broker.

``Engine.step(view)`` is called at decision instants (bar closes and scheduled times) by a
driver: the backtest runner with a ``SimBroker``, the shadow/demo loops with live brokers
later. The engine never knows which; it only talks to a ``BrokerPort``.

Per step, at ``now = view.now``:

1. Update the session state machine (its first update always emits, so a restart inside
   FORCE_FLAT still flattens).
2. Reconcile the intent store with the broker on the first step (startup), after a lost
   connection, while any order outcome is unresolved, and after every step that acted
   (``aurum.execution.reconcile``).
3. Kill switch engaged: close every owned position, cancel every owned order, nothing else.
4. If the phase says flat (CLOSED, PRE_SESSION, FORCE_FLAT): the same, exit reason FORCE_FLAT.
5. Outside OPEN: cancel owned pending entry orders.
6. For each owned position: close it when its signal's ``max_hold`` has elapsed (TIMEOUT) or
   the strategy reports the setup invalid (INVALIDATION). ``max_hold`` is a signal-to-exit
   timeout: the deadline is ``Signal.time + max_hold`` (the decision instant, not the fill),
   rebuilt from the persisted signal after a restart; a deadline that passed while the engine
   was down closes the position at the first step, never restarting the clock.
7. In OPEN, at a close of the strategy's decision timeframe, unless an order outcome is still
   unresolved: ask the strategy for a Signal, the risk engine for a decision, and submit the
   resulting ``OrderIntent`` through the ``OrderManager`` (recorded, age-limited, idempotent).

A lost connection anywhere in a step is journalled and the step ends; the next step retries
(so a cutoff that passes while disconnected is acted on as soon as the broker is back).

"Owned" means the broker reports Aurum's magic number and an "AU" idempotency key, so manual
trades are never touched. Everything that happens is appended to ``journal``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from types import MappingProxyType

from aurum.core.enums import ExecutionStatus, ExitReason, SessionPhase
from aurum.core.errors import BrokerDisconnectedError, NoMarketDataError
from aurum.core.models import (
    DealRecord,
    ExecutionReport,
    OrderIntent,
    OrderState,
    PositionState,
    SessionState,
    Signal,
    SymbolSpec,
)
from aurum.core.timeframes import Timeframe
from aurum.execution.intents import UNRESOLVED, IntentStore
from aurum.execution.killswitch import KillSwitch
from aurum.execution.orders import (
    KEY_PREFIX,
    OrderManager,
    owned_deals,
    owned_orders,
    owned_positions,
)
from aurum.execution.reconcile import reconcile
from aurum.features.indicators import atr
from aurum.features.view import MarketView
from aurum.ports.broker import BrokerPort
from aurum.risk.base import RiskContext, RiskEngine
from aurum.sessions.clock import SessionClock
from aurum.sessions.state_machine import SessionStateMachine
from aurum.strategies.base import Strategy

__all__ = ["KEY_PREFIX", "Engine", "JournalEntry", "JournalEvent"]

ATR_BARS = 100
"""Completed M15 bars the spread/ATR gate's ATR is computed over (fixed, so it is
deterministic whatever history a mode has loaded)."""

HISTORY_BARS: Mapping[Timeframe, int] = MappingProxyType({Timeframe.M15: ATR_BARS})
"""Completed bars the engine itself reads at a decision, apart from the strategy's
``warmup_bars``: the M15 ATR for the policy risk engine's spread/ATR gate. A run must load them
before its first decision (backtests merge the two by maximum per timeframe)."""


class JournalEvent(StrEnum):
    SESSION = "session"
    SIGNAL = "signal"
    RISK_REJECTED = "risk_rejected"
    ENTRY_BLOCKED = "entry_blocked"
    ORDER_FILLED = "order_filled"
    ORDER_PLACED = "order_placed"
    ORDER_REJECTED = "order_rejected"
    ORDER_UNKNOWN = "order_unknown"
    CLOSE = "close"
    CLOSE_FAILED = "close_failed"
    CANCEL = "cancel"
    CANCEL_FAILED = "cancel_failed"
    RECONCILE = "reconcile"
    KILL_SWITCH = "kill_switch"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True, slots=True)
class JournalEntry:
    time: datetime
    """Engine decision time (UTC); broker execution times are in the reports/trades."""
    event: JournalEvent
    detail: str
    phase: SessionPhase
    idempotency_key: str | None = None
    position_id: int | None = None
    order_id: int | None = None
    exit_reason: ExitReason | None = None


class Engine:
    def __init__(
        self,
        *,
        broker: BrokerPort,
        strategy: Strategy,
        risk: RiskEngine,
        calendar: SessionClock,
        symbol: str,
        magic: int,
        store: IntentStore | None = None,
        kill_switch: KillSwitch | None = None,
        max_intent_age: timedelta = timedelta(seconds=60),
        atr_period: int = 14,
    ) -> None:
        self._broker = broker
        self._strategy = strategy
        self._risk = risk
        self._calendar = calendar
        self._sm = SessionStateMachine(calendar)
        self._symbol = symbol
        self._magic = magic
        self._store = store or IntentStore()
        self._orders = OrderManager(broker, self._store, magic=magic, max_intent_age=max_intent_age)
        self._kill = kill_switch
        self._max_age = max_intent_age
        self._atr_period = atr_period
        self._spec: SymbolSpec | None = None
        self._signals: dict[str, Signal] = self._store.signals()
        """Restored from the store, so a restart keeps every position's signal."""
        self._deadlines: dict[str, datetime] = {}
        self._reconciled = False
        self._acted = False
        self.journal: list[JournalEntry] = []
        self.exit_reasons: dict[int, ExitReason] = {}
        """Position id -> why the engine closed it (broker-side stops/targets are not here)."""

    # ------------------------------------------------------------------ public
    @property
    def store(self) -> IntentStore:
        return self._store

    def signal_for(self, idempotency_key: str | None) -> Signal | None:
        """The signal behind an owned order/position (None if unknown, e.g. adopted)."""
        return self._signals.get(idempotency_key) if idempotency_key else None

    def next_deadline_after(self, now: datetime) -> datetime | None:
        """Earliest ``max_hold`` expiry after ``now`` among positions seen open."""
        later = [d for d in self._deadlines.values() if d > now]
        return min(later) if later else None

    def step(self, view: MarketView) -> None:
        now = view.now
        transition = self._sm.update(now)
        state = self._sm.state
        assert state is not None
        if transition is not None:
            before = transition.previous.phase if transition.previous else "start"
            self._log(now, JournalEvent.SESSION, f"{before} -> {state.phase}", state.phase)
        try:
            if not self._reconciled or self._store.records(UNRESOLVED):
                self._reconcile(now, state.phase)
                self._reconciled = True
            self._acted = False
            self._step(view, state)
            if self._acted:
                self._reconcile(now, state.phase)
        except BrokerDisconnectedError as exc:
            self._reconciled = False  # reconcile first thing once the broker is back
            self._log(now, JournalEvent.DISCONNECTED, str(exc), state.phase)

    def flatten(
        self,
        now: datetime,
        reason: ExitReason,
        detail: str,
        phase: SessionPhase | None = None,
    ) -> None:
        """Close every owned position and cancel every owned pending order."""
        phase = phase or (self._sm.state.phase if self._sm.state else SessionPhase.CLOSED)
        positions, orders = self._owned()
        for o in orders:
            self._cancel(now, o, detail, phase)
        for p in positions:
            self._close(now, p, reason, detail, phase)

    # ------------------------------------------------------------------ internals
    def _step(self, view: MarketView, state: SessionState) -> None:
        now = view.now
        kill = self._kill.reason() if self._kill is not None else None
        if kill is not None:
            positions, orders = self._owned()
            if positions or orders:
                self._log(now, JournalEvent.KILL_SWITCH, kill, state.phase)
                self.flatten(now, ExitReason.KILL_SWITCH, f"kill switch: {kill}", state.phase)
            return
        positions, orders = self._owned()
        self._deadlines = {
            p.idempotency_key: s.time + s.max_hold
            for p in positions
            if p.idempotency_key
            and (s := self._signals.get(p.idempotency_key)) is not None
            and s.max_hold is not None
        }
        if state.must_be_flat:
            self.flatten(now, ExitReason.FORCE_FLAT, f"phase {state.phase}", state.phase)
            return
        if not state.entries_allowed:
            for o in orders:
                self._cancel(now, o, f"phase {state.phase}: no new entries", state.phase)
        for p in positions:
            self._manage(view, p, state.phase)
        if state.entries_allowed and self._strategy.decision_timeframe.is_aligned(now):
            self._consider_entry(view, state)

    def _owned(self) -> tuple[list[PositionState], list[OrderState]]:
        return (
            owned_positions(self._broker, self._magic, self._symbol),
            owned_orders(self._broker, self._magic, self._symbol),
        )

    def _reconcile(self, now: datetime, phase: SessionPhase) -> None:
        actions = reconcile(
            self._broker,
            self._orders,
            now=now,
            magic=self._magic,
            symbol=self._symbol,
            max_intent_age=self._max_age,
        )
        for a in actions:
            self._log(
                now,
                JournalEvent.RECONCILE,
                f"{a.kind}: {a.detail}",
                phase,
                key=a.key,
                position_id=a.position_id,
                order_id=a.order_id,
            )

    def _manage(self, view: MarketView, position: PositionState, phase: SessionPhase) -> None:
        signal = self.signal_for(position.idempotency_key)
        if signal is None:
            return
        now = view.now
        if signal.max_hold is not None and now >= signal.time + signal.max_hold:
            self._close(now, position, ExitReason.TIMEOUT, f"max_hold {signal.max_hold}", phase)
            return
        why = self._strategy.invalidation(view, position, signal)
        if why:
            self._close(now, position, ExitReason.INVALIDATION, why, phase)

    def _consider_entry(self, view: MarketView, state: SessionState) -> None:
        now = view.now
        signal = self._strategy.on_bar(view)
        if signal is None:
            return
        if signal.time != now or signal.symbol != self._symbol:
            raise ValueError(
                f"strategy returned a signal for {signal.symbol} at {signal.time}, "
                f"expected {self._symbol} at {now}"
            )
        key = f"{KEY_PREFIX}{signal.key}"
        self._log(now, JournalEvent.SIGNAL, "; ".join(signal.reasons), state.phase, key=key)
        unresolved = self._store.records(UNRESOLVED)
        if unresolved:
            keys = ", ".join(r.key for r in unresolved)
            self._log(
                now,
                JournalEvent.ENTRY_BLOCKED,
                f"order outcome unresolved for {keys}: no new entry until reconciled",
                state.phase,
                key=key,
            )
            return
        decision = self._risk.evaluate(signal, self._risk_context(view, state))
        if not decision.approved:
            detail = "; ".join(decision.reasons)
            self._log(now, JournalEvent.RISK_REJECTED, detail, state.phase, key=key)
            return
        intent = OrderIntent.from_signal(signal, decision, created_at=now, magic=self._magic)
        self._signals[intent.idempotency_key] = signal
        self._acted = True
        report = self._orders.submit(intent, signal, now)
        self._log_report(now, report, state.phase)
        if report.status is ExecutionStatus.FILLED and signal.max_hold is not None:
            self._deadlines[intent.idempotency_key] = signal.time + signal.max_hold

    def _risk_context(self, view: MarketView, state: SessionState) -> RiskContext:
        now = view.now
        if self._spec is None:
            self._spec = self._broker.symbol_info(self._symbol)
        try:
            quote = self._broker.latest_tick(self._symbol)
        except NoMarketDataError:
            quote = None
        positions, orders = self._owned()
        day_start = week_start = session_start = None
        deals: list[DealRecord] = []
        sched = self._calendar.schedule_for(state.trading_day) if state.trading_day else None
        if sched is not None and state.trading_day is not None:
            day_start = sched.day_start
            monday = state.trading_day - timedelta(days=state.trading_day.weekday())
            week_start = self._calendar.local_to_utc(monday, time(0))
            session_start = next((w.start for w in sched.sessions if w.name == state.session), None)
            deals = owned_deals(
                self._broker, self._magic, week_start, now + timedelta(milliseconds=1)
            )
        try:
            m15 = view.bars(Timeframe.M15, ATR_BARS)
            m15_atr = atr(m15.high, m15.low, m15.close, self._atr_period)
        except KeyError:  # no M15 series in this market: the risk engine decides
            m15_atr = None
        return RiskContext(
            now=now,
            session=state,
            account=self._broker.account_info(),
            spec=self._spec,
            positions=positions,
            orders=orders,
            quote=quote,
            connected=self._broker.is_connected(),
            m15_atr=m15_atr,
            deals=deals,
            day_start=day_start,
            week_start=week_start,
            session_start=session_start,
        )

    def _log_report(self, now: datetime, report: ExecutionReport, phase: SessionPhase) -> None:
        event = {
            ExecutionStatus.FILLED: JournalEvent.ORDER_FILLED,
            ExecutionStatus.PLACED: JournalEvent.ORDER_PLACED,
            ExecutionStatus.UNKNOWN: JournalEvent.ORDER_UNKNOWN,
        }.get(report.status, JournalEvent.ORDER_REJECTED)
        detail = report.message or (
            f"{report.status} {report.volume} @ {report.fill_price or report.requested_price}"
        )
        if report.duplicate:
            detail = f"duplicate key: {detail}"
        self._log(
            now,
            event,
            detail,
            phase,
            key=report.idempotency_key,
            position_id=report.position_id,
            order_id=report.order_id,
        )

    def _close(
        self,
        now: datetime,
        position: PositionState,
        reason: ExitReason,
        detail: str,
        phase: SessionPhase,
    ) -> None:
        self._acted = True
        report = self._broker.close_position(position.position_id)
        ok = report.status is ExecutionStatus.CLOSED
        if ok:
            self.exit_reasons[position.position_id] = reason
        self._log(
            now,
            JournalEvent.CLOSE if ok else JournalEvent.CLOSE_FAILED,
            detail if ok else f"{detail}: {report.message}",
            phase,
            key=position.idempotency_key,
            position_id=position.position_id,
            exit_reason=reason,
        )

    def _cancel(self, now: datetime, order: OrderState, detail: str, phase: SessionPhase) -> None:
        self._acted = True
        report = self._broker.cancel_order(order.order_id)
        ok = report.status is ExecutionStatus.CANCELLED
        self._log(
            now,
            JournalEvent.CANCEL if ok else JournalEvent.CANCEL_FAILED,
            detail if ok else f"{detail}: {report.message}",
            phase,
            key=order.idempotency_key,
            order_id=order.order_id,
        )

    def _log(
        self,
        now: datetime,
        event: JournalEvent,
        detail: str,
        phase: SessionPhase,
        *,
        key: str | None = None,
        position_id: int | None = None,
        order_id: int | None = None,
        exit_reason: ExitReason | None = None,
    ) -> None:
        self.journal.append(
            JournalEntry(
                time=now,
                event=event,
                detail=detail,
                phase=phase,
                idempotency_key=key,
                position_id=position_id,
                order_id=order_id,
                exit_reason=exit_reason,
            )
        )
