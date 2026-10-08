"""Session clock: maps any UTC instant to a ``SessionPhase`` (see ADR-0002).

Each trading day is built from Europe/London wall-clock times and converted to UTC for that
specific date with ``zoneinfo``, so DST changes are handled per day (07:00 London is 07:00 UTC in
winter and 06:00 UTC in summer).

Phase boundaries for a trading day D (all half-open, local times from configs/sessions.yaml):

=================  ==========================================================================
CLOSED             00:00 -> first session start - pre_session_minutes; whole non-trading days
PRE_SESSION        -> first session start
OPEN               inside a session window (clipped at no_new_entries_after)
NO_NEW_ENTRIES     between/after session windows, until no_new_entries_after
EXIT_ONLY          no_new_entries_after -> force_flat_at
FORCE_FLAT         force_flat_at -> 24:00 (exposure must be closed, also after a restart)
=================  ==========================================================================
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aurum.core.enums import SessionPhase
from aurum.core.models import SessionState
from aurum.core.timeutil import to_utc
from aurum.settings import SessionsConfig


@dataclass(frozen=True, slots=True)
class SessionWindowUtc:
    name: str
    start: datetime
    end: datetime

    def contains(self, t: datetime) -> bool:
        return self.start <= t < self.end


@dataclass(frozen=True, slots=True)
class DaySchedule:
    """UTC boundaries of one local trading day."""

    trading_day: date
    day_start: datetime
    pre_session_start: datetime
    sessions: tuple[SessionWindowUtc, ...]
    no_new_entries_after: datetime
    force_flat_at: datetime
    day_end: datetime

    @property
    def first_session_start(self) -> datetime:
        return self.sessions[0].start


class SessionClock:
    def __init__(self, config: SessionsConfig) -> None:
        self._config = config
        self._tz = ZoneInfo(config.timezone)
        self._weekdays = config.weekday_numbers

    @property
    def timezone(self) -> ZoneInfo:
        return self._tz

    def is_trading_day(self, local_date: date) -> bool:
        return local_date.weekday() in self._weekdays

    def local_to_utc(self, local_date: date, local_time: time) -> datetime:
        """UTC instant of a wall-clock time on ``local_date`` in the session timezone."""
        return datetime.combine(local_date, local_time, tzinfo=self._tz).astimezone(UTC)

    def schedule_for(self, local_date: date) -> DaySchedule | None:
        """UTC schedule for ``local_date``, or None if it is not a trading day."""
        if not self.is_trading_day(local_date):
            return None
        cfg = self._config
        nne = self.local_to_utc(local_date, cfg.no_new_entries_after)
        windows = tuple(
            SessionWindowUtc(
                name=name,
                start=self.local_to_utc(local_date, w.start),
                end=min(self.local_to_utc(local_date, w.end), nne),
            )
            for name, w in sorted(cfg.sessions.items(), key=lambda kv: kv[1].start)
        )
        return DaySchedule(
            trading_day=local_date,
            day_start=self.local_to_utc(local_date, time(0)),
            pre_session_start=windows[0].start - timedelta(minutes=cfg.pre_session_minutes),
            sessions=windows,
            no_new_entries_after=nne,
            force_flat_at=self.local_to_utc(local_date, cfg.force_flat_at),
            day_end=self.local_to_utc(local_date + timedelta(days=1), time(0)),
        )

    def state_at(self, t: datetime) -> SessionState:
        """Session state at instant ``t`` (any aware datetime; naive is rejected)."""
        utc = to_utc(t)
        local = utc.astimezone(self._tz)
        schedule = self.schedule_for(local.date())
        if schedule is None:
            return SessionState(time=utc, local_time=local, phase=SessionPhase.CLOSED)

        session: str | None = None
        if utc >= schedule.force_flat_at:
            phase = SessionPhase.FORCE_FLAT
        elif utc >= schedule.no_new_entries_after:
            phase = SessionPhase.EXIT_ONLY
        elif utc < schedule.pre_session_start:
            phase = SessionPhase.CLOSED
        elif utc < schedule.first_session_start:
            phase = SessionPhase.PRE_SESSION
        else:
            active = next((w for w in schedule.sessions if w.contains(utc)), None)
            phase = SessionPhase.OPEN if active else SessionPhase.NO_NEW_ENTRIES
            session = active.name if active else None

        return SessionState(
            time=utc,
            local_time=local,
            phase=phase,
            session=session,
            trading_day=schedule.trading_day,
            force_flat_at=schedule.force_flat_at,
        )
