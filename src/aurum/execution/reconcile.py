"""Reconciliation: make the intent store agree with the broker, and fix what is unsafe.

Run on startup and after every engine action. The broker is the source of truth. Per Aurum-
owned item (magic and "AU" key):

* a position the store does not know is **adopted** (no signal, so only force-flat and the kill
  switch manage it);
* a position without a broker-side stop is an incident: the stop from its intent is attached,
  and if that is impossible the position is closed;
* a pending order the store does not know is **cancelled** (an entry nobody can explain);
* an intent whose outcome is unresolved (SENT/UNKNOWN) takes the broker's state if the broker
  holds its key, else it is **expired** once older than ``max_intent_age`` (never resent);
* a filled intent whose position is gone becomes CLOSED, a placed one whose order is gone
  becomes FILLED (it triggered) or CANCELLED.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from aurum.core.enums import ExecutionStatus
from aurum.execution.intents import UNRESOLVED, IntentStatus, IntentStore
from aurum.execution.orders import LOOKBACK, OrderManager, owned_orders, owned_positions
from aurum.ports.broker import BrokerPort


@dataclass(frozen=True, slots=True)
class ReconcileAction:
    kind: str
    detail: str
    key: str | None = None
    position_id: int | None = None
    order_id: int | None = None


def reconcile(
    broker: BrokerPort,
    manager: OrderManager,
    *,
    now: datetime,
    magic: int,
    symbol: str | None,
    max_intent_age: timedelta,
) -> list[ReconcileAction]:
    store: IntentStore = manager.store
    actions: list[ReconcileAction] = []
    positions = owned_positions(broker, magic, symbol)
    orders = owned_orders(broker, magic, symbol)

    for p in positions:
        key = p.idempotency_key
        assert key is not None
        rec = store.get(key)
        if rec is None:
            store.adopt(p, now)
            actions.append(
                ReconcileAction(
                    "adopted", "owned position without a stored intent", key, p.position_id
                )
            )
        elif rec.status is not IntentStatus.FILLED and rec.status is not IntentStatus.ADOPTED:
            store.set_status(key, IntentStatus.FILLED, now, position_id=p.position_id)
            actions.append(
                ReconcileAction("confirmed", f"{rec.status} -> FILLED", key, p.position_id)
            )
        if p.stop_loss is None:
            stop = rec.intent.stop_loss if rec is not None and rec.intent is not None else None
            fixed = False
            if stop is not None:
                fixed = (
                    broker.modify_position(p.position_id, stop, p.take_profit).status
                    is ExecutionStatus.MODIFIED
                )
            if fixed:
                actions.append(
                    ReconcileAction(
                        "stop_attached", f"missing stop set to {stop}", key, p.position_id
                    )
                )
            else:
                closed = broker.close_position(p.position_id).status is ExecutionStatus.CLOSED
                actions.append(
                    ReconcileAction(
                        "closed_without_stop" if closed else "close_failed",
                        "position had no broker-side stop and none could be attached",
                        key,
                        p.position_id,
                    )
                )

    for o in orders:
        key = o.idempotency_key
        assert key is not None
        rec = store.get(key)
        if rec is None:
            ok = broker.cancel_order(o.order_id).status is ExecutionStatus.CANCELLED
            actions.append(
                ReconcileAction(
                    "cancelled_unknown" if ok else "cancel_failed",
                    "owned pending order without a stored intent",
                    key,
                    order_id=o.order_id,
                )
            )
        elif rec.status is not IntentStatus.PLACED:
            store.set_status(key, IntentStatus.PLACED, now, order_id=o.order_id)
            actions.append(
                ReconcileAction("confirmed", f"{rec.status} -> PLACED", key, order_id=o.order_id)
            )

    open_keys = {p.idempotency_key for p in positions}
    pending_keys = {o.idempotency_key for o in orders}
    for rec in store.records(UNRESOLVED | {IntentStatus.FILLED, IntentStatus.PLACED}):
        if rec.key in open_keys or rec.key in pending_keys:
            continue
        if rec.status is IntentStatus.FILLED:
            store.set_status(rec.key, IntentStatus.CLOSED, now)
            continue
        found = manager.find_existing(rec.key, rec.created_at - LOOKBACK, now)
        if rec.status in UNRESOLVED:
            if found is not None:
                store.set_status(rec.key, IntentStatus.CLOSED, now, position_id=found.position_id)
                actions.append(
                    ReconcileAction(
                        "resolved", f"{rec.status} -> filled and already closed", rec.key
                    )
                )
            elif now - rec.created_at > max_intent_age:
                store.set_status(
                    rec.key, IntentStatus.EXPIRED, now, note="not at the broker; never resent"
                )
                actions.append(
                    ReconcileAction("expired", f"{rec.status}: not at the broker", rec.key)
                )
        elif found is not None:  # PLACED, order gone, an opening deal exists: it triggered
            store.set_status(rec.key, IntentStatus.CLOSED, now, position_id=found.position_id)
        else:
            store.set_status(rec.key, IntentStatus.CANCELLED, now)
    return actions
