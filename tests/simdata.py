"""Hand-built market data for simulator tests and golden replay fixtures.

Times are written as "HH:MM:SS.fff" on a fixed UTC day so fixtures read like a tape.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import polars as pl

from aurum.backtest.broker import SimBroker, SimCosts
from aurum.backtest.market import HistoricalMarket, TickDay
from aurum.core.clock import ManualClock
from aurum.core.models import SymbolSpec
from aurum.core.timeframes import Timeframe
from aurum.data.schemas import CANONICAL_BARS, polars_schema
from aurum.features.view import BarSeries, to_ms

DAY = date(2026, 9, 15)  # a Tuesday: BST (London = UTC+1) and EDT (New York = UTC-4)


def at(hms: str, day: date = DAY) -> datetime:
    """UTC instant of "HH:MM[:SS[.fff]]" on ``day``."""
    return datetime.combine(day, time.fromisoformat(hms), tzinfo=UTC)


def spec(*, stops_level: int = 0, freeze_level: int = 0, volume_min: float = 0.01) -> SymbolSpec:
    """Fusion's XAUUSD contract (spec snapshot 2026-10-09) with adjustable levels."""
    return SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        contract_size=100.0,
        volume_min=volume_min,
        volume_max=100.0,
        volume_step=0.01,
        stops_level_points=stops_level,
        freeze_level_points=freeze_level,
    )


TickRow = tuple[str, float, float]
"""("HH:MM:SS.fff", bid, ask)"""
BarRow = tuple[str, float, float, float, float, int]
"""("HH:MM", open, high, low, close, spread_min points) of an M1 bar."""


def tick_day(rows: Sequence[TickRow], day: date = DAY) -> TickDay:
    return TickDay(
        time=np.array([to_ms(at(t, day)) for t, _, _ in rows], dtype=np.int64),
        bid=np.array([b for _, b, _ in rows], dtype=np.float64),
        ask=np.array([a for _, _, a in rows], dtype=np.float64),
    )


def bars_frame(timeframe: Timeframe, rows: Sequence[BarRow], day: date = DAY) -> pl.DataFrame:
    out = []
    for t, o, h, lo, c, spread in rows:
        open_time = at(t, day)
        out.append(
            {
                "open_time": open_time,
                "close_time": open_time + timeframe.duration,
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "tick_count": 1,
                "quote_count": None,
                "spread_open": None,
                "spread_min": spread,
                "spread_max": None,
                "spread_mean": None,
                "spread_close": None,
                "source": "fixture",
            }
        )
    return pl.DataFrame(out, schema=polars_schema(CANONICAL_BARS))


def m1_from_ticks(ticks: Sequence[TickRow], day: date = DAY, point: float = 0.01) -> list[BarRow]:
    """M1 bars (bid OHLC, minimum spread) built from a tick tape, like aurum.data.resample."""
    by_minute: dict[str, list[TickRow]] = {}
    for row in ticks:
        by_minute.setdefault(row[0][:5], []).append(row)
    out: list[BarRow] = []
    for minute in sorted(by_minute):
        bids = [b for _, b, _ in by_minute[minute]]
        spreads = [round((a - b) / point) for _, b, a in by_minute[minute]]
        out.append((minute, bids[0], max(bids), min(bids), bids[-1], min(spreads)))
    return out


def market(
    *,
    m1: Sequence[BarRow] = (),
    ticks: dict[date, Sequence[TickRow]] | None = None,
    extra_points: int = 0,
    higher: dict[Timeframe, Sequence[BarRow]] | None = None,
    day: date = DAY,
) -> HistoricalMarket:
    """A market from M1 rows on ``day`` and optional tick tapes (their days run in TICKS mode).

    If tick tapes are given and no M1 rows, M1 bars are derived from the ticks.
    """
    tapes = {d: tick_day(rows, d) for d, rows in (ticks or {}).items()}
    rows = list(m1) or [r for d, t in sorted((ticks or {}).items()) for r in m1_from_ticks(t, d)]
    bars = {Timeframe.M1: BarSeries.from_frame(Timeframe.M1, bars_frame(Timeframe.M1, rows, day))}
    for tf, tf_rows in (higher or {}).items():
        bars[tf] = BarSeries.from_frame(tf, bars_frame(tf, tf_rows, day))
    return HistoricalMarket(
        symbol="XAUUSD",
        point=0.01,
        bars=bars,
        tick_days=frozenset(tapes),
        tick_loader=tapes.__getitem__ if tapes else None,
        bar_spread_extra_points=extra_points,
    )


def sim(
    mkt: HistoricalMarket,
    *,
    start: str = "00:00",
    commission: float = 2.25,
    slippage: float = 0.0,
    latency_ms: int = 0,
    balance: float = 10_000.0,
    leverage: int = 500,
    symbol_spec: SymbolSpec | None = None,
    day: date = DAY,
    **kwargs: object,
) -> tuple[SimBroker, ManualClock]:
    clock = ManualClock(at(start, day))
    broker = SimBroker(
        market=mkt,
        spec=symbol_spec or spec(),
        clock=clock,
        costs=SimCosts(
            commission_per_lot_side=commission,
            slippage_points=slippage,
            latency=timedelta(milliseconds=latency_ms),
        ),
        balance=balance,
        leverage=leverage,
        **kwargs,  # type: ignore[arg-type]
    )
    broker.connect()
    return broker, clock
