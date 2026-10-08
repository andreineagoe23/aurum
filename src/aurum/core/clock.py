"""Clock implementations. Code that needs "now" takes a ``ClockPort`` instead of calling
``datetime.now`` so tests and the simulator control time."""

from datetime import UTC, datetime, timedelta

from aurum.core.errors import ClockWentBackwardsError
from aurum.core.timeutil import to_utc


class SystemClock:
    """Wall-clock UTC time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Test/simulation clock that only moves when told to, and never backwards."""

    def __init__(self, start: datetime) -> None:
        self._now = to_utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, t: datetime) -> None:
        t = to_utc(t)
        if t < self._now:
            raise ClockWentBackwardsError(f"{t.isoformat()} < {self._now.isoformat()}")
        self._now = t

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ClockWentBackwardsError(f"negative advance {delta}")
        self._now += delta
        return self._now
