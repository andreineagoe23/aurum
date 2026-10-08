"""Session state machine: turns a stream of clock readings into phase transitions.

The first ``update`` always emits a transition (``previous=None``). That is deliberate: after a
restart at 21:00 with an open position, consumers see "entered FORCE_FLAT" and flatten, exactly
as if the process had been running at 20:00.
"""

from dataclasses import dataclass
from datetime import datetime

from aurum.core.enums import SessionPhase
from aurum.core.errors import ClockWentBackwardsError
from aurum.core.models import SessionState
from aurum.core.timeutil import to_utc
from aurum.sessions.clock import SessionClock


@dataclass(frozen=True, slots=True)
class SessionTransition:
    previous: SessionState | None
    current: SessionState

    @property
    def phase(self) -> SessionPhase:
        return self.current.phase

    @property
    def is_startup(self) -> bool:
        return self.previous is None


class SessionStateMachine:
    def __init__(self, clock: SessionClock) -> None:
        self._clock = clock
        self._state: SessionState | None = None

    @property
    def state(self) -> SessionState | None:
        return self._state

    def update(self, now: datetime) -> SessionTransition | None:
        """Advance to ``now``. Returns a transition when phase, session or trading day changed.

        Time may jump forward arbitrarily (restart, sleep); it may never go backwards.
        """
        now = to_utc(now)
        previous = self._state
        if previous is not None and now < previous.time:
            raise ClockWentBackwardsError(f"{now.isoformat()} < {previous.time.isoformat()}")
        current = self._clock.state_at(now)
        self._state = current
        if previous is None or (previous.phase, previous.session, previous.trading_day) != (
            current.phase,
            current.session,
            current.trading_day,
        ):
            return SessionTransition(previous=previous, current=current)
        return None
