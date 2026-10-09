"""Order manager: the only way the engine sends an order (idempotent, age-limited, recorded).

``submit(intent, signal, now)``:

1. Record the intent in the ``IntentStore`` before anything reaches the broker.
2. If the intent is older than ``max_intent_age`` (60 s, broker.yaml), never send it. An order
   the broker already holds under its key is still recognised and reported as a duplicate.
3. Send. Brokers deduplicate on the idempotency key, so a resend can never double an order.
4. If the connection fails during the request, the outcome is UNKNOWN: it is recorded as such
   and resolved by reconciliation from the broker's own positions, orders and deals. The
   engine opens nothing new while any outcome is unresolved.

Ownership everywhere: the broker's ``magic`` equals ours **and** the key starts with "AU".
"""

from datetime import datetime, timedelta

from aurum.core.enums import DealEntry, ExecutionStatus
from aurum.core.errors import BrokerDisconnectedError
from aurum.core.models import (
    DealRecord,
    ExecutionReport,
    OrderIntent,
    OrderState,
    PositionState,
    Signal,
)
from aurum.execution.intents import IntentStatus, IntentStore
from aurum.ports.broker import BrokerPort

KEY_PREFIX = "AU"
LOOKBACK = timedelta(days=7)
"""How far back deal history is searched for an intent's key."""

_STATUS = {
    ExecutionStatus.FILLED: IntentStatus.FILLED,
    ExecutionStatus.PLACED: IntentStatus.PLACED,
    ExecutionStatus.REJECTED: IntentStatus.REJECTED,
    ExecutionStatus.UNKNOWN: IntentStatus.UNKNOWN,
}


def is_owned(key: str | None, magic: int, own_magic: int) -> bool:
    return magic == own_magic and key is not None and key.startswith(KEY_PREFIX)


def owned_positions(broker: BrokerPort, magic: int, symbol: str | None) -> list[PositionState]:
    return [p for p in broker.positions(symbol) if is_owned(p.idempotency_key, p.magic, magic)]


def owned_orders(broker: BrokerPort, magic: int, symbol: str | None) -> list[OrderState]:
    return [o for o in broker.orders(symbol) if is_owned(o.idempotency_key, o.magic, magic)]


def owned_deals(broker: BrokerPort, magic: int, start: datetime, end: datetime) -> list[DealRecord]:
    """Aurum's deals with ``start <= time < end``, oldest first."""
    deals = [
        d for d in broker.history_deals(start, end) if is_owned(d.idempotency_key, d.magic, magic)
    ]
    return sorted(deals, key=lambda d: (d.time, d.deal_id))


def flatten_owned(
    broker: BrokerPort, magic: int, symbol: str | None = None
) -> list[ExecutionReport]:
    """Cancel every owned pending order, then close every owned position (kill switch)."""
    reports = [broker.cancel_order(o.order_id) for o in owned_orders(broker, magic, symbol)]
    reports += [
        broker.close_position(p.position_id) for p in owned_positions(broker, magic, symbol)
    ]
    return reports


class OrderManager:
    def __init__(
        self,
        broker: BrokerPort,
        store: IntentStore,
        *,
        magic: int,
        max_intent_age: timedelta = timedelta(seconds=60),
    ) -> None:
        self._broker = broker
        self._store = store
        self._magic = magic
        self._max_age = max_intent_age

    @property
    def store(self) -> IntentStore:
        return self._store

    def submit(self, intent: OrderIntent, signal: Signal, now: datetime) -> ExecutionReport:
        key = intent.idempotency_key
        self._store.add(intent, signal, now)
        age = now - intent.created_at
        if age > self._max_age:
            found = self.find_existing(key, intent.created_at - LOOKBACK, now)
            if found is not None:
                self._record(found, now)
                return found.model_copy(update={"duplicate": True})
            message = (
                f"stale intent: {age.total_seconds():.1f} s old > "
                f"{self._max_age.total_seconds():.0f} s, not sent"
            )
            self._store.set_status(key, IntentStatus.EXPIRED, now, note=message)
            return ExecutionReport(
                status=ExecutionStatus.REJECTED, time=now, idempotency_key=key, message=message
            )
        try:
            report = self._broker.send_order(intent)
        except BrokerDisconnectedError as exc:
            message = f"connection lost during send, outcome unknown: {exc}"
            self._store.set_status(key, IntentStatus.UNKNOWN, now, note=message)
            return ExecutionReport(
                status=ExecutionStatus.UNKNOWN, time=now, idempotency_key=key, message=message
            )
        self._record(report, now)
        return report

    def find_existing(self, key: str, since: datetime, now: datetime) -> ExecutionReport | None:
        """What the broker holds for ``key``: an open position, a pending order, or an opening
        deal since ``since`` (a position that already closed). None if nothing."""
        for p in self._broker.positions():
            if p.idempotency_key == key and p.magic == self._magic:
                return ExecutionReport(
                    status=ExecutionStatus.FILLED,
                    time=now,
                    idempotency_key=key,
                    position_id=p.position_id,
                    fill_price=p.open_price,
                    volume=p.volume,
                )
        for o in self._broker.orders():
            if o.idempotency_key == key and o.magic == self._magic:
                return ExecutionReport(
                    status=ExecutionStatus.PLACED,
                    time=now,
                    idempotency_key=key,
                    order_id=o.order_id,
                    requested_price=o.price,
                    volume=o.volume,
                )
        for d in self._broker.history_deals(since, now + timedelta(seconds=1)):
            if d.idempotency_key == key and d.magic == self._magic and d.entry is DealEntry.IN:
                return ExecutionReport(
                    status=ExecutionStatus.FILLED,
                    time=now,
                    idempotency_key=key,
                    deal_id=d.deal_id,
                    position_id=d.position_id,
                    fill_price=d.price,
                    volume=d.volume,
                )
        return None

    def _record(self, report: ExecutionReport, now: datetime) -> None:
        status = _STATUS.get(report.status)
        if status is None or report.idempotency_key is None:
            return
        self._store.set_status(
            report.idempotency_key,
            status,
            now,
            position_id=report.position_id,
            order_id=report.order_id,
            note=report.message,
        )
