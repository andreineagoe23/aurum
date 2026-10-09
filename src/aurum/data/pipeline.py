"""Build derived datasets for complete UTC days (`aurum data resample`).

For each day:

1. **Pick the tick source:** ``raw_ticks/mt5_history`` if that day exists, else the live
   collector's chunks (``mt5_live``). History wins because it is the broker's complete record;
   data QA compares the two wherever both exist.
2. If there are ticks: normalize -> ``normalized_ticks`` -> M1 from ticks, checked against the
   broker's M1. If they agree exactly (tick fidelity ``exact``) the tick-built M1 is canonical.
   If not (``incomplete``: the broker's stored tick history for that day is thinned or has
   holes, ADR-0007), canonical M1 is the broker's M1 instead. Normalized ticks are kept either
   way. Without ticks (before tick history began): canonical M1 from the broker's M1 bars.
3. Canonical M5/M15/H1 from canonical M1, each compared with the broker's bars of that
   timeframe.
4. ``canonical_bars/days/<symbol>.parquet`` records, per day, which source the canonical bars
   came from and the tick fidelity, so later phases can tell exact tick days from the rest.

Derived files are rewritten on every run (``overwrite=True``). They are deterministic, so an
unchanged input gives byte-identical output; ``aurum data hashes`` proves it. Raw is only read.
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import polars as pl
import structlog

from aurum.adapters.storage.layout import MT5_HISTORY, MT5_LIVE, DataLayout, days_in
from aurum.adapters.storage.live_ticks import LiveTickStore
from aurum.adapters.storage.parquet import read_parquet, write_parquet
from aurum.core.timeframes import Timeframe
from aurum.data.compare import BarComparison, compare_bars
from aurum.data.normalize import NormalizeStats, normalize_ticks
from aurum.data.resample import (
    BROKER_M1_SOURCE,
    aggregate,
    broker_m1_to_canonical,
    ticks_to_m1,
)
from aurum.data.schemas import (
    CANONICAL_BARS,
    DAY_SOURCES,
    NORMALIZED_TICKS,
    RAW_BARS,
    RAW_TICKS,
    polars_schema,
)

log = structlog.get_logger(__name__)

EXACT, INCOMPLETE, UNVERIFIED = "exact", "incomplete", "unverified"


@dataclass
class DayResult:
    day: date
    source: str | None
    """mt5_history / mt5_live (ticks) or mt5_bars_m1; None if the day has no data at all."""
    ticks: NormalizeStats | None = None
    tick_fidelity: str | None = None
    """exact: tick-built M1 equals the broker's M1; incomplete: it does not (canonical bars then
    come from broker M1); unverified: no broker M1 to check against; None: no ticks."""
    tick_vs_broker: BarComparison | None = None
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
    broker_m1 = _raw_bars(layout, symbol, Timeframe.M1, day)
    m1: pl.DataFrame | None = None
    if raw is not None and not raw[0].is_empty():
        ticks, result.ticks = normalize_ticks(
            raw[0], point=point, source=raw[1], outlier_factor=outlier_factor
        )
        write_parquet(ticks, layout.normalized_ticks(symbol, day), NORMALIZED_TICKS, overwrite=True)
        m1, result.source = ticks_to_m1(ticks), raw[1]
        if broker_m1 is None:
            result.tick_fidelity = UNVERIFIED
        else:
            check = compare_bars(m1, broker_m1, point=point)
            result.tick_vs_broker = check
            if check.ohlc_mismatch or check.only_broker or check.only_canonical:
                # The stored ticks do not reproduce the broker's own bars (thinned or holed
                # tick history, ADR-0007): the broker's M1 is the better record of that day.
                result.tick_fidelity = INCOMPLETE
                m1, result.source = broker_m1_to_canonical(broker_m1), BROKER_M1_SOURCE
            else:
                result.tick_fidelity = EXACT
    elif broker_m1 is not None and not broker_m1.is_empty():
        m1, result.source = broker_m1_to_canonical(broker_m1), BROKER_M1_SOURCE
    elif broker_m1 is not None:
        m1 = broker_m1_to_canonical(broker_m1)  # empty day (holiday): empty bar files
    if m1 is None:
        return result
    for tf in Timeframe:
        bars = aggregate(m1, tf)
        write_parquet(bars, layout.canonical_bars(symbol, tf, day), CANONICAL_BARS, overwrite=True)
        result.bars[tf.value] = bars.height
        broker = broker_m1 if tf is Timeframe.M1 else _raw_bars(layout, symbol, tf, day)
        if broker is not None:
            result.comparisons[tf.value] = compare_bars(bars, broker, point=point)
    return result


def _manifest_rows(results: list[DayResult]) -> pl.DataFrame:
    def count(r: DayResult, name: str) -> int | None:
        return None if r.tick_vs_broker is None else int(getattr(r.tick_vs_broker, name))

    return pl.DataFrame(
        {
            "day": [r.day for r in results],
            "m1_source": [r.source for r in results],
            "tick_source": [r.ticks.source if r.ticks else None for r in results],
            "tick_rows": [r.ticks.kept if r.ticks else None for r in results],
            "tick_fidelity": [r.tick_fidelity for r in results],
            "tick_m1_mismatch": [count(r, "ohlc_mismatch") for r in results],
            "broker_only_minutes": [count(r, "only_broker") for r in results],
            "tick_only_minutes": [count(r, "only_canonical") for r in results],
        },
        schema=polars_schema(DAY_SOURCES),
    )


def write_day_manifest(layout: DataLayout, symbol: str, results: list[DayResult]) -> Path:
    """Merge ``results`` into ``canonical_bars/days/<symbol>.parquet`` (one row per UTC day)."""
    path = layout.day_manifest(symbol)
    fresh = _manifest_rows(results)
    if path.exists():
        old = read_parquet(path, DAY_SOURCES)
        fresh = pl.concat([old.filter(~pl.col("day").is_in(fresh["day"].implode())), fresh])
    return write_parquet(fresh.sort("day"), path, DAY_SOURCES, overwrite=True)


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
        log.info(
            "resample.day",
            day=day.isoformat(),
            source=r.source,
            tick_fidelity=r.tick_fidelity,
            bars=r.bars,
        )
    if results:
        write_day_manifest(layout, symbol, results)
    return results
