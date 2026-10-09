"""Historical market for the simulator: bars for strategies, price paths for fills.

Per UTC day the simulator works in one of two data modes (ADR-0007):

* ``TICKS`` on days whose stored ticks reproduce the broker's M1 exactly (``tick_fidelity =
  exact``): every fill, stop and target is resolved on the real bid/ask quotes, in order.
* ``BARS`` on every other day: the price path is the canonical M1 bars (bid OHLC). The ask is
  ``bid + spread`` with ``spread = bar minimum spread + bar_spread_extra_points`` (the broker's
  bar spread is the bar's *minimum*; the extra is measured on exact days, see
  configs/backtest.yaml). Within a minute the order of high and low is unknown, so the
  simulator applies a worst-case rule and flags the trade (see ``aurum.backtest.broker``).

Times are int64 milliseconds since the Unix epoch (UTC). Prices are USD/oz.
"""

from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

import numpy as np

from aurum.core.timeframes import Timeframe
from aurum.features.view import BarSeries, BarStore, FloatArray, IntArray, from_ms, to_ms

DAY_MS = 86_400_000
LAST_QUOTE_LOOKBACK_DAYS = 5
"""How far back ``last_quote`` looks for a quote (covers weekends and holidays)."""


class DataMode(StrEnum):
    TICKS = "ticks"
    BARS = "bars"


def utc_day(ms: int) -> date:
    return from_ms(ms).date()


def day_start_ms(d: date) -> int:
    return to_ms(datetime(d.year, d.month, d.day, tzinfo=UTC))


@dataclass(frozen=True, slots=True)
class Quote:
    """One executable quote. In BARS mode it is synthesised from an M1 bar."""

    time_ms: int
    bid: float
    ask: float
    mode: DataMode
    day: date
    index: int
    """Tick index within ``day`` (TICKS) or index into the M1 series (BARS)."""

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def time(self) -> datetime:
        return from_ms(self.time_ms)


@dataclass(frozen=True, slots=True, eq=False)
class TickDay:
    """Normalized ticks of one UTC day, sorted by time (ties keep their stored order)."""

    time: IntArray
    bid: FloatArray
    ask: FloatArray

    def __post_init__(self) -> None:
        if not len(self.time) == len(self.bid) == len(self.ask):
            raise ValueError("TickDay arrays differ in length")
        if len(self.time) and np.any(np.diff(self.time) < 0):
            raise ValueError("ticks are not sorted by time")


@dataclass(frozen=True, slots=True, eq=False)
class TickSegment:
    """Consecutive ticks of one day; ``start`` is the index of the first one within the day."""

    day: date
    start: int
    time: IntArray
    bid: FloatArray
    ask: FloatArray


@dataclass(frozen=True, slots=True, eq=False)
class BarSegment:
    """Consecutive M1 bars of one BARS-mode day; ``start`` indexes the M1 series.

    ``spread`` is the assumed spread per bar in price units, so ``ask = bid + spread``.
    """

    day: date
    start: int
    open_time: IntArray
    close_time: IntArray
    open: FloatArray
    high: FloatArray
    low: FloatArray
    close: FloatArray
    spread: FloatArray


Segment = TickSegment | BarSegment


class HistoricalMarket:
    def __init__(
        self,
        *,
        symbol: str,
        point: float,
        bars: Mapping[Timeframe, BarSeries],
        tick_days: frozenset[date] = frozenset(),
        tick_loader: Callable[[date], TickDay] | None = None,
        bar_spread_extra_points: int = 0,
        tick_cache_days: int = 3,
    ) -> None:
        if Timeframe.M1 not in bars:
            raise ValueError("the simulator needs M1 bars for price paths")
        if tick_days and tick_loader is None:
            raise ValueError("tick_days given without a tick_loader")
        self.symbol = symbol
        self.point = point
        self.store = BarStore(bars)
        self.m1 = bars[Timeframe.M1]
        self.tick_days = tick_days
        self._loader = tick_loader
        self._cache: OrderedDict[date, TickDay] = OrderedDict()
        self._cache_size = tick_cache_days
        spread_points = np.maximum(self.m1.spread_min, 0) + bar_spread_extra_points
        self._bar_spread: FloatArray = spread_points.astype(np.float64) * point

    # ------------------------------------------------------------------ modes and ticks
    def mode(self, day: date) -> DataMode:
        return DataMode.TICKS if day in self.tick_days else DataMode.BARS

    def ticks(self, day: date) -> TickDay:
        if day not in self.tick_days or self._loader is None:
            raise KeyError(f"{day} is not a tick day")
        cached = self._cache.get(day)
        if cached is None:
            cached = self._loader(day)
            self._cache[day] = cached
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(day)
        return cached

    # ------------------------------------------------------------------ price paths
    def segments(
        self, start_ms: int, end_ms: int, start_index: int | None = None
    ) -> Iterator[Segment]:
        """The price path over ``[start_ms, end_ms)``, day by day, oldest first.

        TICKS: ticks with ``start_ms <= time < end_ms``; if ``start_index`` is given it is the
        first tick index on ``start_ms``'s day (to resume exactly after a fill).
        BARS: M1 bars with ``close_time > start_ms`` and ``open_time < end_ms`` (a bar that
        contains ``start_ms`` is included whole).
        """
        if end_ms <= start_ms:
            return
        day = utc_day(start_ms)
        last = utc_day(end_ms - 1)
        first = True
        while day <= last:
            lo = max(start_ms, day_start_ms(day))
            hi = min(end_ms, day_start_ms(day) + DAY_MS)
            seg = (
                self._tick_segment(day, lo, hi, start_index if first else None)
                if self.mode(day) is DataMode.TICKS
                else self._bar_segment(day, lo, hi)
            )
            if seg is not None:
                yield seg
            first = False
            day += timedelta(days=1)

    def _tick_segment(self, day: date, lo: int, hi: int, index: int | None) -> TickSegment | None:
        td = self.ticks(day)
        i = int(np.searchsorted(td.time, lo, side="left")) if index is None else index
        j = int(np.searchsorted(td.time, hi, side="left"))
        if j <= i:
            return None
        return TickSegment(day, i, td.time[i:j], td.bid[i:j], td.ask[i:j])

    def _bar_segment(self, day: date, lo: int, hi: int) -> BarSegment | None:
        m1 = self.m1
        day_lo = day_start_ms(day)
        i = int(np.searchsorted(m1.close_time, max(lo, day_lo), side="right"))
        i = max(i, int(np.searchsorted(m1.open_time, day_lo, side="left")))
        j = int(np.searchsorted(m1.open_time, hi, side="left"))
        if j <= i:
            return None
        s = slice(i, j)
        return BarSegment(
            day,
            i,
            m1.open_time[s],
            m1.close_time[s],
            m1.open[s],
            m1.high[s],
            m1.low[s],
            m1.close[s],
            self._bar_spread[s],
        )

    # ------------------------------------------------------------------ quotes
    def next_quote(self, at_ms: int, until_ms: int) -> Quote | None:
        """First executable quote at or after ``at_ms`` and before ``until_ms``, or None.

        TICKS: the first tick with ``time >= at_ms``. BARS: the open of the first M1 bar that
        ends after ``at_ms`` (sub-minute delays cannot be resolved on bars; the quote time is
        ``max(open_time, at_ms)``).
        """
        for seg in self.segments(at_ms, until_ms):
            if isinstance(seg, TickSegment):
                return Quote(
                    int(seg.time[0]),
                    float(seg.bid[0]),
                    float(seg.ask[0]),
                    DataMode.TICKS,
                    seg.day,
                    seg.start,
                )
            bid = float(seg.open[0])
            return Quote(
                max(int(seg.open_time[0]), at_ms),
                bid,
                bid + float(seg.spread[0]),
                DataMode.BARS,
                seg.day,
                seg.start,
            )
        return None

    def last_quote(self, at_ms: int) -> Quote | None:
        """Newest quote with ``time <= at_ms`` (BARS: the close of the newest M1 bar that has
        closed, stamped 1 ms before its close), looking back at most a few days."""
        day = utc_day(at_ms)
        for _ in range(LAST_QUOTE_LOOKBACK_DAYS + 1):
            q = self._last_on(day, at_ms)
            if q is not None:
                return q
            day -= timedelta(days=1)
        return None

    def _last_on(self, day: date, at_ms: int) -> Quote | None:
        hi = min(at_ms, day_start_ms(day) + DAY_MS - 1)
        if self.mode(day) is DataMode.TICKS:
            td = self.ticks(day)
            j = int(np.searchsorted(td.time, hi, side="right")) - 1
            if j < 0:
                return None
            bid, ask = float(td.bid[j]), float(td.ask[j])
            return Quote(int(td.time[j]), bid, ask, DataMode.TICKS, day, j)
        m1 = self.m1
        j = int(np.searchsorted(m1.close_time, hi, side="right")) - 1
        if j < 0 or m1.open_time[j] < day_start_ms(day):
            return None
        bid = float(m1.close[j])
        return Quote(
            int(m1.close_time[j]) - 1, bid, bid + float(self._bar_spread[j]), DataMode.BARS, day, j
        )
