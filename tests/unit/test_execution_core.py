"""Intent store, order manager and reconciliation on the in-memory FakeBroker."""

from datetime import timedelta
from pathlib import Path

import pytest

from aurum.adapters.fake.broker import FakeBroker
from aurum.core.clock import ManualClock
from aurum.core.enums import ExecutionStatus, OrderType, Side
from aurum.core.models import OrderIntent, Signal, Tick
from aurum.execution.intents import IntentStatus, IntentStore
from aurum.execution.killswitch import KillSwitch
from aurum.execution.orders import OrderManager, flatten_owned, is_owned, owned_positions
from aurum.execution.reconcile import reconcile
from tests.factories import T0, approve, signal
from tests.fault.faulty import FaultyBroker, as_port

MAGIC = 7020261
AGE = timedelta(seconds=60)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


@pytest.fixture
def broker(clock: ManualClock) -> FakeBroker:
    b = FakeBroker(clock=clock)
    b.connect()
    b.add_ticks([Tick(symbol="XAUUSD", time=T0 - timedelta(seconds=1), bid=2000.0, ask=2000.1)])
    return b


def intent_for(
    sig: Signal, *, magic: int = MAGIC, created: timedelta = timedelta(0)
) -> OrderIntent:
    return OrderIntent.from_signal(
        sig, approve(sig, 0.10), created_at=sig.time + created, magic=magic
    )


def manager(broker: object, store: IntentStore | None = None) -> OrderManager:
    return OrderManager(broker, store or IntentStore(), magic=MAGIC, max_intent_age=AGE)  # type: ignore[arg-type]


# ------------------------------------------------------------------ store


def test_store_persists_across_instances(tmp_path: Path) -> None:
    sig = signal(stop_loss=1990.0)
    path = tmp_path / "intents.sqlite"
    with IntentStore(path) as store:
        store.add(intent_for(sig), sig, T0)
        store.add(intent_for(sig), sig, T0)  # same key: ignored
        key = intent_for(sig).idempotency_key
        store.set_status(key, IntentStatus.FILLED, T0, position_id=7)
    with IntentStore(path) as again:
        (rec,) = again.records()
        assert (rec.key, rec.status, rec.position_id) == (key, IntentStatus.FILLED, 7)
        assert rec.signal == sig
        assert again.signals() == {key: sig}
        assert again.records(frozenset({IntentStatus.SENT})) == []


# ------------------------------------------------------------------ order manager


def test_submit_records_the_fill(broker: FakeBroker) -> None:
    m = manager(broker)
    sig = signal(stop_loss=1990.0)
    report = m.submit(intent_for(sig), sig, T0)
    assert report.status is ExecutionStatus.FILLED
    rec = m.store.get(report.idempotency_key or "")
    assert rec is not None
    assert (rec.status, rec.position_id) == (IntentStatus.FILLED, report.position_id)


def test_stale_intent_is_never_sent(broker: FakeBroker) -> None:
    m = manager(broker)
    sig = signal(stop_loss=1990.0)
    report = m.submit(intent_for(sig), sig, T0 + timedelta(seconds=61))
    assert report.status is ExecutionStatus.REJECTED
    assert "stale intent: 61.0 s old > 60 s" in report.message
    assert broker.sent_intents == []
    assert m.store.get(report.idempotency_key or "").status is IntentStatus.EXPIRED  # type: ignore[union-attr]


def test_stale_intent_already_at_the_broker_is_a_duplicate(broker: FakeBroker) -> None:
    m = manager(broker)
    sig = signal(stop_loss=1990.0)
    first = m.submit(intent_for(sig), sig, T0)
    again = m.submit(intent_for(sig), sig, T0 + timedelta(minutes=5))
    assert again.duplicate
    assert again.position_id == first.position_id
    assert len(broker.sent_intents) == 1


def test_lost_reply_is_unknown_not_rejected(broker: FakeBroker) -> None:
    faulty = FaultyBroker(broker)
    faulty.lose_next_reply = True
    m = manager(as_port(faulty))
    sig = signal(stop_loss=1990.0)
    report = m.submit(intent_for(sig), sig, T0)
    assert report.status is ExecutionStatus.UNKNOWN
    assert m.store.get(report.idempotency_key or "").status is IntentStatus.UNKNOWN  # type: ignore[union-attr]
    assert len(broker.positions()) == 1


# ------------------------------------------------------------------ ownership


def test_ownership_needs_magic_and_key_prefix() -> None:
    assert is_owned("AU123", MAGIC, MAGIC)
    assert not is_owned("AU123", 0, MAGIC)  # someone else's EA or a manual trade
    assert not is_owned("manual", MAGIC, MAGIC)
    assert not is_owned(None, MAGIC, MAGIC)


def test_flatten_owned_leaves_manual_trades_alone(broker: FakeBroker) -> None:
    ours, manual = signal(stop_loss=1990.0), signal(stop_loss=1991.0, t=T0 + timedelta(seconds=1))
    broker.send_order(intent_for(ours))
    broker.send_order(intent_for(manual, magic=0))
    pending = signal(
        stop_loss=1980.0,
        entry_type=OrderType.LIMIT,
        entry_price=1990.0,
        t=T0 + timedelta(seconds=2),
    )
    broker.send_order(intent_for(pending))
    reports = flatten_owned(broker, MAGIC)
    assert [r.status for r in reports] == [ExecutionStatus.CANCELLED, ExecutionStatus.CLOSED]
    (left,) = broker.positions()
    assert left.magic == 0


def test_kill_switch_file(tmp_path: Path) -> None:
    kill = KillSwitch(tmp_path / "data" / "KILL")
    assert kill.reason() is None
    kill.engage("drawdown review", T0)
    assert kill.reason() == f"{T0.isoformat()} drawdown review"
    assert kill.clear()
    assert not kill.clear()
    (tmp_path / "data" / "KILL").write_text("", encoding="utf-8")
    assert kill.reason() == "KILL file present"


# ------------------------------------------------------------------ reconciliation


def run(broker: object, m: OrderManager, now=T0):  # type: ignore[no-untyped-def]
    return reconcile(broker, m, now=now, magic=MAGIC, symbol="XAUUSD", max_intent_age=AGE)  # type: ignore[arg-type]


def test_unknown_owned_position_is_adopted(broker: FakeBroker) -> None:
    broker.send_order(intent_for(signal(stop_loss=1990.0)))  # sent by a previous process
    m = manager(broker)
    (action,) = run(broker, m)
    assert action.kind == "adopted"
    assert m.store.records()[0].status is IntentStatus.ADOPTED
    assert run(broker, m) == []  # idempotent


def test_position_without_stop_gets_its_stop_or_is_closed(broker: FakeBroker) -> None:
    m = manager(broker)
    sig = signal(stop_loss=1990.0)
    report = m.submit(intent_for(sig), sig, T0)
    pid = report.position_id
    assert pid is not None
    broker._positions[pid] = broker._positions[pid].model_copy(update={"stop_loss": None})
    (action,) = run(broker, m)
    assert action.kind == "stop_attached"
    assert broker.positions()[0].stop_loss == 1990.0
    # An adopted position has no intent to take the stop from: it is closed.
    other = FakeBroker(clock=ManualClock(T0))
    other.connect()
    other.add_ticks([Tick(symbol="XAUUSD", time=T0, bid=2000.0, ask=2000.1)])
    other.send_order(intent_for(sig))
    (opid,) = [p.position_id for p in other.positions()]
    other._positions[opid] = other._positions[opid].model_copy(update={"stop_loss": None})
    kinds = [a.kind for a in run(other, manager(other))]
    assert kinds == ["adopted", "closed_without_stop"]
    assert other.positions() == []


def test_unknown_pending_order_is_cancelled(broker: FakeBroker) -> None:
    pending = signal(stop_loss=1980.0, entry_type=OrderType.LIMIT, entry_price=1990.0)
    broker.send_order(intent_for(pending))
    m = manager(broker)
    (action,) = run(broker, m)
    assert action.kind == "cancelled_unknown"
    assert broker.orders() == []


def test_unresolved_intent_waits_then_expires(broker: FakeBroker) -> None:
    faulty = FaultyBroker(broker)
    faulty.fail_next_send = True
    m = manager(as_port(faulty))
    sig = signal(stop_loss=1990.0)
    report = m.submit(intent_for(sig), sig, T0)
    assert run(broker, m, T0 + timedelta(seconds=30)) == []  # may still arrive
    (action,) = run(broker, m, T0 + timedelta(seconds=61))
    assert action.kind == "expired"
    assert m.store.get(report.idempotency_key or "").status is IntentStatus.EXPIRED  # type: ignore[union-attr]


def test_filled_then_closed_intent_becomes_closed(broker: FakeBroker) -> None:
    m = manager(broker)
    sig = signal(stop_loss=1990.0)
    report = m.submit(intent_for(sig), sig, T0)
    broker.close_position(report.position_id or 0)
    assert run(broker, m) == []
    assert m.store.get(report.idempotency_key or "").status is IntentStatus.CLOSED  # type: ignore[union-attr]
    assert owned_positions(broker, MAGIC, None) == []


def test_find_existing_locates_a_sell_position(broker: FakeBroker) -> None:
    sell = signal(Side.SELL, stop_loss=2010.0)
    m = manager(broker)
    report = m.submit(intent_for(sell), sell, T0)
    found = m.find_existing(report.idempotency_key or "", T0 - timedelta(days=1), T0)
    assert found is not None
    assert found.position_id == report.position_id
