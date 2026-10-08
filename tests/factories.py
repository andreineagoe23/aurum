"""Builders for domain objects used across tests."""

from datetime import UTC, datetime, timedelta

from aurum.core.enums import OrderType, Side
from aurum.core.models import OrderIntent, RiskDecision, Signal, Tick

T0 = datetime(2026, 3, 30, 8, 0, tzinfo=UTC)


def tick(bid: float = 2000.00, ask: float = 2000.20, t: datetime = T0) -> Tick:
    return Tick(symbol="XAUUSD", time=t, bid=bid, ask=ask)


def signal(
    side: Side = Side.BUY,
    *,
    t: datetime = T0,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    entry_type: OrderType = OrderType.MARKET,
    entry_price: float | None = None,
) -> Signal:
    default_sl = 1995.0 if side is Side.BUY else 2005.0
    return Signal(
        strategy_id="test_strategy",
        symbol="XAUUSD",
        side=side,
        time=t,
        data_as_of=t - timedelta(minutes=5),
        entry_type=entry_type,
        entry_price=entry_price,
        stop_loss=stop_loss if stop_loss is not None else default_sl,
        take_profit=take_profit,
        reasons=("m15 close 2001.50 > h1 ema(50) 1998.20",),
    )


def approve(sig: Signal, volume: float = 0.10) -> RiskDecision:
    return RiskDecision(
        decision_id=f"rd-{sig.key}",
        signal_key=sig.key,
        approved=True,
        volume=volume,
        stop_distance_points=500,
        risk_amount=50.0,
        decided_at=sig.time,
    )


def reject(sig: Signal) -> RiskDecision:
    return RiskDecision(
        decision_id=f"rd-{sig.key}",
        signal_key=sig.key,
        approved=False,
        volume=0.0,
        reasons=("spread 45 points > max 30",),
        decided_at=sig.time,
    )


def intent(side: Side = Side.BUY, volume: float = 0.10, **kwargs: object) -> OrderIntent:
    sig = signal(side, **kwargs)  # type: ignore[arg-type]
    return OrderIntent.from_signal(sig, approve(sig, volume), created_at=sig.time)
