"""Reference strategy: fixed-time entries with fixed stop/target distances.

**Not a research strategy.** It exists only to exercise the simulator end to end and to be
replicated exactly by an MT5 Strategy Tester EA for the Phase 3 parity check
(``mql5/AurumReference.mq5``). It has no edge by design.

Rule, at every close of the decision timeframe (M5 or M15 variant) at instant ``now`` (UTC):

* if ``now``'s UTC wall-clock time equals an entry's ``time_utc`` and the newest completed bar
  of the decision timeframe closed exactly at ``now`` (no data gap),
* propose a MARKET order on that entry's ``side`` with
  ``stop = close -/+ stop_points x point`` and ``target = close +/- target_points x point``
  (the entry's own distances), where ``close`` is that bar's bid close, and the entry's
  ``max_hold`` (if any).

No invalidation rule: exits are the stop, the target, ``max_hold`` and force-flat.
"""

import math
from collections.abc import Mapping
from datetime import time, timedelta
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aurum.core.enums import Side
from aurum.core.models import PositionState, Signal
from aurum.core.timeframes import Timeframe
from aurum.features.view import MarketView
from aurum.settings import ClockTime


class FixedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    time_utc: ClockTime
    side: Side
    stop_points: int = Field(gt=0)
    target_points: int | None = Field(default=None, gt=0)
    max_hold_minutes: int | None = Field(default=None, gt=0)


class ReferenceParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: list[FixedEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        times = [e.time_utc for e in self.entries]
        if len(set(times)) != len(times):
            raise ValueError("entry times must be unique")
        return self


class FixedTimeReference:
    """See module docstring. ``point`` comes from the broker's symbol spec (never hard-coded)."""

    def __init__(
        self,
        params: ReferenceParams,
        *,
        symbol: str,
        point: float,
        decision_timeframe: Timeframe,
        strategy_id: str = "reference_fixed_time",
    ) -> None:
        for e in params.entries:
            if e.time_utc.minute % (decision_timeframe.seconds // 60):
                raise ValueError(f"entry {e.time_utc} is not a {decision_timeframe} bar close")
        self._params = params
        self._symbol = symbol
        self._point = point
        self._digits = max(0, round(-math.log10(point)))
        self._tf = decision_timeframe
        self._id = strategy_id
        self._entries: dict[time, FixedEntry] = {e.time_utc: e for e in params.entries}

    @property
    def strategy_id(self) -> str:
        return self._id

    @property
    def decision_timeframe(self) -> Timeframe:
        return self._tf

    @property
    def warmup_bars(self) -> Mapping[Timeframe, int]:
        return {}  # decides from the newest bar only

    def on_bar(self, view: MarketView) -> Signal | None:
        now = view.now
        entry = self._entries.get(now.time().replace(second=0, microsecond=0))
        if entry is None or now.second or now.microsecond:
            return None
        bars = view.bars(self._tf, 1)
        if len(bars) == 0 or bars.last_close_time != now:
            return None
        close = float(bars.close[-1])
        sign = entry.side.sign
        stop = round(close - sign * entry.stop_points * self._point, self._digits)
        target = (
            round(close + sign * entry.target_points * self._point, self._digits)
            if entry.target_points
            else None
        )
        hold = timedelta(minutes=entry.max_hold_minutes) if entry.max_hold_minutes else None
        return Signal(
            strategy_id=self._id,
            symbol=self._symbol,
            side=entry.side,
            time=now,
            data_as_of=now,
            stop_loss=stop,
            take_profit=target,
            max_hold=hold,
            reasons=(
                f"fixed entry at {entry.time_utc:%H:%M} UTC",
                f"{self._tf} close {close:.2f} at {now:%H:%M}",
            ),
            features={"close": close},
        )

    def invalidation(self, view: MarketView, position: PositionState, signal: Signal) -> str | None:
        return None
