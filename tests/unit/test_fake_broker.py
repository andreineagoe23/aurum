from datetime import UTC, datetime, timedelta

import pytest

from aurum.adapters.fake import FakeBroker, example_xauusd_spec
from aurum.core.clock import ManualClock
from aurum.core.enums import DealEntry, ExecutionStatus, OrderType, Side
from aurum.core.errors import (
    BrokerDisconnectedError,
    BrokerError,
    NoMarketDataError,
    SafetyViolationError,
)
from aurum.core.models import Bar, OrderIntent
from aurum.core.timeframes import Timeframe
from aurum.ports.broker import BrokerPort
from tests.factories import T0, intent, tick


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


@pytest.fixture
def fb(clock: ManualClock) -> FakeBroker:
    b = FakeBroker(clock=clock, balance=10_000.0, commission_per_lot_side=2.25)
    b.add_ticks([tick(bid=2000.00, ask=2000.20, t=T0)])
    b.connect()
    return b


def test_satisfies_broker_port(fb: FakeBroker) -> None:
    assert isinstance(fb, BrokerPort)


def test_buy_fills_at_ask_sell_at_bid(fb: FakeBroker) -> None:
    buy = fb.send_order(intent(Side.BUY))
    sell = fb.send_order(_sell())
    assert buy.status is ExecutionStatus.FILLED
    assert buy.fill_price == 2000.20
    assert sell.fill_price == 2000.00


def _sell() -> OrderIntent:
    return intent(Side.SELL)


def test_slippage_is_adverse(clock: ManualClock) -> None:
    b = FakeBroker(clock=clock, slippage_points=5)
    b.add_ticks([tick(bid=2000.00, ask=2000.20)])
    b.connect()
    assert b.send_order(intent(Side.BUY)).fill_price == pytest.approx(2000.25)
    assert b.send_order(_sell()).fill_price == pytest.approx(1999.95)


def test_never_fills_on_a_future_tick(clock: ManualClock) -> None:
    b = FakeBroker(clock=clock)
    b.add_ticks([tick(t=T0 + timedelta(seconds=1))])
    b.connect()
    report = b.send_order(intent())
    assert report.status is ExecutionStatus.REJECTED
    assert "no tick" in report.message
    with pytest.raises(NoMarketDataError):
        b.latest_tick("XAUUSD")


def test_stop_loss_attached_to_position(fb: FakeBroker) -> None:
    fb.send_order(intent(Side.BUY))
    (pos,) = fb.positions()
    assert pos.stop_loss == 1995.0
    assert pos.idempotency_key is not None


def test_duplicate_idempotency_key_never_creates_second_position(fb: FakeBroker) -> None:
    first = fb.send_order(intent())
    again = fb.send_order(intent())  # same signal -> same key (e.g. after a restart)
    assert not first.duplicate
    assert again.duplicate
    assert again.position_id == first.position_id
    assert len(fb.positions()) == 1
    assert len(fb.sent_intents) == 1


def test_unapproved_intent_bypassing_validation_is_refused(fb: FakeBroker) -> None:
    good = intent()
    bad_decision = good.risk_decision.model_construct(
        **{**dict(good.risk_decision), "approved": False}
    )
    bypassed = OrderIntent.model_construct(**{**dict(good), "risk_decision": bad_decision})
    with pytest.raises(SafetyViolationError):
        fb.send_order(bypassed)
    assert fb.positions() == []
    assert not fb.check_order(bypassed).ok


def test_invalid_volume_rejected(fb: FakeBroker) -> None:
    report = fb.send_order(intent(volume=0.015))
    assert report.status is ExecutionStatus.REJECTED
    assert "invalid volume" in report.message


def test_stop_on_wrong_side_of_market_rejected(fb: FakeBroker) -> None:
    # Signal had no reference price, but the market (ask 2000.20) is below the "stop".
    report = fb.send_order(intent(Side.BUY, stop_loss=2001.0))
    assert report.status is ExecutionStatus.REJECTED
    assert fb.positions() == []


def test_stops_level_enforced(clock: ManualClock) -> None:
    spec = example_xauusd_spec().model_copy(update={"stops_level_points": 1000})
    b = FakeBroker(clock=clock, symbol_spec=spec)
    b.add_ticks([tick()])
    b.connect()
    result = b.check_order(intent())  # stop 5.20 away < 10.00 required
    assert not result.ok
    assert "stops level" in result.reasons[0]


def test_round_trip_pnl_and_commission(fb: FakeBroker, clock: ManualClock) -> None:
    opened = fb.send_order(intent(Side.BUY, volume=0.10))
    assert opened.commission == pytest.approx(-0.225)
    clock.advance(timedelta(minutes=30))
    fb.add_ticks([tick(bid=2003.20, ask=2003.40, t=clock.now())])
    (pos,) = fb.positions()
    assert pos.unrealized_pnl == pytest.approx(30.0)  # (2003.20-2000.20)*100*0.1
    assert fb.account_info().equity == pytest.approx(10_000 - 0.225 + 30.0)
    assert opened.position_id is not None
    closed = fb.close_position(opened.position_id)
    assert closed.fill_price == 2003.20  # buy closes at bid
    assert fb.positions() == []
    assert fb.account_info().balance == pytest.approx(10_000 + 30.0 - 0.45)
    deals = fb.history_deals(T0, clock.now() + timedelta(seconds=1))
    assert [d.entry for d in deals] == [DealEntry.IN, DealEntry.OUT]
    assert deals[1].side is Side.SELL
    assert sum(d.commission for d in deals) == pytest.approx(-0.45)  # $4.50/lot round trip


def test_sell_pnl_sign(fb: FakeBroker, clock: ManualClock) -> None:
    opened = fb.send_order(_sell())  # fills at bid 2000.00
    clock.advance(timedelta(minutes=1))
    fb.add_ticks([tick(bid=1998.80, ask=1999.00, t=clock.now())])
    assert opened.position_id is not None
    fb.close_position(opened.position_id)  # closes at ask 1999.00
    deals = fb.history_deals(T0, clock.now() + timedelta(seconds=1))
    assert deals[-1].profit == pytest.approx(10.0)


def test_partial_close(fb: FakeBroker) -> None:
    opened = fb.send_order(intent(volume=0.10))
    assert opened.position_id is not None
    fb.close_position(opened.position_id, volume=0.04)
    (pos,) = fb.positions()
    assert pos.volume == pytest.approx(0.06)
    with pytest.raises(BrokerError):
        fb.close_position(opened.position_id, volume=1.0)


def test_modify_position_validates_levels(fb: FakeBroker) -> None:
    opened = fb.send_order(intent())
    assert opened.position_id is not None
    report = fb.modify_position(opened.position_id, stop_loss=1997.0, take_profit=2010.0)
    assert report.status is ExecutionStatus.MODIFIED
    (pos,) = fb.positions()
    assert (pos.stop_loss, pos.take_profit) == (1997.0, 2010.0)
    with pytest.raises(ValueError, match="beyond entry"):
        fb.modify_position(opened.position_id, stop_loss=2001.0, take_profit=None)
    with pytest.raises(BrokerError):
        fb.modify_position(999, stop_loss=1990.0, take_profit=None)


def test_pending_order_placed_and_cancelled(fb: FakeBroker) -> None:
    report = fb.send_order(intent(entry_type=OrderType.LIMIT, entry_price=1999.0))
    assert report.status is ExecutionStatus.PLACED
    (order,) = fb.orders()
    assert order.price == 1999.0
    assert order.stop_loss == 1995.0
    assert report.order_id is not None
    assert fb.cancel_order(report.order_id).status is ExecutionStatus.CANCELLED
    assert fb.orders() == []
    with pytest.raises(BrokerError):
        fb.cancel_order(report.order_id)


def test_disconnected_broker_raises_everywhere(fb: FakeBroker) -> None:
    fb.disconnect()
    assert not fb.is_connected()
    for call in (
        fb.account_info,
        lambda: fb.latest_tick("XAUUSD"),
        lambda: fb.send_order(intent()),
        fb.positions,
        fb.orders,
    ):
        with pytest.raises(BrokerDisconnectedError):
            call()


def test_bars_only_completed(fb: FakeBroker, clock: ManualClock) -> None:
    def bar(open_time: datetime) -> Bar:
        return Bar(
            symbol="XAUUSD",
            timeframe=Timeframe.H1,
            open_time=open_time,
            open=2000,
            high=2001,
            low=1999,
            close=2000.5,
        )

    fb.add_bars([bar(datetime(2026, 3, 30, h, tzinfo=UTC)) for h in (6, 7, 8)])
    start, end = datetime(2026, 3, 30, tzinfo=UTC), datetime(2026, 3, 31, tzinfo=UTC)
    clock.set(datetime(2026, 3, 30, 8, 35, tzinfo=UTC))
    assert [b.open_time.hour for b in fb.bars("XAUUSD", Timeframe.H1, start, end)] == [6, 7]
    clock.set(datetime(2026, 3, 30, 9, 0, tzinfo=UTC))
    assert [b.open_time.hour for b in fb.bars("XAUUSD", Timeframe.H1, start, end)] == [6, 7, 8]


def test_ticks_window_and_causality(fb: FakeBroker, clock: ManualClock) -> None:
    fb.add_ticks([tick(t=T0 + timedelta(seconds=s)) for s in (1, 2, 3)])
    clock.set(T0 + timedelta(seconds=2))
    got = fb.ticks("XAUUSD", T0, T0 + timedelta(minutes=1))
    assert [t.time for t in got] == [T0, T0 + timedelta(seconds=1), T0 + timedelta(seconds=2)]


def test_unknown_symbol(fb: FakeBroker) -> None:
    with pytest.raises(BrokerError, match="unknown symbol"):
        fb.symbol_info("EURUSD")
    assert fb.symbol_info("XAUUSD").contract_size == 100
