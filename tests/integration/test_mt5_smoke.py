"""MT5 integration tests against the real terminal (Windows VM, DEMO account only).

* ``mt5``: read-only. Auto-skipped unless Windows + MetaTrader5 + a running terminal.
* ``mt5_orders``: sends minimum-volume DEMO orders; additionally needs
  ``AURUM_MT5_ORDER_TESTS=1``. The live-trading guard stays on: the account must report DEMO.

Market-hours checks skip (not fail) when the latest quote is stale (weekend, daily break).
"""

import dataclasses
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from aurum.adapters.mt5 import MT5Broker, is_available, load_mt5
from aurum.adapters.mt5.servertime import OffsetCheck
from aurum.core.clock import SystemClock
from aurum.core.enums import DealEntry, ExecutionStatus, Mode, Side
from aurum.core.models import OrderIntent, RiskDecision, Signal
from aurum.core.timeframes import Timeframe
from aurum.settings import Settings, load_settings

CLOCK = SystemClock()


@pytest.mark.mt5
def test_metatrader5_package_loads() -> None:
    assert is_available()
    assert hasattr(load_mt5(), "initialize")


# --------------------------------------------------------------------------- fixtures


def demo_settings() -> Settings:
    """Repo settings with AURUM_MODE forced to DEMO for this process (the guard still checks
    that the account itself is DEMO before every write)."""
    s = load_settings()
    return dataclasses.replace(s, env=s.env.model_copy(update={"aurum_mode": Mode.DEMO}))


@pytest.fixture
def broker() -> Iterator[MT5Broker]:
    b = MT5Broker(load_settings())
    b.connect()
    yield b
    b.disconnect()


@pytest.fixture
def symbol(broker: MT5Broker) -> str:
    return broker.resolve_symbol()


def require_open_market(broker: MT5Broker) -> None:
    check = broker.server_time_check
    if check is None or check.status is not OffsetCheck.VERIFIED:
        pytest.skip("latest quote is stale (market closed): live-market checks skipped")


# --------------------------------------------------------------------------- read-only


@pytest.mark.mt5
def test_ping_is_demo(broker: MT5Broker) -> None:
    account = broker.account_info()
    assert account.is_demo
    assert account.currency  # auto-detected (GBP on the Phase 1 demo)


@pytest.mark.mt5
def test_server_clock_rule_verified(broker: MT5Broker) -> None:
    require_open_market(broker)
    check = broker.server_time_check
    assert check is not None
    assert abs(check.residual) < timedelta(seconds=120)


@pytest.mark.mt5
def test_latest_tick_is_fresh_utc(broker: MT5Broker, symbol: str) -> None:
    require_open_market(broker)
    tick = broker.latest_tick(symbol)
    now = CLOCK.now()
    assert now - timedelta(seconds=60) <= tick.time <= now + timedelta(seconds=2)
    assert tick.ask >= tick.bid


@pytest.mark.mt5
@pytest.mark.parametrize("tf", [Timeframe.M5, Timeframe.M15, Timeframe.H1])
def test_latest_completed_bar_is_the_one_that_just_closed(
    broker: MT5Broker, symbol: str, tf: Timeframe
) -> None:
    require_open_market(broker)
    now = CLOCK.now()
    bars = broker.bars(symbol, tf, now - timedelta(hours=6), now)
    assert bars, "no bars in the last 6 hours while the market is open"
    assert all(b.close_time <= now for b in bars)
    expected = tf.last_completed_bar(now)
    if (now - expected.close_time) < timedelta(seconds=5):
        pytest.skip("bar boundary right now; terminal may not have it yet")
    assert bars[-1].open_time == expected.open_time


@pytest.mark.mt5
def test_ticks_inside_bar_match_bar_range(broker: MT5Broker, symbol: str) -> None:
    require_open_market(broker)
    now = CLOCK.now()
    bar = broker.bars(symbol, Timeframe.M1, now - timedelta(minutes=10), now)[-1]
    ticks = broker.ticks(symbol, bar.open_time, bar.close_time)
    assert ticks, "no ticks inside the last completed M1 bar"
    bids = [t.bid for t in ticks]
    # MT5 bars are bid bars: same open and extremes as the ticks that fall inside them.
    assert bids[0] == pytest.approx(bar.open)
    assert max(bids) == pytest.approx(bar.high)
    assert min(bids) == pytest.approx(bar.low)


@pytest.mark.mt5
def test_daily_break_is_server_midnight_in_utc(broker: MT5Broker, symbol: str) -> None:
    """The gold daily break (17:00-18:00 New York) must land on the UTC hour our rule predicts,
    including in the weeks where US and EU DST disagree (ADR-0004)."""
    # Mondays in the US-DST-but-not-EU-DST windows; the server is UTC+3 then, so the break
    # (server 00:00) is 21:00 UTC. An EET server would put it at 22:00 UTC.
    for monday in (datetime(2026, 3, 9, tzinfo=UTC), datetime(2025, 10, 27, tzinfo=UTC)):
        bars = broker.bars(symbol, Timeframe.H1, monday, monday + timedelta(days=4))
        hours_by_day = Counter(b.open_time.date() for b in bars)
        assert set(hours_by_day.values()) == {23}, hours_by_day
        assert {b.open_time.hour for b in bars} == set(range(24)) - {21}


# --------------------------------------------------------------------------- demo orders


def smoke_intent(broker: MT5Broker, symbol: str, tag: str) -> OrderIntent:
    """Minimum-volume BUY with SL/TP 10.00 away from the current ask (~1000 points)."""
    spec = broker.symbol_info(symbol)
    tick = broker.latest_tick(symbol)
    now = CLOCK.now()
    signal = Signal(
        strategy_id=f"it_{tag}",
        symbol=symbol,
        side=Side.BUY,
        time=now,
        data_as_of=tick.time,
        stop_loss=spec.round_price(tick.ask - 10),
        take_profit=spec.round_price(tick.ask + 10),
        reasons=(f"integration test {tag}: minimum volume demo order",),
    )
    decision = RiskDecision(
        decision_id=f"it-{signal.key}",
        signal_key=signal.key,
        approved=True,
        volume=spec.volume_min,
        decided_at=now,
    )
    return OrderIntent.from_signal(
        signal, decision, created_at=now, magic=broker._broker_cfg.magic_number
    )


@pytest.fixture
def demo_broker() -> Iterator[MT5Broker]:
    b = MT5Broker(demo_settings())
    b.connect()
    assert b.account_info().is_demo, "refusing: not a DEMO account"
    require_open_market(b)
    yield b
    # Safety net: never leave a test position behind.
    for p in b.positions():
        if p.idempotency_key and p.magic == b._broker_cfg.magic_number:
            b.close_position(p.position_id)
    b.disconnect()


@pytest.mark.mt5
@pytest.mark.mt5_orders
def test_demo_open_modify_close_reconcile(demo_broker: MT5Broker) -> None:
    b = demo_broker
    symbol = b.resolve_symbol()
    intent = smoke_intent(b, symbol, "smoke")
    balance_before = b.account_info().balance

    assert b.check_order(intent).ok

    opened = b.send_order(intent)
    assert opened.status is ExecutionStatus.FILLED, opened.message
    assert opened.position_id is not None
    (pos,) = [p for p in b.positions(symbol) if p.position_id == opened.position_id]
    assert pos.idempotency_key == intent.idempotency_key  # comment survived the broker
    assert pos.stop_loss == intent.stop_loss
    assert abs(pos.open_time - CLOCK.now()) < timedelta(seconds=30)  # UTC, not server time

    new_sl = b.symbol_info(symbol).round_price(intent.stop_loss - 5)
    modified = b.modify_position(pos.position_id, new_sl, intent.take_profit)
    assert modified.status is ExecutionStatus.MODIFIED, modified.message
    assert b.positions(symbol)[0].stop_loss == new_sl

    closed = b.close_position(pos.position_id)
    assert closed.status is ExecutionStatus.CLOSED, closed.message
    assert all(p.position_id != pos.position_id for p in b.positions(symbol))

    deals = b.position_deals(pos.position_id)
    assert [d.entry for d in deals] == [DealEntry.IN, DealEntry.OUT]
    assert all(d.idempotency_key == intent.idempotency_key for d in deals)
    window = b.history_deals(
        CLOCK.now() - timedelta(minutes=10), CLOCK.now() + timedelta(seconds=5)
    )
    assert {d.deal_id for d in deals} <= {d.deal_id for d in window}
    pnl = sum(d.profit + d.commission for d in deals)
    assert b.account_info().balance == pytest.approx(balance_before + pnl, abs=0.011)

    assert b.send_order(intent).duplicate  # reconciliation: same key never trades twice


@pytest.mark.mt5
@pytest.mark.mt5_orders
def test_twenty_reconnect_cycles_never_duplicate(demo_broker: MT5Broker) -> None:
    b = demo_broker
    symbol = b.resolve_symbol()
    intent = smoke_intent(b, symbol, "reconnect")
    first = b.send_order(intent)
    assert first.status is ExecutionStatus.FILLED, first.message
    try:
        for cycle in range(20):
            b.disconnect()
            assert not b.is_connected()
            b.connect()
            assert b.is_connected(), f"cycle {cycle}"
            again = b.send_order(intent)  # a restarted process re-deciding the same signal
            assert again.duplicate, f"cycle {cycle}: {again}"
            assert again.position_id == first.position_id
        mine = [p for p in b.positions(symbol) if p.idempotency_key == intent.idempotency_key]
        assert len(mine) == 1
    finally:
        assert first.position_id is not None
        b.close_position(first.position_id)
    deals = b.position_deals(first.position_id)
    assert [d.entry for d in deals] == [DealEntry.IN, DealEntry.OUT]
