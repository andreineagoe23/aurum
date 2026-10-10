"""trend_pullback 0.2.0: H1 trend regime, M15 pullback setup, M15 or M5 entry.

The pre-registered spec is ``docs/strategies/trend_pullback.md`` (approved 2026-10-10, text
``b9baf4c``). Every rule below implements a numbered section of it; nothing here is a choice
that the spec does not make. Searched parameters (``h1_ema``, ``stop_atr_mult``,
``target_r``) accept only the declared grid values. Every other number is fixed (module
constants); changing one is a new strategy version.

Rules are stated for a long; a short mirrors every comparison.

* **Indicators (§3).** EMA(n) and ATR(14) at a bar are computed over the 500 completed bars of
  that timeframe ending at that bar (``ema_last``, ``atr``). Fewer than 500 → the rule is false.
  Values are cached per bar; the data are immutable once completed.
* **Regime (§4)** at T, with ``h0`` the newest completed H1 bar:
  ``close(h0) > EMA_H1(h0)`` and ``EMA_H1(h0) - EMA_H1(h3) > 0`` (strict).
* **Setup (§5)** at the M15 bar ``b0`` closing at T0, a decision time in session:
  ``b0..b7`` consecutive; some ``low(b0..b3) <= EMA20``; every ``close(b0..b7) > EMA50``
  (strict); ``close(b0) > EMA20(b0)``; long regime at T0.
* **m15 entry (§7):** setup at T and ``close(b0) > high(b1)``.
* **m5 trigger (§6):** the armed setup at an M5 close T is the newest setup bar closing in
  ``[T-30, T-5]`` min. It is armed unless an M5 bar closing in ``(T0, T)`` expired it
  (``close <= EMA50(b_s)``) or met the trigger. At T: expiry first, then trigger
  ``close(m0) > high(m1)``, ``close(m0) > EMA20(b_s)``, long regime as of T. Derived from
  bars only, so a restart re-derives the same state.
* **Stop (§8):** ``min low`` over the swing window (m15: ``b0..b7``; m5: the 8 M15 bars
  completed at T plus the M5 bars closing after the newest of them, up to and including
  ``m0``) minus ``stop_atr_mult x ATR14_M15(b0)``, rounded down to ``point``.
  ``500 <= d <= 2000`` points, where the minimum is ``max(500, stops_level + 10)``. Otherwise
  there is no signal.
* **Target (§9):** ``ref + target_r x (ref - stop)``, rounded toward the entry.
  **Timeout (§10):** ``max_hold`` 180 min from ``Signal.time``.
* **Invalidation (§11):** an H1 bar closing after the decision with ``close < EMA_H1``.

``diagnostics()`` counts would-be signals refused for ``stop_below_minimum`` and
``stop_above_cap``, and ``data_gap`` decision times (one per T at which a bar the rules may
read at T is missing; ``_read_set_incomplete``). The counts are diagnostics only; no decision
reads them, and a gap never adds or removes a signal beyond what the rules already do.
"""

import itertools
import math
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Literal, Self

import numpy as np
from pydantic import BaseModel, ConfigDict, model_validator

from aurum.core.enums import Side
from aurum.core.models import PositionState, Signal
from aurum.core.timeframes import Timeframe
from aurum.features.indicators import atr, ema_last
from aurum.features.view import BarSeries, FloatArray, MarketView, from_ms, to_ms

SPEC_VERSION = "0.2.0"
WINDOW = 500
H1_SLOPE_BARS = 3
M15_FAST, M15_SLOW = 20, 50
TOUCH_BARS, SWING_BARS = 4, 8
ATR_PERIOD = 14
MAX_HOLD = timedelta(minutes=180)
M5_TRIGGER_BARS = 6
MIN_STOP_POINTS, STOP_CAP_POINTS = 500, 2000
STOP_ATR_MULTS = (0.25, 0.5, 1.0)
TARGET_RS = (1.0, 1.5, 2.0)
H1_EMAS = (30, 50, 80)

M5_MS, M15_MS = 5 * 60_000, 15 * 60_000
TRIGGER_WINDOW_MS = M5_TRIGGER_BARS * M5_MS


class TrendPullbackParams(BaseModel):
    """Only the declared grid values (spec §14); 54 trials = 2 variants x 3 x 3 x 3."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    h1_ema: Literal[30, 50, 80] = 50  # = H1_EMAS
    stop_atr_mult: float = 0.5
    target_r: float = 1.5

    @model_validator(mode="after")
    def _on_the_grid(self) -> Self:
        if self.stop_atr_mult not in STOP_ATR_MULTS:
            raise ValueError(f"stop_atr_mult must be one of {STOP_ATR_MULTS}")
        if self.target_r not in TARGET_RS:
            raise ValueError(f"target_r must be one of {TARGET_RS}")
        return self


class TrendPullback:
    def __init__(
        self,
        params: TrendPullbackParams,
        *,
        symbol: str,
        point: float,
        decision_timeframe: Timeframe,
        entries_allowed: Callable[[datetime], bool],
        stops_level_points: int = 0,
        strategy_id: str = "trend_pullback",
    ) -> None:
        if decision_timeframe not in (Timeframe.M15, Timeframe.M5):
            raise ValueError(f"trend_pullback decides on M15 or M5, not {decision_timeframe}")
        self._p = params
        self._symbol = symbol
        self._point = point
        self._digits = max(0, round(-math.log10(point)))
        self._tf = decision_timeframe
        self._entries_allowed = entries_allowed
        self._min_stop = max(MIN_STOP_POINTS, stops_level_points + 10)
        self._id = strategy_id
        # Indicator values per (timeframe, kind, bar close). Valid because one instance sees one
        # market whose completed bars never change (one instance per run or live session).
        self._cache: dict[tuple[Timeframe, str, int], float | None] = {}
        self._rejections: Counter[str] = Counter()

    # ------------------------------------------------------------------ contract
    @property
    def strategy_id(self) -> str:
        return self._id

    @property
    def decision_timeframe(self) -> Timeframe:
        return self._tf

    @property
    def warmup_bars(self) -> Mapping[Timeframe, int]:
        # §3: 504 H1, 509 M15, 3 M5 completed bars before the first decision (both variants).
        return {
            Timeframe.H1: WINDOW + H1_SLOPE_BARS + 1,
            Timeframe.M15: WINDOW + SWING_BARS + 1,
            Timeframe.M5: 3,
        }

    def diagnostics(self) -> Mapping[str, int]:
        return {
            k: self._rejections[k] for k in ("stop_below_minimum", "stop_above_cap", "data_gap")
        }

    def on_bar(self, view: MarketView) -> Signal | None:
        if not self._entries_allowed(view.now):  # §12 (the engine also only calls then)
            return None
        if self._read_set_incomplete(view):  # diagnostics only; decides nothing
            self._rejections["data_gap"] += 1
        if self._tf is Timeframe.M15:
            return self._on_m15(view)
        return self._on_m5(view)

    def _read_set_incomplete(self, view: MarketView) -> bool:
        """One ``data_gap`` = a decision time T at which some bar the variant's rules may read
        at T is missing. m15: the M15 closes T-105 min .. T (b7..b0). m5: the M5 closes
        T-30 .. T (m0, its predecessor, every window bar the reconstruction re-reads and its
        predecessor) and every M15 close T-135 .. T (setup candidates in [T-30, T-5] with
        their 8-bar windows, and the swing window at T)."""
        now = to_ms(view.now)
        if self._tf is Timeframe.M15:
            return _missing(view.bars(Timeframe.M15), now - (SWING_BARS - 1) * M15_MS, now, M15_MS)
        span_m15 = TRIGGER_WINDOW_MS + (SWING_BARS - 1) * M15_MS
        return _missing(view.bars(Timeframe.M5), now - TRIGGER_WINDOW_MS, now, M5_MS) or _missing(
            view.bars(Timeframe.M15), now - span_m15, now, M15_MS
        )

    def invalidation(self, view: MarketView, position: PositionState, signal: Signal) -> str | None:
        h1 = view.bars(Timeframe.H1)
        sign = signal.side.sign
        start = int(np.searchsorted(h1.close_time, to_ms(signal.time), side="right"))
        for j in range(start, len(h1)):  # only H1 bars completed after the decision (§11)
            e = self._ema(h1, j, self._p.h1_ema)
            if e is not None and (float(h1.close[j]) - e) * sign < 0:
                return (
                    f"H1 close {float(h1.close[j]):.2f} {'<' if sign > 0 else '>'} "
                    f"EMA{self._p.h1_ema} {e:.3f} at {from_ms(int(h1.close_time[j])):%H:%M}Z"
                )
        return None

    # ------------------------------------------------------------------ variants
    def _on_m15(self, view: MarketView) -> Signal | None:
        now = to_ms(view.now)
        m15, h1 = view.bars(Timeframe.M15), view.bars(Timeframe.H1)
        i = len(m15) - 1
        if i < 0 or int(m15.close_time[i]) != now or not self._consecutive(m15, i):
            return None
        side = self._setup(m15, i, h1)
        if side is None:
            return None
        sign = side.sign
        close = float(m15.close[i])
        prev = float(m15.high[i - 1]) if sign > 0 else float(m15.low[i - 1])
        if (close - prev) * sign <= 0:  # §7: close beyond the previous bar's extreme
            return None
        extremes = (
            m15.low[i - SWING_BARS + 1 : i + 1]
            if sign > 0
            else m15.high[i - SWING_BARS + 1 : i + 1]
        )
        reasons = (
            f"M15 setup at {view.now:%H:%M}Z, {'long' if sign > 0 else 'short'} regime",
            f"M15 close {close:.2f} {'>' if sign > 0 else '<'} previous "
            f"{'high' if sign > 0 else 'low'} {prev:.2f}",
        )
        return self._signal(view.now, side, close, extremes, m15, i, reasons)

    def _on_m5(self, view: MarketView) -> Signal | None:
        now = to_ms(view.now)
        m5, m15, h1 = view.bars(Timeframe.M5), view.bars(Timeframe.M15), view.bars(Timeframe.H1)
        k = len(m5) - 1
        if k < 1 or int(m5.close_time[k]) != now:
            return None
        armed = self._armed_setup(m15, h1, now)
        if armed is None:
            return None
        s, side = armed
        # Bars after the setup's close and before now: an expiry or an earlier trigger ends it.
        first = int(np.searchsorted(m5.close_time, int(m15.close_time[s]), side="right"))
        for q in range(first, k):
            if self._expired(m5, q, m15, s, side) or self._triggers(m5, q, m15, s, side, h1):
                return None
        if self._expired(m5, k, m15, s, side):
            return None
        if int(m5.open_time[k - 1]) != int(m5.open_time[k]) - M5_MS:
            return None
        if not self._triggers(m5, k, m15, s, side, h1):
            return None
        sign = side.sign
        b0 = int(np.searchsorted(m15.close_time, now, side="right")) - 1  # newest M15 at T
        if b0 - SWING_BARS + 1 < 0:
            return None
        since = int(np.searchsorted(m5.close_time, int(m15.close_time[b0]), side="right"))
        if sign > 0:
            extremes = np.concatenate(
                (m15.low[b0 - SWING_BARS + 1 : b0 + 1], m5.low[since : k + 1])
            )
        else:
            extremes = np.concatenate(
                (m15.high[b0 - SWING_BARS + 1 : b0 + 1], m5.high[since : k + 1])
            )
        close = float(m5.close[k])
        reasons = (
            f"M15 setup at {from_ms(int(m15.close_time[s])):%H:%M}Z, "
            f"{'long' if sign > 0 else 'short'} regime",
            f"M5 close {close:.2f} {'>' if sign > 0 else '<'} previous "
            f"{'high' if sign > 0 else 'low'} "
            f"{float(m5.high[k - 1] if sign > 0 else m5.low[k - 1]):.2f}",
        )
        return self._signal(view.now, side, close, extremes, m15, b0, reasons)

    # ------------------------------------------------------------------ rules
    def _regime(self, h1: BarSeries, at_ms: int) -> Side | None:
        """§4, with h0 the newest H1 bar completed at ``at_ms``."""
        j = int(np.searchsorted(h1.close_time, at_ms, side="right")) - 1
        if j - H1_SLOPE_BARS < 0:
            return None
        e0 = self._ema(h1, j, self._p.h1_ema)
        e3 = self._ema(h1, j - H1_SLOPE_BARS, self._p.h1_ema)
        if e0 is None or e3 is None:
            return None
        close, slope = float(h1.close[j]), e0 - e3
        if close > e0 and slope > 0.0:
            return Side.BUY
        if close < e0 and slope < 0.0:
            return Side.SELL
        return None

    def _consecutive(self, m15: BarSeries, i: int) -> bool:
        if i - SWING_BARS + 1 < 0:
            return False
        opens = m15.open_time[i - SWING_BARS + 1 : i + 1]
        return bool(np.all(np.diff(opens) == M15_MS))

    def _setup(self, m15: BarSeries, i: int, h1: BarSeries) -> Side | None:
        """§5 at M15 bar ``i``: the side of the setup, or None."""
        t0 = int(m15.close_time[i])
        if not self._consecutive(m15, i) or not self._entries_allowed(from_ms(t0)):
            return None
        side = self._regime(h1, t0)
        if side is None:
            return None
        sign = side.sign
        fast_or_none = [self._ema(m15, i - k, M15_FAST) for k in range(TOUCH_BARS)]
        slow_or_none = [self._ema(m15, i - k, M15_SLOW) for k in range(SWING_BARS)]
        fast = [v for v in fast_or_none if v is not None]
        slow = [v for v in slow_or_none if v is not None]
        if len(fast) < TOUCH_BARS or len(slow) < SWING_BARS:
            return None
        if sign > 0:  # 5.2: the wick counts, equality counts
            touch = any(float(m15.low[i - k]) <= fast[k] for k in range(TOUCH_BARS))
        else:
            touch = any(float(m15.high[i - k]) >= fast[k] for k in range(TOUCH_BARS))
        hold = all((float(m15.close[i - k]) - slow[k]) * sign > 0 for k in range(SWING_BARS))
        resumed = (float(m15.close[i]) - fast[0]) * sign > 0
        return side if touch and hold and resumed else None

    def _armed_setup(self, m15: BarSeries, h1: BarSeries, now: int) -> tuple[int, Side] | None:
        """The newest setup bar closing in [now - 30 min, now - 5 min] (§6)."""
        hi = int(np.searchsorted(m15.close_time, now - M5_MS, side="right")) - 1
        for s in range(hi, -1, -1):
            if int(m15.close_time[s]) < now - TRIGGER_WINDOW_MS:
                break
            side = self._setup(m15, s, h1)
            if side is not None:
                return s, side
        return None

    def _expired(self, m5: BarSeries, q: int, m15: BarSeries, s: int, side: Side) -> bool:
        slow = self._ema(m15, s, M15_SLOW)
        return slow is None or (float(m5.close[q]) - slow) * side.sign <= 0

    def _triggers(
        self, m5: BarSeries, q: int, m15: BarSeries, s: int, side: Side, h1: BarSeries
    ) -> bool:
        sign = side.sign
        if q < 1 or int(m5.open_time[q - 1]) != int(m5.open_time[q]) - M5_MS:
            return False
        if not self._entries_allowed(from_ms(int(m5.close_time[q]))):  # §6 session limit
            return False
        prev = float(m5.high[q - 1]) if sign > 0 else float(m5.low[q - 1])
        fast = self._ema(m15, s, M15_FAST)
        close = float(m5.close[q])
        return (
            fast is not None
            and (close - prev) * sign > 0
            and (close - fast) * sign > 0
            and self._regime(h1, int(m5.close_time[q])) is side
        )

    def _signal(
        self,
        now: datetime,
        side: Side,
        ref: float,
        extremes: FloatArray,
        m15: BarSeries,
        b0: int,
        reasons: tuple[str, ...],
    ) -> Signal | None:
        sign = side.sign
        a = self._atr(m15, b0)
        if a is None:
            return None
        p = self._p
        if sign > 0:
            stop = self._to_grid(float(extremes.min()) - p.stop_atr_mult * a, math.floor)
        else:
            stop = self._to_grid(float(extremes.max()) + p.stop_atr_mult * a, math.ceil)
        d = round((ref - stop) * sign / self._point)
        if d < self._min_stop:
            self._rejections["stop_below_minimum"] += 1
            return None
        if d > STOP_CAP_POINTS:
            self._rejections["stop_above_cap"] += 1
            return None
        # Long: ref + R x (ref - stop); short: ref - R x (stop - ref). Rounded toward the entry.
        target = self._to_grid(
            ref + p.target_r * (ref - stop), math.floor if sign > 0 else math.ceil
        )
        return Signal(
            strategy_id=self._id,
            symbol=self._symbol,
            side=side,
            time=now,
            data_as_of=now,
            stop_loss=stop,
            take_profit=target,
            max_hold=MAX_HOLD,
            reasons=(
                *reasons,
                f"stop {stop:.{self._digits}f} = {d} points from {ref:.{self._digits}f} "
                f"(ATR14 {a:.4f} x {p.stop_atr_mult}), target {p.target_r}R",
            ),
            features={
                "ref": ref,
                "stop_points": float(d),
                "atr14_m15": a,
                "h1_ema": float(p.h1_ema),
                "stop_atr_mult": p.stop_atr_mult,
                "target_r": p.target_r,
            },
        )

    # ------------------------------------------------------------------ indicators
    def _to_grid(self, x: float, rounding: Callable[[float], int]) -> float:
        """§3: q = round(x / point, 6), then floor or ceil, x point."""
        return round(rounding(round(x / self._point, 6)) * self._point, self._digits)

    def _ema(self, bars: BarSeries, j: int, period: int) -> float | None:
        key = (bars.timeframe, f"ema{period}", int(bars.close_time[j]))
        if key not in self._cache:
            lo = j - WINDOW + 1
            self._cache[key] = None if lo < 0 else ema_last(bars.close[lo : j + 1], period)
        return self._cache[key]

    def _atr(self, bars: BarSeries, j: int) -> float | None:
        key = (bars.timeframe, "atr", int(bars.close_time[j]))
        if key not in self._cache:
            lo = j - WINDOW + 1
            self._cache[key] = (
                None
                if lo < 0
                else atr(
                    bars.high[lo : j + 1], bars.low[lo : j + 1], bars.close[lo : j + 1], ATR_PERIOD
                )
            )
        return self._cache[key]


def _missing(bars: BarSeries, first_ms: int, last_ms: int, step_ms: int) -> bool:
    """Whether any bar that should close on the ``step_ms`` grid in [first_ms, last_ms] is
    absent (closes are multiples of the timeframe)."""
    lo = -(-first_ms // step_ms) * step_ms
    hi = last_ms // step_ms * step_ms
    if hi < lo:
        return False
    expected = (hi - lo) // step_ms + 1
    present = int(np.searchsorted(bars.close_time, hi, side="right")) - int(
        np.searchsorted(bars.close_time, lo, side="left")
    )
    return present < expected


def declared_grid() -> list[dict[str, float]]:
    """The pre-registered search grid (spec section 14), in a fixed order: 27 points per
    variant, 54 trials in all."""
    return [
        {"h1_ema": h, "stop_atr_mult": m, "target_r": r}
        for h, m, r in itertools.product(H1_EMAS, STOP_ATR_MULTS, TARGET_RS)
    ]
