"""Build derived datasets for complete UTC days (`aurum data resample`).

For each day:

1. **Pick the tick source:** ``raw_ticks/mt5_history`` if that day exists, else the live
   collector's chunks (``mt5_live``). History wins because it is the broker's complete record;
   data QA compares the two wherever both exist.
2. If there are ticks: normalize -> ``normalized_ticks`` -> canonical M1 from ticks.
   Otherwise (before tick history began): canonical M1 from the broker's M1 bars.
3. Canonical M5/M15/H1 from canonical M1, each compared with the broker's bars of that
   timeframe.

Derived files are rewritten on every run (``overwrite=True``). They are deterministic, so an
unchanged input gives byte-identical output; ``aurum data hashes`` proves it. Raw is only read.
"""

from dataclasses import dataclass, field
from datetime import date

import polars as pl
import structlog

from aurum.adapters.storage.layout import MT5_HISTORY, MT5_LIVE, DataLayout, days_in
from aurum.adapters.storage.live_ticks import LiveTickStore
from aurum.adapters.storage.parquet import read_parquet, write_parquet
from aurum.core.timeframes import Timeframe
from aurum.data.compare import BarComparison, compare_bars
from aurum.data.normalize import NormalizeStats, normalize_ticks
from aurum.data.resample import aggregate, broker_m1_to_canonical, ticks_to_m1
from aurum.data.schemas import CANONICAL_BARS, NORMALIZED_TICKS, RAW_BARS, RAW_TICKS

log = structlog.get_logger(__name__)


@dataclass
class DayResult:
    day: date
    source: str | None
    """mt5_history / mt5_live (ticks) or mt5_bars_m1; None if the day has no data at all."""
    ticks: NormalizeStats | None = None
    bars: dict[str, int] = field(default_factory=dict)
    comparisons: dict[str, BarComparison] = field(default_factory=dict)


def available_days(layout: DataLayout, symbol: str) -> list[date]:
    """Every UTC day with any raw data: history ticks, live ticks or broker M1 bars."""
    found = set(days_in(layout.raw_ticks_dir(symbol, MT5_HISTORY)))
    found |= set(LiveTickStore(layout, symbol).days())
    found |= set(days_in(layout.raw_bars_dir(symbol, Timeframe.M1)))
    return sorted(found)


def _raw_ticks(layout: DataLayout, symbol: str, day: date) -> tuple[pl.DataFrame, str] | None:
    history = layout.raw_ticks(symbol, day)
    if history.exists():
        return read_parquet(history, RAW_TICKS), MT5_HISTORY
    live = LiveTickStore(layout, symbol)
    if day in live.days():
        return live.read_day(day), MT5_LIVE
    return None


def _raw_bars(layout: DataLayout, symbol: str, tf: Timeframe, day: date) -> pl.DataFrame | None:
    path = layout.raw_bars(symbol, tf, day)
    return read_parquet(path, RAW_BARS) if path.exists() else None


def build_day(
    layout: DataLayout, symbol: str, day: date, *, point: float, outlier_factor: float
) -> DayResult:
    result = DayResult(day=day, source=None)
    raw = _raw_ticks(layout, symbol, day)
    m1: pl.DataFrame | None = None
    if raw is not None and not raw[0].is_empty():
        ticks, result.ticks = normalize_ticks(
            raw[0], point=point, source=raw[1], outlier_factor=outlier_factor
        )
        write_parquet(ticks, layout.normalized_ticks(symbol, day), NORMALIZED_TICKS, overwrite=True)
        m1, result.source = ticks_to_m1(ticks), raw[1]
    else:
        broker_m1 = _raw_bars(layout, symbol, Timeframe.M1, day)
        if broker_m1 is not None:
            m1 = broker_m1_to_canonical(broker_m1)
            result.source = "mt5_bars_m1" if not broker_m1.is_empty() else None
    if m1 is None:
        return result
    for tf in Timeframe:
        bars = aggregate(m1, tf)
        write_parquet(bars, layout.canonical_bars(symbol, tf, day), CANONICAL_BARS, overwrite=True)
        result.bars[tf.value] = bars.height
        broker = _raw_bars(layout, symbol, tf, day)
        if broker is not None:
            result.comparisons[tf.value] = compare_bars(bars, broker, point=point)
    return result


def build_range(
    layout: DataLayout,
    symbol: str,
    days: list[date],
    *,
    point: float,
    outlier_factor: float,
) -> list[DayResult]:
    results = []
    for day in days:
        r = build_day(layout, symbol, day, point=point, outlier_factor=outlier_factor)
        results.append(r)
        log.info("resample.day", day=day.isoformat(), source=r.source, bars=r.bars)
    return results
