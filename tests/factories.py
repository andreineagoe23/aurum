"""Builders for domain objects used across tests."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from aurum.core.enums import OrderType, Side
from aurum.core.models import OrderIntent, RiskDecision, Signal, Tick
from aurum.core.timeframes import Timeframe

if TYPE_CHECKING:
    import polars as pl

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


def canonical_bars(
    timeframe: Timeframe,
    open_times: Sequence[datetime],
    closes: Sequence[float],
    *,
    spread_min: int | None = 5,
    width: float = 0.5,
) -> "pl.DataFrame":
    """Canonical-bar rows (aurum.data.schemas.CANONICAL_BARS) with open = previous close (first:
    close), high/low ``width`` beyond the body, and a constant minimum spread in points."""
    import polars as pl

    from aurum.data.schemas import CANONICAL_BARS, polars_schema

    rows = []
    prev = closes[0] if closes else 0.0
    for t, c in zip(open_times, closes, strict=True):
        o = prev
        rows.append(
            {
                "open_time": t,
                "close_time": t + timeframe.duration,
                "open": o,
                "high": max(o, c) + width,
                "low": min(o, c) - width,
                "close": c,
                "tick_count": 10,
                "quote_count": None,
                "spread_open": None,
                "spread_min": spread_min,
                "spread_max": None,
                "spread_mean": None,
                "spread_close": None,
                "source": "test",
            }
        )
        prev = c
    return pl.DataFrame(rows, schema=polars_schema(CANONICAL_BARS))
