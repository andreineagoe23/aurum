"""Process watchdog: the trading loop writes a heartbeat file; ``aurum watchdog`` checks it.

The loop (shadow/demo apps, Phase 7/8) calls ``Heartbeat.beat`` every cycle. The watchdog,
run by the OS scheduler, reads the file. When the trader should be running (from the
pre-session start to 30 minutes after the cutoff on a trading day) and its heartbeat is
missing or older than ``max_age``, the watchdog engages the kill switch. In DEMO mode
``aurum watchdog`` also flattens Aurum's positions at once. A hung or crashed trader therefore
cannot leave exposure unmanaged past the cutoff, even though broker-side stops already bound
the loss of each trade.

The file is written atomically (temporary file + rename), so a reader never sees half a beat.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from aurum.core.enums import SessionPhase
from aurum.core.timeutil import to_utc
from aurum.sessions.clock import SessionClock

AFTER_CUTOFF = timedelta(minutes=30)
"""How long after the cutoff the trader must still be alive (to finish flattening)."""


@dataclass(frozen=True, slots=True)
class Beat:
    time: datetime
    process: str
    detail: str


class Heartbeat:
    def __init__(self, path: Path, process: str) -> None:
        self.path = path
        self.process = process

    def beat(self, now: datetime, detail: str = "") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {"time": to_utc(now).isoformat(), "process": self.process, "detail": detail}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(body) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


def read_heartbeat(path: Path) -> Beat | None:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return Beat(datetime.fromisoformat(body["time"]), body["process"], body.get("detail", ""))


class Verdict(StrEnum):
    OK = "OK"
    IDLE = "IDLE"
    """The trader is not required to run now (outside the trading window)."""
    STALE = "STALE"
    MISSING = "MISSING"


@dataclass(frozen=True, slots=True)
class WatchdogResult:
    verdict: Verdict
    detail: str
    age: timedelta | None = None

    @property
    def alarm(self) -> bool:
        return self.verdict in (Verdict.STALE, Verdict.MISSING)


def must_run(calendar: SessionClock, now: datetime) -> bool:
    """True from the pre-session start until ``AFTER_CUTOFF`` after the cutoff of a trading day."""
    state = calendar.state_at(now)
    if state.trading_day is None:
        return False
    sched = calendar.schedule_for(state.trading_day)
    if sched is None:
        return False
    if state.phase is SessionPhase.CLOSED and now < sched.pre_session_start:
        return False
    return now < sched.force_flat_at + AFTER_CUTOFF


def check_heartbeat(
    path: Path, now: datetime, *, max_age: timedelta, calendar: SessionClock
) -> WatchdogResult:
    beat = read_heartbeat(path)
    required = must_run(calendar, now)
    if beat is None:
        if required:
            return WatchdogResult(Verdict.MISSING, f"no heartbeat at {path}")
        return WatchdogResult(Verdict.IDLE, "no heartbeat; trader not required now")
    age = to_utc(now) - beat.time
    detail = f"{beat.process} beat {age.total_seconds():.0f} s ago ({beat.detail})"
    if age <= max_age:
        return WatchdogResult(Verdict.OK, detail, age)
    if required:
        return WatchdogResult(Verdict.STALE, detail, age)
    return WatchdogResult(Verdict.IDLE, detail, age)
