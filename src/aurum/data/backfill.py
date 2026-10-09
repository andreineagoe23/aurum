"""Historical backfill (`aurum data backfill`): broker ticks and bars into immutable raw Parquet.

* **Bounded chunks.** Ticks are requested one UTC day at a time; bars in windows of
  ``bar_chunk_days`` and split into one file per UTC day.
* **Complete days only.** A day is fetched once it has fully ended in UTC (the current day
  belongs to the live collector).
* **Idempotent and resumable.** A day whose file exists is skipped, so a re-run after a crash
  continues where it stopped. Files are written atomically and never overwritten. Every
  calendar day in the range gets a file, an empty one when the broker has nothing for it, so
  "fetched, nothing there" is distinguishable from "not fetched yet".
* **Retries.** A day on which the market should have traded but nothing came back is asked again
  ``empty_retries`` times (the terminal downloads history on demand). If it is still empty it
  is stored empty and data QA reports it. ``refetch_empty`` asks again for stored empty days and
  replaces them only if data has appeared (empty -> data is the one allowed replacement).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path

import polars as pl
import pyarrow as pa
import structlog

from aurum.adapters.storage.layout import DataLayout
from aurum.adapters.storage.parquet import read_parquet, write_parquet
from aurum.core.timeframes import Timeframe
from aurum.data.market_hours import MarketHours
from aurum.data.schemas import RAW_BARS, RAW_TICKS
from aurum.ports.clock import ClockPort
from aurum.ports.market_data import MarketDataSource
from aurum.settings import BackfillConfig

log = structlog.get_logger(__name__)


def day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def days(start: date, end: date) -> list[date]:
    """Every calendar day in ``[start, end]``."""
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


@dataclass
class DatasetResult:
    dataset: str
    written: int = 0
    rows: int = 0
    skipped_existing: int = 0
    empty_days: list[str] = field(default_factory=list)
    """Days stored empty although the market should have traded (QA explains or flags them)."""
    refilled: int = 0


class Backfiller:
    def __init__(
        self,
        source: MarketDataSource,
        layout: DataLayout,
        cfg: BackfillConfig,
        market: MarketHours,
        *,
        clock: ClockPort,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.source = source
        self.layout = layout
        self.cfg = cfg
        self.market = market
        self.clock = clock
        self.sleep = sleep or time.sleep

    def last_complete_day(self) -> date:
        return self.clock.now().date() - timedelta(days=1)

    def _clamp(self, end: date | None) -> date:
        last = self.last_complete_day()
        return last if end is None or end > last else end

    def _trading_expected(self, d: date) -> bool:
        return self.market.expected_data_seconds(day_start(d), day_start(d) + timedelta(days=1)) > 0

    def _store(
        self, frame: pl.DataFrame, path: Path, schema: pa.Schema, result: DatasetResult, d: date
    ) -> None:
        replacing_empty = path.exists()
        if replacing_empty and frame.is_empty():
            return
        if frame.is_empty() and self._trading_expected(d):
            result.empty_days.append(d.isoformat())
        if replacing_empty:
            path.unlink()  # only ever an empty file (checked by the caller)
            result.refilled += 1
        write_parquet(frame, path, schema, overwrite=False)
        result.written += 1
        result.rows += frame.height

    def _needs_fetch(self, path: Path, refetch_empty: bool) -> bool:
        if not path.exists():
            return True
        return refetch_empty and read_parquet(path).height == 0

    # ------------------------------------------------------------------ ticks
    def ticks(
        self,
        symbol: str,
        start: date,
        end: date | None = None,
        *,
        refetch_empty: bool = False,
    ) -> DatasetResult:
        result = DatasetResult("raw_ticks/mt5_history")
        for d in days(start, self._clamp(end)):
            path = self.layout.raw_ticks(symbol, d)
            if not self._needs_fetch(path, refetch_empty):
                result.skipped_existing += 1
                continue
            frame = self._fetch_with_retries(
                d,
                partial(
                    self.source.tick_frame, symbol, day_start(d), day_start(d + timedelta(days=1))
                ),
            )
            self._store(frame, path, RAW_TICKS, result, d)
            log.info("backfill.ticks", day=d.isoformat(), rows=frame.height)
        return result

    # ------------------------------------------------------------------ bars
    def bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: date,
        end: date | None = None,
        *,
        refetch_empty: bool = False,
    ) -> DatasetResult:
        result = DatasetResult(f"raw_broker_bars/{timeframe}")
        all_days = days(start, self._clamp(end))
        step = self.cfg.bar_chunk_days
        for i in range(0, len(all_days), step):
            chunk = all_days[i : i + step]
            todo = [
                d
                for d in chunk
                if self._needs_fetch(self.layout.raw_bars(symbol, timeframe, d), refetch_empty)
            ]
            result.skipped_existing += len(chunk) - len(todo)
            if not todo:
                continue
            lo, hi = day_start(todo[0]), day_start(todo[-1] + timedelta(days=1))
            frame = self._fetch_with_retries(
                todo[0] if len(todo) == 1 else None,
                partial(self.source.rate_frame, symbol, timeframe, lo, hi),
                retry_if_empty=any(self._trading_expected(d) for d in todo),
            )
            frame = frame.with_columns(pl.col("open_time").dt.date().alias("_day"))
            for d in todo:
                part = frame.filter(pl.col("_day") == d).drop("_day")
                self._store(part, self.layout.raw_bars(symbol, timeframe, d), RAW_BARS, result, d)
            log.info(
                "backfill.bars",
                timeframe=str(timeframe),
                start=todo[0].isoformat(),
                end=todo[-1].isoformat(),
                rows=frame.height,
            )
        return result

    def _fetch_with_retries(
        self,
        d: date | None,
        fetch: Callable[[], pl.DataFrame],
        *,
        retry_if_empty: bool | None = None,
    ) -> pl.DataFrame:
        frame = fetch()
        should_retry = (
            retry_if_empty
            if retry_if_empty is not None
            else (d is not None and self._trading_expected(d))
        )
        attempt = 0
        while frame.is_empty() and should_retry and attempt < self.cfg.empty_retries:
            attempt += 1
            log.info("backfill.retry_empty", day=d.isoformat() if d else None, attempt=attempt)
            self.sleep(self.cfg.retry_seconds * attempt)
            frame = fetch()
        return frame
