"""Timeframes and completed-bar logic.

Conventions (see ADR-0003):

* A bar is identified by its **open time** (UTC), like MT5.
* ``close_time = open_time + duration``. The bar covers ``[open_time, close_time)``.
* A bar is *completed*, and therefore visible to decisions, only once ``now >= close_time``.
  At 10:35 the latest visible H1 bar is the one that opened 09:00 and closed 10:00.
* Bars are aligned to multiples of their duration since the Unix epoch. For M1..H1 this equals
  alignment on any whole-hour-offset server clock, so it matches broker bars.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from aurum.core.timeutil import EPOCH, to_utc


@dataclass(frozen=True, slots=True)
class BarSpan:
    """The time interval of one bar: ``[open_time, close_time)`` in UTC."""

    timeframe: "Timeframe"
    open_time: datetime
    close_time: datetime


class Timeframe(StrEnum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    H1 = "H1"

    @classmethod
    def parse(cls, value: str) -> "Timeframe":
        """Case-insensitive parse, e.g. ``"m15"`` -> ``Timeframe.M15``."""
        try:
            return cls(value.strip().upper())
        except ValueError:
            valid = ", ".join(tf.value for tf in cls)
            raise ValueError(f"unknown timeframe {value!r}; expected one of {valid}") from None

    @property
    def duration(self) -> timedelta:
        return _DURATIONS[self]

    @property
    def seconds(self) -> int:
        return int(self.duration.total_seconds())

    @property
    def mt5_constant_name(self) -> str:
        """Name of the matching ``MetaTrader5.TIMEFRAME_*`` constant."""
        return f"TIMEFRAME_{self.value}"

    def is_aligned(self, t: datetime) -> bool:
        """True if ``t`` is exactly on a bar boundary of this timeframe."""
        return (to_utc(t) - EPOCH) % self.duration == timedelta(0)

    def bar_open_time(self, t: datetime) -> datetime:
        """Open time (UTC) of the bar that contains instant ``t`` (the possibly-forming bar)."""
        utc = to_utc(t)
        return EPOCH + ((utc - EPOCH) // self.duration) * self.duration

    def bar_close_time(self, open_time: datetime) -> datetime:
        """Close time (UTC) of the bar opening at ``open_time``. Raises if not aligned."""
        if not self.is_aligned(open_time):
            raise ValueError(f"{open_time.isoformat()} is not a {self.value} bar open time")
        return to_utc(open_time) + self.duration

    def last_completed_bar(self, now: datetime) -> BarSpan:
        """The most recent bar with ``close_time <= now``: the newest bar a decision may use."""
        close = self.bar_open_time(now)
        return BarSpan(timeframe=self, open_time=close - self.duration, close_time=close)


_DURATIONS: Final[dict[Timeframe, timedelta]] = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.H1: timedelta(hours=1),
}
