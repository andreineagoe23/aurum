"""History depth probe (`aurum data depth`): how far back the broker serves bars and ticks.

Phase 2 measured the depth once (reports/phase_2.md section 1). This command repeats it, so the
owner can see whether the tick window is rolling (start moves forward) and whether stored days
still match what the server serves now (thinning of older ticks, ADR-0007).

* **Bars:** the whole history per timeframe; bars per year; "continuous since" = the day after
  the last trading date (MarketHours) with fewer than half the typical number of bars.
* **Ticks:** one-hour probes at 14:00 UTC on trading dates, stepping back a week at a time until
  one is empty, then day by day forward to the first day with ticks.
* **Re-fetch check:** for given stored days, the row count the server returns now vs the
  stored raw file.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl

from aurum.core.timeframes import Timeframe
from aurum.data.market_hours import MarketHours
from aurum.ports.market_data import MarketDataSource

EPOCH_PROBE = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass
class BarDepth:
    timeframe: str
    bars: int
    first_bar: datetime | None
    continuous_since: date | None
    bars_per_year: dict[int, int]


@dataclass
class DepthResult:
    measured_at: datetime
    bars: list[BarDepth]
    first_tick: datetime | None
    tick_probes: list[dict[str, Any]] = field(default_factory=list)
    refetch: list[dict[str, Any]] = field(default_factory=list)


def continuous_since(daily_counts: dict[date, int], market: MarketHours, last: date) -> date | None:
    """Day after the last trading date with fewer than half the typical bar count."""
    if not daily_counts:
        return None
    first = min(daily_counts)
    dates = [d for d in market.trading_dates(first, last) if market.cfg.early_closes.get(d) is None]
    recent = [daily_counts.get(d, 0) for d in dates[-250:]]
    typical = sorted(recent)[len(recent) // 2] if recent else 0
    sparse = [d for d in dates if daily_counts.get(d, 0) < typical / 2]
    if not sparse:
        return first
    after = [d for d in dates if d > sparse[-1]]
    return after[0] if after else None


def bar_depth(
    source: MarketDataSource, symbol: str, tf: Timeframe, market: MarketHours, now: datetime
) -> BarDepth:
    frame = source.rate_frame(symbol, tf, EPOCH_PROBE, now)
    times = frame["open_time"]
    # Trading date = New York date of (time + 6h): 18:00 New York belongs to the next date.
    ny = frame.select(
        (pl.col("open_time").dt.convert_time_zone(market.cfg.timezone) + pl.duration(hours=6))
        .dt.date()
        .alias("d")
    )["d"]
    daily = Counter(ny.to_list())
    first = times.min() if frame.height else None
    return BarDepth(
        timeframe=tf.value,
        bars=frame.height,
        first_bar=first if isinstance(first, datetime) else None,
        continuous_since=continuous_since(dict(daily), market, now.date()),
        bars_per_year=dict(sorted(Counter(t.year for t in times.to_list()).items())),
    )


def _probe(source: MarketDataSource, symbol: str, d: date) -> int:
    start = datetime(d.year, d.month, d.day, 14, tzinfo=UTC)
    return source.tick_frame(symbol, start, start + timedelta(hours=1)).height


def first_tick(
    source: MarketDataSource, symbol: str, market: MarketHours, today: date, *, max_weeks: int = 800
) -> tuple[datetime | None, list[dict[str, Any]]]:
    probes: list[dict[str, Any]] = []
    d = today - timedelta(days=1)
    newest_with_ticks: date | None = None
    oldest_empty: date | None = None
    for _ in range(max_weeks):
        if market.session(d) is not None:
            n = _probe(source, symbol, d)
            probes.append({"date": d, "ticks_14_15_utc": n})
            if n:
                newest_with_ticks = d
            else:
                oldest_empty = d
                break
        d -= timedelta(days=7) if newest_with_ticks else timedelta(days=1)
    if newest_with_ticks is None:
        return None, probes
    d = oldest_empty or newest_with_ticks
    while d <= newest_with_ticks:
        start = datetime(d.year, d.month, d.day, tzinfo=UTC)
        frame = source.tick_frame(symbol, start, start + timedelta(days=1))
        if frame.height:
            t = frame["time"].min()
            return (t if isinstance(t, datetime) else None), probes
        d += timedelta(days=1)
    return None, probes


def refetch_check(
    source: MarketDataSource, symbol: str, stored: dict[date, int]
) -> list[dict[str, Any]]:
    out = []
    for d, rows in sorted(stored.items()):
        start = datetime(d.year, d.month, d.day, tzinfo=UTC)
        now_rows = source.tick_frame(symbol, start, start + timedelta(days=1)).height
        out.append({"day": d, "stored_rows": rows, "server_rows_now": now_rows})
    return out


def measure_depth(
    source: MarketDataSource,
    symbol: str,
    market: MarketHours,
    now: datetime,
    *,
    refetch_days: dict[date, int] | None = None,
) -> DepthResult:
    bars = [bar_depth(source, symbol, tf, market, now) for tf in Timeframe]
    tick, probes = first_tick(source, symbol, market, now.date())
    return DepthResult(
        measured_at=now,
        bars=bars,
        first_tick=tick,
        tick_probes=probes,
        refetch=refetch_check(source, symbol, refetch_days or {}),
    )
