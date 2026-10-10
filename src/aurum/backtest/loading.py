"""Load the Phase 2 store into a ``HistoricalMarket`` for a backtest period.

* Canonical bars (M1/M5/M15/H1) for UTC days ``lo .. last + 1``. ``lo`` is
  ``first - warmup_days``, or earlier when the strategy declares ``warmup_bars``: whole UTC days
  before the first decision are counted back (Parquet row counts, no data read) until every
  timeframe has its bars. Too little history is an error, never a short warm-up.
* Normalized ticks only for days in ``first..last`` whose tick fidelity is ``exact``
  (``canonical_bars/days``, ADR-0007), loaded lazily one day at a time.
* Backtests pass the sealed final-test period as ``forbidden`` date ranges: any data file dated
  inside one is refused **before it is read** (`SealedPeriodError`), whether it would serve
  warm-up, the period or the extra end day.
* A SHA-256 over every input file (path relative to the data dir + file hash), recorded in the
  run manifest so a result can be tied to the exact data it saw.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from aurum.adapters.storage.layout import DataLayout, days_in
from aurum.adapters.storage.parquet import read_parquet, sha256_file
from aurum.backtest.market import HistoricalMarket, TickDay
from aurum.core.errors import SealedPeriodError
from aurum.core.timeframes import Timeframe
from aurum.data.schemas import CANONICAL_BARS, DAY_SOURCES
from aurum.features.view import BarSeries

TIMEFRAMES = (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1)


@dataclass(frozen=True)
class LoadedMarket:
    market: HistoricalMarket
    files: list[Path]
    """Every input file, sorted."""
    tick_days: list[date]
    bar_days: list[date]
    """Days in ``first..last`` with M1 bars but no exact ticks (BARS mode)."""
    first_data_day: date
    """Start of the load range: the earliest UTC day any bar file could come from."""

    def data_hash(self, root: Path) -> str:
        h = hashlib.sha256()
        for f in self.files:
            h.update(f.relative_to(root).as_posix().encode())
            h.update(sha256_file(f).encode())
        return h.hexdigest()


class TickLoader:
    """Reads one day of normalized ticks (time, bid, ask) on demand."""

    def __init__(self, layout: DataLayout, symbol: str) -> None:
        self._layout = layout
        self._symbol = symbol

    def __call__(self, day: date) -> TickDay:
        df = pl.read_parquet(
            self._layout.normalized_ticks(self._symbol, day), columns=["time", "bid", "ask"]
        )
        return TickDay(
            time=df["time"].dt.epoch("ms").to_numpy().astype(np.int64),
            bid=df["bid"].to_numpy().astype(np.float64),
            ask=df["ask"].to_numpy().astype(np.float64),
        )


def load_market(
    layout: DataLayout,
    symbol: str,
    *,
    point: float,
    first: date,
    last: date,
    warmup_days: int,
    bar_spread_extra_points: int,
    last_data_day: date | None = None,
    forbidden: Sequence[tuple[date, date]] = (),
    warmup_bars: Mapping[Timeframe, int] | None = None,
    warmup_before: date | None = None,
) -> LoadedMarket:
    """``last_data_day`` defaults to ``last + 1`` (UTC). ``forbidden``: date ranges no file may
    come from.
    ``warmup_bars``: completed bars per timeframe needed from whole UTC days before
    ``warmup_before`` (the UTC date of the first decision instant; default ``first``)."""
    if last < first:
        raise ValueError(f"empty period {first}..{last}")
    lo = first - timedelta(days=warmup_days)
    hi = last + timedelta(days=1) if last_data_day is None else last_data_day

    def refuse_sealed(days: list[date]) -> None:
        hit = sorted(d for d in days if any(a <= d <= b for a, b in forbidden))
        if hit:
            raise SealedPeriodError(
                f"refusing to read {symbol} data dated in the sealed period ({hit[0]}, "
                f"requested {lo}..{hi})"
            )

    def allowed(paths: list[Path], days: list[date]) -> list[Path]:
        refuse_sealed(days)
        return paths

    for tf, needed in (warmup_bars or {}).items():
        before = warmup_before or first
        counted = 0
        for d in sorted(days_in(layout.canonical_bars_dir(symbol, tf)), reverse=True):
            if d >= before:
                continue
            refuse_sealed([d])  # before reading even the row count
            rows = pl.scan_parquet(layout.canonical_bars(symbol, tf, d)).select(pl.len())
            counted += int(rows.collect().item())
            if counted >= needed:
                lo = min(lo, d)
                break
        else:
            raise ValueError(
                f"warm-up needs {needed} completed {tf} bars before {before}; "
                f"only {counted} in the store"
            )

    files: list[Path] = []
    bars: dict[Timeframe, BarSeries] = {}
    planned: dict[Timeframe, list[Path]] = {}
    for tf in TIMEFRAMES:  # every date is checked before any file is read
        wanted = [d for d in days_in(layout.canonical_bars_dir(symbol, tf)) if lo <= d <= hi]
        planned[tf] = allowed([layout.canonical_bars(symbol, tf, d) for d in wanted], wanted)
    for tf, paths in planned.items():
        if not paths:
            raise FileNotFoundError(f"no canonical {tf} bars for {symbol} in {lo}..{hi}")
        df = pl.read_parquet(paths).sort("open_time")
        if df.columns != CANONICAL_BARS.names:
            raise ValueError(f"canonical {tf} bars have columns {df.columns}")
        bars[tf] = BarSeries.from_frame(tf, df)
        files.extend(paths)

    manifest_path = layout.day_manifest(symbol)
    days = read_parquet(manifest_path, DAY_SOURCES)
    files.append(manifest_path)
    exact = {
        d
        for d in days.filter(pl.col("tick_fidelity") == "exact")["day"].to_list()
        if first <= d <= last and layout.normalized_ticks(symbol, d).is_file()
    }
    files.extend(
        allowed([layout.normalized_ticks(symbol, d) for d in sorted(exact)], sorted(exact))
    )
    m1_open = bars[Timeframe.M1].open_time.astype("datetime64[ms]").astype("datetime64[D]")
    m1_days = {d for d in np.unique(m1_open).astype(date).tolist() if first <= d <= last}
    market = HistoricalMarket(
        symbol=symbol,
        point=point,
        bars=bars,
        tick_days=frozenset(exact),
        tick_loader=TickLoader(layout, symbol) if exact else None,
        bar_spread_extra_points=bar_spread_extra_points,
    )
    return LoadedMarket(
        market=market,
        files=sorted(files),
        tick_days=sorted(exact),
        bar_days=sorted(m1_days - exact),
        first_data_day=lo,
    )
