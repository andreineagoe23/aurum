"""Session clock + state machine, including UK DST changes (last Sunday of March / October).

The wall-clock oracle in tests/property/test_session_properties.py sweeps 2020-2032; these
are the hand-checked anchors.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from aurum.core.enums import SessionPhase as P
from aurum.core.errors import ClockWentBackwardsError
from aurum.sessions import SessionClock, SessionStateMachine
from aurum.settings import AppConfig


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.fixture
def clock(app_config: AppConfig) -> SessionClock:
    return SessionClock(app_config.sessions)


# --------------------------------------------------------------------------- DST schedules

# (last Sunday of March, last Sunday of October) — the UK clock-change days.
DST_SUNDAYS = [
    (date(2025, 3, 30), date(2025, 10, 26)),
    (date(2026, 3, 29), date(2026, 10, 25)),
    (date(2027, 3, 28), date(2027, 10, 31)),
]


def test_uk_clock_changes(clock: SessionClock) -> None:
    for march, october in DST_SUNDAYS:
        # The clock-change Sundays themselves are closed all day.
        for sunday in (march, october):
            assert sunday.weekday() == 6
            assert clock.schedule_for(sunday) is None
            for hour in range(24):
                assert clock.state_at(
                    utc(sunday.year, sunday.month, sunday.day, hour, 30)
                ).phase is (P.CLOSED)
        # Spring forward: 07:00 London is 07:00 UTC on Friday, 06:00 UTC on Monday.
        friday_gmt = clock.schedule_for(march - timedelta(days=2))
        monday_bst = clock.schedule_for(march + timedelta(days=1))
        assert friday_gmt is not None
        assert monday_bst is not None
        assert friday_gmt.sessions[0].start.time() == time(7, 0)
        assert monday_bst.sessions[0].start.time() == time(6, 0)
        assert friday_gmt.force_flat_at.time() == time(20, 0)
        assert monday_bst.force_flat_at.time() == time(19, 0)
        assert (monday_bst.sessions[1].start.time(), monday_bst.sessions[1].end.time()) == (
            time(12, 0),
            time(15, 30),
        )
        # Fall back: one hour later in UTC.
        friday_bst = clock.schedule_for(october - timedelta(days=2))
        monday_gmt = clock.schedule_for(october + timedelta(days=1))
        assert friday_bst is not None
        assert monday_gmt is not None
        assert friday_bst.sessions[0].start.time() == time(6, 0)
        assert monday_gmt.sessions[0].start.time() == time(7, 0)
        assert friday_bst.no_new_entries_after.time() == time(18, 30)
        assert monday_gmt.no_new_entries_after.time() == time(19, 30)


def test_schedule_values_are_utc_and_the_spring_day_has_23_hours(clock: SessionClock) -> None:
    schedule = clock.schedule_for(date(2026, 7, 1))
    assert schedule is not None
    assert schedule.pre_session_start == utc(2026, 7, 1, 5, 30)
    assert schedule.day_start == utc(2026, 6, 30, 23, 0)
    assert schedule.day_end == utc(2026, 7, 1, 23, 0)
    assert all(w.start.tzinfo is UTC for w in schedule.sessions)
    sunday = date(2026, 3, 29)
    start = clock.local_to_utc(sunday, time(0))
    end = clock.local_to_utc(sunday + timedelta(days=1), time(0))
    assert end - start == timedelta(hours=23)


# --------------------------------------------------------------------------- phases

# Monday 2026-03-30 (BST, London = UTC+1) and Monday 2026-10-26 (GMT, London = UTC).
PHASES = [
    (utc(2026, 3, 29, 23, 0), P.CLOSED, None),  # 00:00 London Monday
    (utc(2026, 3, 30, 5, 29), P.CLOSED, None),
    (utc(2026, 3, 30, 5, 30), P.PRE_SESSION, None),
    (utc(2026, 3, 30, 6, 0), P.OPEN, "london_morning"),
    (utc(2026, 3, 30, 9, 59, 59), P.OPEN, "london_morning"),
    (utc(2026, 3, 30, 10, 0), P.NO_NEW_ENTRIES, None),
    (utc(2026, 3, 30, 12, 0), P.OPEN, "ldn_ny_overlap"),
    (utc(2026, 3, 30, 15, 30), P.NO_NEW_ENTRIES, None),
    (utc(2026, 3, 30, 18, 29), P.NO_NEW_ENTRIES, None),
    (utc(2026, 3, 30, 18, 30), P.EXIT_ONLY, None),
    (utc(2026, 3, 30, 19, 0), P.FORCE_FLAT, None),
    (utc(2026, 3, 30, 22, 59), P.FORCE_FLAT, None),
    (utc(2026, 3, 30, 23, 0), P.CLOSED, None),  # 00:00 London Tuesday
    (utc(2026, 10, 26, 6, 30), P.PRE_SESSION, None),
    (utc(2026, 10, 26, 7, 0), P.OPEN, "london_morning"),
    (utc(2026, 10, 26, 19, 30), P.EXIT_ONLY, None),
    (utc(2026, 10, 26, 20, 0), P.FORCE_FLAT, None),
    (utc(2026, 10, 27, 0, 0), P.CLOSED, None),
    (utc(2026, 10, 23, 19, 30), P.FORCE_FLAT, None),  # Friday 20:30 BST
    (utc(2026, 10, 24, 12, 0), P.CLOSED, None),  # Saturday
]


def test_phase_at(clock: SessionClock) -> None:
    for t, phase, session in PHASES:
        state = clock.state_at(t)
        assert (state.phase, state.session) == (phase, session), t
        assert state.entries_allowed is (phase is P.OPEN), t
    flat = {P.CLOSED, P.PRE_SESSION, P.FORCE_FLAT}
    assert {p for p in P if p.must_be_flat} == flat


def test_state_carries_trading_day_and_cutoff(clock: SessionClock) -> None:
    state = clock.state_at(utc(2026, 3, 30, 8, 0))
    assert state.trading_day == date(2026, 3, 30)
    assert state.force_flat_at == utc(2026, 3, 30, 19, 0)
    assert state.local_time.hour == 9
    weekend = clock.state_at(utc(2026, 10, 24, 12, 0))
    assert weekend.trading_day is None
    assert weekend.force_flat_at is None
    assert weekend.must_be_flat


# --------------------------------------------------------------------------- state machine


def test_first_update_always_emits_so_restart_triggers_force_flat(clock: SessionClock) -> None:
    machine = SessionStateMachine(clock)
    transition = machine.update(utc(2026, 3, 30, 20, 0))  # 21:00 BST, restart after cutoff
    assert transition is not None
    assert transition.is_startup
    assert transition.phase is P.FORCE_FLAT
    assert transition.current.must_be_flat


def test_full_day_sweep_yields_canonical_sequence(clock: SessionClock) -> None:
    """One transition per phase change and none in between."""
    machine = SessionStateMachine(clock)
    seen: list[tuple[P, str | None]] = []
    t = utc(2026, 3, 29, 23, 0)  # Monday 00:00 London
    while t <= utc(2026, 3, 31, 0, 0):
        tr = machine.update(t)
        if tr is not None:
            seen.append((tr.phase, tr.current.session))
        t += timedelta(minutes=1)
    assert seen == [
        (P.CLOSED, None),
        (P.PRE_SESSION, None),
        (P.OPEN, "london_morning"),
        (P.NO_NEW_ENTRIES, None),
        (P.OPEN, "ldn_ny_overlap"),
        (P.NO_NEW_ENTRIES, None),
        (P.EXIT_ONLY, None),
        (P.FORCE_FLAT, None),
        (P.CLOSED, None),  # Tuesday 00:00 London
    ]


def test_new_trading_day_is_a_transition_even_if_phase_repeats(clock: SessionClock) -> None:
    machine = SessionStateMachine(clock)
    machine.update(utc(2026, 3, 30, 5, 0))  # Monday CLOSED
    tr = machine.update(utc(2026, 3, 31, 5, 0))  # Tuesday CLOSED (jumped a day)
    assert tr is not None
    assert tr.previous is not None
    assert tr.previous.trading_day == date(2026, 3, 30)
    assert tr.current.trading_day == date(2026, 3, 31)


def test_time_cannot_go_backwards(clock: SessionClock) -> None:
    machine = SessionStateMachine(clock)
    machine.update(utc(2026, 3, 30, 12, 0))
    with pytest.raises(ClockWentBackwardsError):
        machine.update(utc(2026, 3, 30, 11, 59))
