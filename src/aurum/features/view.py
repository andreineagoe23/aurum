"""What a strategy may see: completed bars up to "now", nothing later.

``BarSeries`` is a columnar, read-only bar history (numpy arrays; times are int64 milliseconds
since the Unix epoch, UTC; prices are bid, USD/oz; spreads are points). ``BarStore`` holds the
full history of each timeframe and hands out ``BarView``s bound to an instant ``now``: every
series a view returns contains exactly the bars with ``close_time <= now`` (MASTER_PROMPT §2:
a decision at 10:35 sees the H1 bar that closed at 10:00, never the forming one). Higher
timeframes are therefore as-of joined to lower ones on close time by construction.

The same view type serves backtests (``BarStore`` over stored canonical bars) and, later, the
live modes (a store fed with bars as they complete), so strategy code is identical.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import numpy as np
import numpy.typing as npt
import polars as pl

from aurum.core.timeframes import Timeframe
from aurum.core.timeutil import EPOCH, to_utc

IntArray = npt.NDArray[np.int64]
FloatArray = npt.NDArray[np.float64]


def to_ms(t: datetime) -> int:
    """Milliseconds since the Unix epoch of an aware datetime."""
    return (to_utc(t) - EPOCH) // timedelta(milliseconds=1)


def from_ms(ms: int) -> datetime:
    """Aware UTC datetime of ``ms`` milliseconds since the Unix epoch."""
    return EPOCH + timedelta(milliseconds=int(ms))


def _readonly[T: np.generic](a: npt.NDArray[T]) -> npt.NDArray[T]:
    a = np.ascontiguousarray(a)
    a.setflags(write=False)
    return a


@dataclass(frozen=True, slots=True, eq=False)
class BarSeries:
    """Bars of one timeframe, oldest first. Every array has the same length.

    ``spread_min`` is the minimum spread in the bar in points (-1 where unknown);
    ``tick_count`` counts bid-changing ticks (MT5 ``tick_volume``).
    """

    timeframe: Timeframe
    open_time: IntArray
    close_time: IntArray
    open: FloatArray
    high: FloatArray
    low: FloatArray
    close: FloatArray
    spread_min: IntArray
    tick_count: IntArray

    def __post_init__(self) -> None:
        n = len(self.open_time)
        for name in ("close_time", "open", "high", "low", "close", "spread_min", "tick_count"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"BarSeries column {name} has a different length")

    def __len__(self) -> int:
        return len(self.open_time)

    @classmethod
    def from_frame(cls, timeframe: Timeframe, df: pl.DataFrame) -> "BarSeries":
        """From canonical-bar rows (aurum.data.schemas.CANONICAL_BARS columns), sorted by
        ``open_time``. Raises if the rows are unsorted, duplicated or misaligned."""

        def ms(col: str) -> IntArray:
            return df[col].dt.epoch("ms").to_numpy().astype(np.int64)

        open_time = ms("open_time")
        close_time = ms("close_time")
        if len(open_time) and np.any(np.diff(open_time) <= 0):
            raise ValueError(f"{timeframe} bars are not strictly increasing in open_time")
        step = timeframe.seconds * 1000
        if np.any(close_time - open_time != step) or np.any(open_time % step != 0):
            raise ValueError(f"{timeframe} bars are not aligned {timeframe} intervals")
        return cls(
            timeframe=timeframe,
            open_time=_readonly(open_time),
            close_time=_readonly(close_time),
            open=_readonly(df["open"].to_numpy().astype(np.float64)),
            high=_readonly(df["high"].to_numpy().astype(np.float64)),
            low=_readonly(df["low"].to_numpy().astype(np.float64)),
            close=_readonly(df["close"].to_numpy().astype(np.float64)),
            spread_min=_readonly(df["spread_min"].fill_null(-1).to_numpy().astype(np.int64)),
            tick_count=_readonly(df["tick_count"].fill_null(0).to_numpy().astype(np.int64)),
        )

    def head(self, n: int) -> "BarSeries":
        """The first ``n`` bars (array views, still read-only)."""
        return self._slice(slice(0, n))

    def tail(self, n: int) -> "BarSeries":
        """The last ``n`` bars (all of them if fewer)."""
        return self._slice(slice(max(len(self) - n, 0), len(self)))

    def _slice(self, s: slice) -> "BarSeries":
        return BarSeries(
            timeframe=self.timeframe,
            open_time=self.open_time[s],
            close_time=self.close_time[s],
            open=self.open[s],
            high=self.high[s],
            low=self.low[s],
            close=self.close[s],
            spread_min=self.spread_min[s],
            tick_count=self.tick_count[s],
        )

    def completed_count(self, now_ms: int) -> int:
        """Number of bars with ``close_time <= now_ms``."""
        return int(np.searchsorted(self.close_time, now_ms, side="right"))

    @property
    def last_close_time(self) -> datetime | None:
        """Close time of the newest bar (None if empty)."""
        return from_ms(int(self.close_time[-1])) if len(self) else None


class MarketView(Protocol):
    """A strategy's window on the market at one instant."""

    @property
    def now(self) -> datetime:
        """The decision instant (UTC)."""
        ...

    def bars(self, timeframe: Timeframe, count: int | None = None) -> BarSeries:
        """Completed bars (``close_time <= now``), the newest ``count`` if given."""
        ...


class BarStore:
    """Full bar history per timeframe; ``view_at(now)`` exposes only what was known at ``now``."""

    def __init__(self, series: Mapping[Timeframe, BarSeries]) -> None:
        for tf, s in series.items():
            if s.timeframe is not tf:
                raise ValueError(f"series for {tf} holds {s.timeframe} bars")
        self._series = dict(series)

    @property
    def timeframes(self) -> frozenset[Timeframe]:
        return frozenset(self._series)

    def view_at(self, now: datetime) -> "BarView":
        return BarView(self, to_utc(now))

    def _completed(self, timeframe: Timeframe, now_ms: int, count: int | None) -> BarSeries:
        try:
            full = self._series[timeframe]
        except KeyError:
            raise KeyError(f"no {timeframe} bars loaded") from None
        visible = full.head(full.completed_count(now_ms))
        return visible if count is None else visible.tail(count)


class BarView:
    """``MarketView`` over a ``BarStore`` at a fixed instant."""

    __slots__ = ("_now", "_now_ms", "_store")

    def __init__(self, store: BarStore, now: datetime) -> None:
        self._store = store
        self._now = now
        self._now_ms = to_ms(now)

    @property
    def now(self) -> datetime:
        return self._now

    def bars(self, timeframe: Timeframe, count: int | None = None) -> BarSeries:
        if count is not None and count < 0:
            raise ValueError("count must be >= 0")
        return self._store._completed(timeframe, self._now_ms, count)
