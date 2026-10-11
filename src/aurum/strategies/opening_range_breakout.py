"""opening_range_breakout 0.1.0: the first M15 or M5 close outside an opening range.

The pre-registered spec is ``docs/strategies/opening_range_breakout.md`` (frozen 2026-10-11).
Every rule below implements a numbered section of it; nothing here is a choice the spec does
not make. Searched parameters (``or_bars``, ``target_r``) accept only the declared grid values.
Every other number is fixed (module constants); changing one is a new strategy version.

Rules are stated for a long; a short mirrors every comparison.

* **Ranges (§5).** Per London trading date D: ``london`` anchored at 08:00 Europe/London
  (session ``london_morning``) and ``newyork`` at 09:30 America/New_York (``ldn_ny_overlap``),
  each converted with its own city's daylight time. The range is the ``or_bars`` M15 bars
  opening at A, A + 15 min, ...; ``H`` = max high, ``L`` = min low, ``range_end`` = the close of
  the last one. All must exist.
* **Eligible decision time (§6, §12.3)** for a range: a close T of the decision timeframe with
  ``range_end < T <= range_end + 120 min``, ``session_at(T)`` equal to the range's session
  (entries allowed), and ``blackout(T)`` None.
* **Breakout (§6)** at an eligible T: the read set (range bars + every decision-timeframe bar
  closing in ``(range_end, T]``) is complete and ``close(T) > H`` strictly; every earlier
  eligible close was inside ``[L, H]``. The first eligible close outside **spends** the range,
  whether or not its stop is within bounds. A close outside at a blackout instant neither
  signals nor spends it.
* **Stop (§8):** ``L - 20 points`` rounded down to ``point``;
  ``d = round((ref - stop) / point)`` with ``ref = close(T)``.
  ``max(500, stops_level + 10) <= d <= 2000``, else no signal (the range is still spent).
* **Target (§9):** ``ref + target_r x (ref - stop)``, rounded toward the entry.
  **Timeout (§10):** ``max_hold`` 120 min from ``Signal.time``. **No invalidation (§11).**

**Stateless.** At every call at T the strategy re-derives each range of T's trading date from
the bars completed at T and the calendar (``_walk``), so a restart reaches the same state,
including whether a range is spent. The walk covers every instant of the window up to T,
whether or not the engine called the strategy at it.

**Diagnostics (§6).** Each range-date keeps the outcome of its latest walk (``outcomes()``):
its status and its ``data_gap`` / ``blackout_skipped`` instants. A newer walk replaces the
older one, so each instant counts once however often the strategy is called. Statuses are as
of the strategy's latest call on that date; range-dates of a date on which it was never
called are not counted. No decision reads them.
"""

import itertools
import math
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import BaseModel, ConfigDict, model_validator

from aurum.core.enums import Side
from aurum.core.models import PositionState, Signal
from aurum.core.timeframes import Timeframe
from aurum.features.view import IntArray, MarketView, from_ms, to_ms

SPEC_VERSION = "0.1.0"
LONDON, NEW_YORK = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")
ENTRY_WINDOW = timedelta(minutes=120)
MAX_HOLD = timedelta(minutes=120)
STOP_BUFFER_POINTS = 20
MIN_STOP_POINTS, STOP_CAP_POINTS = 500, 2000
OR_BARS = (2, 4)
TARGET_RS = (1.0, 1.5, 2.0)
STATUSES = (
    "signal",
    "stop_below_minimum",
    "stop_above_cap",
    "no_break",
    "data_gap",
    "range_missing",
    "no_decision_time",
)
"""Each range-date ends in exactly one (§6)."""

M15_MS = 15 * 60_000
WINDOW_MS = ENTRY_WINDOW // timedelta(milliseconds=1)


@dataclass(frozen=True, slots=True)
class OpeningRange:
    name: str
    anchor: time
    tz: ZoneInfo
    session: str

    def anchor_utc(self, day: date) -> datetime:
        """The anchor on trading date ``day`` (London date), in UTC."""
        return datetime.combine(day, self.anchor, self.tz).astimezone(UTC)


RANGES = (
    OpeningRange("london", time(8, 0), LONDON, "london_morning"),
    OpeningRange("newyork", time(9, 30), NEW_YORK, "ldn_ny_overlap"),
)


class OpeningRangeBreakoutParams(BaseModel):
    """Only the declared grid values (spec §14); 12 trials = 2 variants x 2 x 3."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    or_bars: Literal[2, 4] = 2  # = OR_BARS
    target_r: float = 1.5

    @model_validator(mode="after")
    def _on_the_grid(self) -> Self:
        if self.target_r not in TARGET_RS:
            raise ValueError(f"target_r must be one of {TARGET_RS}")
        return self


@dataclass(frozen=True, slots=True)
class RangeOutcome:
    """One range on one trading date, as of the walk at ``as_of`` (spec §6)."""

    day: date
    range: str
    status: str
    as_of: datetime
    final: bool
    """Spent, or its whole window lies at or before ``as_of``: no later walk can change it."""
    eligible: int
    """Eligible decision times walked, up to and including the spend."""
    data_gap: int
    blackout_skipped: int
    high: float | None = None
    low: float | None = None
    width_points: int | None = None
    spend_time: datetime | None = None
    side: Side | None = None
    ref: float | None = None
    stop: float | None = None
    stop_points: int | None = None


class OpeningRangeBreakout:
    def __init__(
        self,
        params: OpeningRangeBreakoutParams,
        *,
        symbol: str,
        point: float,
        decision_timeframe: Timeframe,
        session_at: Callable[[datetime], str | None],
        blackout: Callable[[datetime], str | None],
        stops_level_points: int = 0,
        strategy_id: str = "opening_range_breakout",
    ) -> None:
        """``session_at(t)``: the calendar's session at ``t`` when entries are allowed there,
        else None. ``blackout(t)``: the risk engine's ``NewsBlackout.reason_at``."""
        if decision_timeframe not in (Timeframe.M15, Timeframe.M5):
            raise ValueError(
                f"opening_range_breakout decides on M15 or M5, not {decision_timeframe}"
            )
        self._p = params
        self._symbol = symbol
        self._point = point
        self._digits = max(0, round(-math.log10(point)))
        self._tf = decision_timeframe
        self._step_ms = decision_timeframe.seconds * 1000
        self._session_at = session_at
        self._blackout = blackout
        self._min_stop = max(MIN_STOP_POINTS, stops_level_points + 10)
        self._id = strategy_id
        self._outcomes: dict[tuple[date, str], RangeOutcome] = {}
        # Calendar answers per instant (pure functions of time).
        self._sessions: dict[int, str | None] = {}
        self._blocked: dict[int, bool] = {}

    # ------------------------------------------------------------------ contract
    @property
    def strategy_id(self) -> str:
        return self._id

    @property
    def decision_timeframe(self) -> Timeframe:
        return self._tf

    @property
    def warmup_bars(self) -> Mapping[Timeframe, int]:
        return {}  # §3: every value comes from bars of the same day, after the anchor

    def diagnostics(self) -> Mapping[str, int]:
        outs = list(self._outcomes.values())
        status = Counter(o.status for o in outs)
        return {
            "data_gap": sum(o.data_gap for o in outs),
            "blackout_skipped": sum(o.blackout_skipped for o in outs),
            "stop_below_minimum": status["stop_below_minimum"],
            "stop_above_cap": status["stop_above_cap"],
            **{f"status_{s}": status[s] for s in STATUSES},
        }

    def outcomes(self) -> list[RangeOutcome]:
        """Every range-date seen so far, by date and range."""
        order = {r.name: i for i, r in enumerate(RANGES)}
        return sorted(self._outcomes.values(), key=lambda o: (o.day, order[o.range]))

    def on_bar(self, view: MarketView) -> Signal | None:
        now = view.now
        now_ms = to_ms(now)
        if self._session(now_ms) is None:  # §12.1 (the engine also only calls then)
            return None
        day = now.astimezone(LONDON).date()
        signal = None
        for rng in RANGES:
            out = self._update(view, rng, day, now_ms)
            if out.status == "signal" and out.spend_time == now:
                signal = self._signal(now, rng, out)
        return signal

    def invalidation(self, view: MarketView, position: PositionState, signal: Signal) -> str | None:
        return None  # §11: stop, target, timeout and force-flat only

    # ------------------------------------------------------------------ rules
    def _update(self, view: MarketView, rng: OpeningRange, day: date, now_ms: int) -> RangeOutcome:
        key = (day, rng.name)
        known = self._outcomes.get(key)
        if known is not None and known.final and to_ms(known.as_of) <= now_ms:
            return known
        out = self._walk(view, rng, day, now_ms)
        if known is None or to_ms(known.as_of) <= now_ms:
            self._outcomes[key] = out
        return out

    def _walk(self, view: MarketView, rng: OpeningRange, day: date, now_ms: int) -> RangeOutcome:
        """§5-§8 for one range-date, from the bars completed at ``now_ms``."""
        n = self._p.or_bars
        anchor = to_ms(rng.anchor_utc(day))
        range_end = anchor + n * M15_MS
        last = min(range_end + WINDOW_MS, now_ms)
        m15 = view.bars(Timeframe.M15)
        found = [_index(m15.open_time, anchor + k * M15_MS) for k in range(n)]
        rows = [i for i in found if i is not None]
        exists = len(rows) == n
        hi = float(m15.high[rows].max()) if exists else math.nan
        lo = float(m15.low[rows].min()) if exists else math.nan
        bars = view.bars(self._tf)
        missing = not exists
        eligible = data_gap = skipped = 0

        def outcome(status: str, *, final: bool, **spend: Any) -> RangeOutcome:
            return RangeOutcome(
                day=day,
                range=rng.name,
                status=status,
                as_of=from_ms(now_ms),
                final=final,
                eligible=eligible,
                data_gap=data_gap,
                blackout_skipped=skipped,
                high=hi if exists else None,
                low=lo if exists else None,
                width_points=round((hi - lo) / self._point) if exists else None,
                **spend,
            )

        t = range_end + self._step_ms
        while t <= last:
            j = _index(bars.close_time, t)
            if j is None:
                missing = True  # stays in the read set of every later instant
            if self._session(t) != rng.session:  # rules 6.1-6.3: not a decision time at all
                t += self._step_ms
                continue
            close = float(bars.close[j]) if j is not None else math.nan
            outside = j is not None and exists and (close > hi or close < lo)
            if self._is_blocked(t):  # rule 12.3: neither signals nor spends
                if outside and not missing:
                    skipped += 1
                t += self._step_ms
                continue
            eligible += 1
            if missing:
                data_gap += 1
            elif outside:  # the first eligible close outside spends the range (§6)
                side = Side.BUY if close > hi else Side.SELL
                stop, d = self._stop(side, close, hi, lo)
                if d < self._min_stop:
                    status = "stop_below_minimum"
                elif d > STOP_CAP_POINTS:
                    status = "stop_above_cap"
                else:
                    status = "signal"
                return outcome(
                    status, final=True, spend_time=from_ms(t), side=side, ref=close, stop=stop,
                    stop_points=d,
                )  # fmt: skip
            t += self._step_ms
        if eligible == 0:
            status = "no_decision_time"
        elif not exists:
            status = "range_missing"
        else:
            status = "data_gap" if data_gap else "no_break"
        return outcome(status, final=range_end + WINDOW_MS <= now_ms)

    def _stop(self, side: Side, ref: float, hi: float, lo: float) -> tuple[float, int]:
        """§8: the opposite side of the range -/+ the buffer, on the price grid, and its
        distance from ``ref`` in points."""
        buffer = STOP_BUFFER_POINTS * self._point
        if side is Side.BUY:
            stop = self._to_grid(lo - buffer, math.floor)
        else:
            stop = self._to_grid(hi + buffer, math.ceil)
        return stop, round((ref - stop) * side.sign / self._point)

    def _signal(self, now: datetime, rng: OpeningRange, out: RangeOutcome) -> Signal:
        assert out.side is not None  # a "signal" outcome carries every spend field
        assert out.ref is not None
        assert out.stop is not None
        assert out.high is not None
        assert out.low is not None
        assert out.width_points is not None
        assert out.stop_points is not None
        sign, ref, stop, p = out.side.sign, out.ref, out.stop, self._p
        # Long: ref + R x (ref - stop); short: ref - R x (stop - ref). Rounded toward the entry.
        target = self._to_grid(
            ref + p.target_r * (ref - stop), math.floor if sign > 0 else math.ceil
        )
        f = f".{self._digits}f"
        edge = f"H {out.high:{f}}" if sign > 0 else f"L {out.low:{f}}"
        return Signal(
            strategy_id=self._id,
            symbol=self._symbol,
            side=out.side,
            time=now,
            data_as_of=now,
            stop_loss=stop,
            take_profit=target,
            max_hold=MAX_HOLD,
            reasons=(
                f"{rng.name} range {rng.anchor:%H:%M} {rng.tz.key}, {p.or_bars} M15 bars: "
                f"H {out.high:{f}}, L {out.low:{f}}, W {out.width_points} points",
                f"{self._tf} close {ref:{f}} {'>' if sign > 0 else '<'} {edge}: "
                "first eligible close outside the range",
                f"stop {stop:{f}} = {out.stop_points} points from {ref:{f}} (opposite side "
                f"{'-' if sign > 0 else '+'} {STOP_BUFFER_POINTS} points), target {p.target_r}R",
            ),
            features={
                "range": rng.name,
                "ref": ref,
                "range_high": out.high,
                "range_low": out.low,
                "width_points": out.width_points,
                "stop_points": out.stop_points,
                "or_bars": p.or_bars,
                "target_r": p.target_r,
            },
        )

    # ------------------------------------------------------------------ helpers
    def _session(self, t_ms: int) -> str | None:
        if t_ms not in self._sessions:
            self._sessions[t_ms] = self._session_at(from_ms(t_ms))
        return self._sessions[t_ms]

    def _is_blocked(self, t_ms: int) -> bool:
        if t_ms not in self._blocked:
            self._blocked[t_ms] = self._blackout(from_ms(t_ms)) is not None
        return self._blocked[t_ms]

    def _to_grid(self, x: float, rounding: Callable[[float], int]) -> float:
        """§3: q = round(x / point, 6), then floor or ceil, x point."""
        return round(rounding(round(x / self._point, 6)) * self._point, self._digits)


def _index(times: IntArray, t_ms: int) -> int | None:
    """Position of ``t_ms`` in the sorted ``times``, or None if absent."""
    i = int(np.searchsorted(times, t_ms))
    return i if i < len(times) and int(times[i]) == t_ms else None


def declared_grid() -> list[dict[str, float]]:
    """The pre-registered search grid (spec section 14), in a fixed order: 6 points per
    variant, 12 trials in all."""
    return [{"or_bars": n, "target_r": r} for n, r in itertools.product(OR_BARS, TARGET_RS)]
