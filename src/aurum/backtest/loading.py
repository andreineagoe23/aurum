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
* ``plan_market`` resolves every input file first (directory listings and warm-up row counts
  only); its SHA-256 over every file (path relative to the data dir + file hash) is the
  evaluation's input-data identity, warm-up included, known before the evaluation starts.
  ``read_market`` then reads exactly those files.
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
class MarketPlan:
    """Every input file of one backtest, resolved before any of them is read."""

    symbol: str
    first: date
    last: date
    bars: dict[Timeframe, list[Path]]
    day_manifest: Path
    tick_files: dict[date, Path]
    """Days in ``first..last`` with exact normalized ticks."""
    first_data_day: date
    """Start of the load range: the earliest UTC day any bar file could come from."""

    @property
    def files(self) -> list[Path]:
        """Every input file, sorted."""
        bars = [p for paths in self.bars.values() for p in paths]
        return sorted([*bars, self.day_manifest, *self.tick_files.values()])

    @property
    def tick_days(self) -> list[date]:
        return sorted(self.tick_files)

    def data_hash(self, root: Path) -> str:
        h = hashlib.sha256()
        for f in self.files:
            h.update(f.relative_to(root).as_posix().encode())
            h.update(sha256_file(f).encode())
        return h.hexdigest()


@dataclass(frozen=True)
class LoadedMarket:
    market: HistoricalMarket
    plan: MarketPlan
    bar_days: list[date]
    """Days in ``first..last`` with M1 bars but no exact ticks (BARS mode)."""

    @property
    def files(self) -> list[Path]:
        return self.plan.files

    @property
    def tick_days(self) -> list[date]:
        return self.plan.tick_days

    @property
    def first_data_day(self) -> date:
        return self.plan.first_data_day


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


def plan_market(
    layout: DataLayout,
    symbol: str,
    *,
    first: date,
    last: date,
    warmup_days: int,
    last_data_day: date | None = None,
    forbidden: Sequence[tuple[date, date]] = (),
    warmup_bars: Mapping[Timeframe, int] | None = None,
    warmup_before: date | None = None,
) -> MarketPlan:
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

    bars: dict[Timeframe, list[Path]] = {}
    for tf in TIMEFRAMES:  # every date is checked before any file is read
        wanted = [d for d in days_in(layout.canonical_bars_dir(symbol, tf)) if lo <= d <= hi]
        refuse_sealed(wanted)
        if not wanted:
            raise FileNotFoundError(f"no canonical {tf} bars for {symbol} in {lo}..{hi}")
        bars[tf] = [layout.canonical_bars(symbol, tf, d) for d in wanted]
    manifest_path = layout.day_manifest(symbol)
    days = read_parquet(manifest_path, DAY_SOURCES)
    exact = sorted(
        d
        for d in days.filter(pl.col("tick_fidelity") == "exact")["day"].to_list()
        if first <= d <= last and layout.normalized_ticks(symbol, d).is_file()
    )
    refuse_sealed(exact)
    return MarketPlan(
        symbol=symbol,
        first=first,
        last=last,
        bars=bars,
        day_manifest=manifest_path,
        tick_files={d: layout.normalized_ticks(symbol, d) for d in exact},
        first_data_day=lo,
    )


def read_market(
    layout: DataLayout, plan: MarketPlan, *, point: float, bar_spread_extra_points: int
) -> LoadedMarket:
    """Read exactly the planned files."""
    bars: dict[Timeframe, BarSeries] = {}
    for tf, paths in plan.bars.items():
        df = pl.read_parquet(paths).sort("open_time")
        if df.columns != CANONICAL_BARS.names:
            raise ValueError(f"canonical {tf} bars have columns {df.columns}")
        bars[tf] = BarSeries.from_frame(tf, df)
    m1_open = bars[Timeframe.M1].open_time.astype("datetime64[ms]").astype("datetime64[D]")
    m1_days = {d for d in np.unique(m1_open).astype(date).tolist() if plan.first <= d <= plan.last}
    exact = frozenset(plan.tick_files)
    market = HistoricalMarket(
        symbol=plan.symbol,
        point=point,
        bars=bars,
        tick_days=exact,
        tick_loader=TickLoader(layout, plan.symbol) if exact else None,
        bar_spread_extra_points=bar_spread_extra_points,
    )
    return LoadedMarket(market=market, plan=plan, bar_days=sorted(m1_days - exact))


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
    """``plan_market`` then ``read_market``."""
    plan = plan_market(
        layout,
        symbol,
        first=first,
        last=last,
        warmup_days=warmup_days,
        last_data_day=last_data_day,
        forbidden=forbidden,
        warmup_bars=warmup_bars,
        warmup_before=warmup_before,
    )
    return read_market(layout, plan, point=point, bar_spread_extra_points=bar_spread_extra_points)
