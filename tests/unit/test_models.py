from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from aurum.core.enums import OrderType, Side
from aurum.core.models import Bar, MarketEvent, OrderIntent, RiskDecision, SymbolSpec, Tick
from aurum.core.timeframes import Timeframe
from tests.factories import T0, approve, intent, reject, signal, tick


def bar(open_time: datetime = datetime(2026, 3, 30, 9, tzinfo=UTC), **kw: float) -> Bar:
    values = {"open": 2000.0, "high": 2003.0, "low": 1998.0, "close": 2001.0} | kw
    return Bar(symbol="XAUUSD", timeframe=Timeframe.H1, open_time=open_time, **values)


# --------------------------------------------------------------------------- time rules


def test_model_conventions() -> None:
    """Aware UTC times only, frozen, unknown fields rejected (CLAUDE.md conventions)."""
    from zoneinfo import ZoneInfo

    naive = datetime(2026, 3, 30, 8, 0)  # noqa: DTZ001
    with pytest.raises(ValidationError, match="timezone"):
        Tick(symbol="XAUUSD", time=naive, bid=1, ask=1.1)
    london = datetime(2026, 7, 1, 9, tzinfo=ZoneInfo("Europe/London"))
    t = Tick(symbol="XAUUSD", time=london, bid=1, ask=1.1)
    assert t.time == datetime(2026, 7, 1, 8, tzinfo=UTC)
    assert t.time.tzinfo is UTC
    with pytest.raises(ValidationError):
        t.bid = 1.0  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Tick(symbol="XAUUSD", time=T0, bid=1, ask=2, foo=1)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- market data


def test_tick_executable_prices() -> None:
    t = tick(bid=2000.0, ask=2000.3)
    assert t.price_for(Side.BUY) == 2000.3
    assert t.price_for(Side.SELL) == 2000.0
    assert t.exit_price_for(Side.BUY) == 2000.0
    assert t.exit_price_for(Side.SELL) == 2000.3
    assert t.spread == pytest.approx(0.3)


def test_bar_close_time_and_validation() -> None:
    b = bar()
    assert b.close_time == datetime(2026, 3, 30, 10, tzinfo=UTC)
    with pytest.raises(ValidationError, match="not aligned"):
        bar(open_time=datetime(2026, 3, 30, 9, 15, tzinfo=UTC))
    with pytest.raises(ValidationError, match="inconsistent OHLC"):
        bar(high=1999.0)
    with pytest.raises(ValidationError, match="inconsistent OHLC"):
        bar(low=2000.5)


def test_market_event_rules() -> None:
    b = bar()
    MarketEvent(time=b.close_time, symbol="XAUUSD", bar=b)
    with pytest.raises(ValidationError, match="look-ahead"):
        MarketEvent(time=b.close_time - timedelta(seconds=1), symbol="XAUUSD", bar=b)
    t = tick()
    MarketEvent(time=t.time, symbol="XAUUSD", tick=t)
    with pytest.raises(ValidationError, match="before its quote time"):
        MarketEvent(time=t.time - timedelta(milliseconds=1), symbol="XAUUSD", tick=t)
    with pytest.raises(ValidationError, match="exactly one"):
        MarketEvent(time=t.time, symbol="XAUUSD")
    with pytest.raises(ValidationError, match="exactly one"):
        MarketEvent(time=bar().close_time, symbol="XAUUSD", tick=t, bar=bar())
    with pytest.raises(ValidationError, match="symbol mismatch"):
        MarketEvent(time=t.time, symbol="EURUSD", tick=t)


def test_symbol_spec_helpers() -> None:
    spec = SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        contract_size=100,
        volume_min=0.01,
        volume_max=50,
        volume_step=0.01,
    )
    assert spec.money_per_price_unit_per_lot == pytest.approx(100.0)
    assert spec.price_to_points(1.25) == pytest.approx(125)
    assert spec.points_to_price(30) == pytest.approx(0.30)
    assert spec.round_price(2000.123) == 2000.12
    assert spec.is_valid_volume(0.07)
    assert not spec.is_valid_volume(0.075)
    assert not spec.is_valid_volume(0.001)
    assert not spec.is_valid_volume(51)
    with pytest.raises(ValidationError):
        spec.model_copy(update={}).model_validate(spec.model_dump() | {"volume_min": 100})


# --------------------------------------------------------------------------- decisions


def test_signal_validation() -> None:
    sig = signal()
    with pytest.raises(ValidationError, match="after decision time"):  # causality
        sig.model_validate(sig.model_dump() | {"data_as_of": sig.time + timedelta(seconds=1)})
    with pytest.raises(ValidationError):  # objective reasons are mandatory
        sig.model_validate(sig.model_dump() | {"reasons": ()})
    for side, sl, tp in [
        (Side.BUY, 2001.0, None),
        (Side.BUY, 1995.0, 1999.0),
        (Side.SELL, 1999.0, None),
        (Side.SELL, 2005.0, 2001.0),
    ]:
        with pytest.raises(ValidationError, match="beyond entry"):
            signal(
                side, stop_loss=sl, take_profit=tp, entry_type=OrderType.LIMIT, entry_price=2000.0
            )


def test_signal_key_is_deterministic() -> None:
    assert signal().key == signal().key
    assert signal().key != signal(t=T0 + timedelta(minutes=5)).key
    assert signal(Side.BUY).key != signal(Side.SELL).key


def test_risk_decision_invariants() -> None:
    sig = signal()
    with pytest.raises(ValidationError, match="volume > 0"):
        RiskDecision(decision_id="d", signal_key=sig.key, approved=True, volume=0, decided_at=T0)
    with pytest.raises(ValidationError, match="volume 0"):
        RiskDecision(
            decision_id="d",
            signal_key=sig.key,
            approved=False,
            volume=0.1,
            reasons=("x",),
            decided_at=T0,
        )
    with pytest.raises(ValidationError, match="state reasons"):
        RiskDecision(decision_id="d", signal_key=sig.key, approved=False, volume=0, decided_at=T0)


# --------------------------------------------------------------------------- order intent safety


def test_order_intent_cannot_be_built_from_rejected_decision() -> None:
    sig = signal()
    with pytest.raises(ValidationError):
        OrderIntent.from_signal(sig, reject(sig), created_at=T0)
    good = OrderIntent.from_signal(sig, approve(sig), created_at=T0)
    with pytest.raises(ValidationError, match="approved RiskDecision"):
        OrderIntent.model_validate(good.model_dump() | {"risk_decision": reject(sig)})


def test_order_intent_cannot_exceed_approved_volume() -> None:
    sig = signal()
    good = OrderIntent.from_signal(sig, approve(sig, 0.10), created_at=T0)
    with pytest.raises(ValidationError, match="exceeds approved volume"):
        OrderIntent.model_validate(good.model_dump() | {"volume": 0.11})


def test_order_intent_always_has_stop_loss() -> None:
    good = intent()
    data = good.model_dump()
    del data["stop_loss"]
    with pytest.raises(ValidationError, match="stop_loss"):
        OrderIntent.model_validate(data)
    with pytest.raises(ValidationError):
        OrderIntent.model_validate(good.model_dump() | {"stop_loss": None})


def test_order_intent_decision_must_match_signal() -> None:
    a, b = signal(), signal(t=T0 + timedelta(minutes=5))
    with pytest.raises(ValueError, match="does not belong"):
        OrderIntent.from_signal(a, approve(b), created_at=T0)


def test_idempotency_key_derived_from_signal_and_fits_mt5_comment() -> None:
    i = intent()
    assert i.idempotency_key == f"AU{signal().key}"
    assert len(i.idempotency_key) <= 29
    assert intent().idempotency_key == i.idempotency_key  # same signal -> same key


def test_pending_order_needs_price() -> None:
    sig = signal(entry_type=OrderType.LIMIT, entry_price=1999.0)
    i = OrderIntent.from_signal(sig, approve(sig), created_at=T0)
    with pytest.raises(ValidationError, match="needs a price"):
        OrderIntent.model_validate(i.model_dump() | {"price": None})
