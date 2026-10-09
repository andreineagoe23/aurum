"""The decision loop shared by every mode: session -> exits -> strategy -> risk -> broker.

``Engine.step(view)`` is called at decision instants (bar closes and scheduled times) by a
driver: the backtest runner with a ``SimBroker`` today, the shadow/demo loops with live brokers
later. The engine never knows which; it only talks to a ``BrokerPort``.

Per step, at ``now = view.now``:

1. Update the session state machine (its first update always emits, so a restart inside
   FORCE_FLAT still flattens).
2. If the phase says flat (CLOSED, PRE_SESSION, FORCE_FLAT): close every owned position and
   cancel every owned pending order (exit reason FORCE_FLAT).
3. Outside OPEN: cancel owned pending entry orders.
4. For each owned position: close it when its signal's ``max_hold`` has elapsed (TIMEOUT) or
   the strategy reports the setup invalid (INVALIDATION).
5. In OPEN, at a close of the strategy's decision timeframe: ask the strategy for a Signal, the
   risk engine for a decision, and send the resulting ``OrderIntent``.

"Owned" means the broker reports Aurum's magic number and an "AU" idempotency key, so manual
trades are never touched. Everything that happens is appended to ``journal``.

Not here yet (Phase 4): reconciliation of positions opened before a restart, stale intents,
kill switch, retries with backoff.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from aurum.core.enums import ExecutionStatus, ExitReason, SessionPhase
from aurum.core.errors import NoMarketDataError
from aurum.core.models import (
    ExecutionReport,
    OrderIntent,
    OrderState,
    PositionState,
    SessionState,
    Signal,
    SymbolSpec,
)
from aurum.features.view import MarketView
from aurum.ports.broker import BrokerPort
from aurum.risk.base import RiskContext, RiskEngine
from aurum.sessions.clock import SessionClock
from aurum.sessions.state_machine import SessionStateMachine
from aurum.strategies.base import Strategy

KEY_PREFIX = "AU"


class JournalEvent(StrEnum):
    SESSION = "session"
    SIGNAL = "signal"
    RISK_REJECTED = "risk_rejected"
    ORDER_FILLED = "order_filled"
    ORDER_PLACED = "order_placed"
    ORDER_REJECTED = "order_rejected"
    CLOSE = "close"
    CLOSE_FAILED = "close_failed"
    CANCEL = "cancel"
    CANCEL_FAILED = "cancel_failed"


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
    ) -> None:
        self._broker = broker
        self._strategy = strategy
        self._risk = risk
        self._sm = SessionStateMachine(calendar)
        self._symbol = symbol
        self._magic = magic
        self._spec: SymbolSpec | None = None
        self._signals: dict[str, Signal] = {}
        self._deadlines: dict[str, datetime] = {}
        self.journal: list[JournalEntry] = []
        self.exit_reasons: dict[int, ExitReason] = {}
        """Position id -> why the engine closed it (broker-side stops/targets are not here)."""

    # ------------------------------------------------------------------ public
    def signal_for(self, idempotency_key: str | None) -> Signal | None:
        """The signal behind an owned order/position (None if unknown, e.g. pre-restart)."""
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
    def _owns(self, key: str | None, magic: int) -> bool:
        return magic == self._magic and key is not None and key.startswith(KEY_PREFIX)

    def _owned(self) -> tuple[list[PositionState], list[OrderState]]:
        positions = [
            p
            for p in self._broker.positions(self._symbol)
            if self._owns(p.idempotency_key, p.magic)
        ]
        orders = [
            o for o in self._broker.orders(self._symbol) if self._owns(o.idempotency_key, o.magic)
        ]
        return positions, orders

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
        positions, orders = self._owned()
        key = f"{KEY_PREFIX}{signal.key}"
        self._log(now, JournalEvent.SIGNAL, "; ".join(signal.reasons), state.phase, key=key)
        if self._spec is None:
            self._spec = self._broker.symbol_info(self._symbol)
        try:
            quote = self._broker.latest_tick(self._symbol)
        except NoMarketDataError:
            quote = None
        ctx = RiskContext(
            now=now,
            session=state,
            account=self._broker.account_info(),
            spec=self._spec,
            positions=positions,
            orders=orders,
            quote=quote,
        )
        decision = self._risk.evaluate(signal, ctx)
        if not decision.approved:
            detail = "; ".join(decision.reasons)
            self._log(now, JournalEvent.RISK_REJECTED, detail, state.phase, key=key)
            return
        intent = OrderIntent.from_signal(signal, decision, created_at=now, magic=self._magic)
        self._signals[intent.idempotency_key] = signal
        report = self._broker.send_order(intent)
        self._log_report(now, report, state.phase)
        if report.status is ExecutionStatus.FILLED and signal.max_hold is not None:
            self._deadlines[intent.idempotency_key] = signal.time + signal.max_hold

    def _log_report(self, now: datetime, report: ExecutionReport, phase: SessionPhase) -> None:
        event = {
            ExecutionStatus.FILLED: JournalEvent.ORDER_FILLED,
            ExecutionStatus.PLACED: JournalEvent.ORDER_PLACED,
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
