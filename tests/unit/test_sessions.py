"""Session clock + state machine, including UK DST changes (last Sunday of March / October)."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from aurum.core.enums import SessionPhase as P
from aurum.core.errors import ClockWentBackwardsError, NaiveDatetimeError
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


@pytest.mark.parametrize(("march", "october"), DST_SUNDAYS)
def test_clock_change_sundays_are_closed_all_day(
    clock: SessionClock, march: date, october: date
) -> None:
    for sunday in (march, october):
        assert sunday.weekday() == 6
        assert clock.schedule_for(sunday) is None
        for hour in range(24):
            assert (
                clock.state_at(
                    datetime(sunday.year, sunday.month, sunday.day, hour, 30, tzinfo=UTC)
                ).phase
                is P.CLOSED
            )


@pytest.mark.parametrize(("march", "october"), DST_SUNDAYS)
def test_spring_forward_shifts_utc_times_one_hour_earlier(
    clock: SessionClock, march: date, october: date
) -> None:
    friday_gmt = clock.schedule_for(march - timedelta(days=2))
    monday_bst = clock.schedule_for(march + timedelta(days=1))
    assert friday_gmt is not None
    assert monday_bst is not None
    assert friday_gmt.sessions[0].start.time() == time(7, 0)  # 07:00 London == 07:00 UTC
    assert monday_bst.sessions[0].start.time() == time(6, 0)  # 07:00 London == 06:00 UTC
    assert friday_gmt.force_flat_at.time() == time(20, 0)
    assert monday_bst.force_flat_at.time() == time(19, 0)
    assert monday_bst.sessions[1].start.time() == time(12, 0)
    assert monday_bst.sessions[1].end.time() == time(15, 30)


@pytest.mark.parametrize(("march", "october"), DST_SUNDAYS)
def test_fall_back_shifts_utc_times_one_hour_later(
    clock: SessionClock, march: date, october: date
) -> None:
    friday_bst = clock.schedule_for(october - timedelta(days=2))
    monday_gmt = clock.schedule_for(october + timedelta(days=1))
    assert friday_bst is not None
    assert monday_gmt is not None
    assert friday_bst.sessions[0].start.time() == time(6, 0)
    assert monday_gmt.sessions[0].start.time() == time(7, 0)
    assert friday_bst.no_new_entries_after.time() == time(18, 30)
    assert monday_gmt.no_new_entries_after.time() == time(19, 30)


def test_local_midnight_on_23_hour_day(clock: SessionClock) -> None:
    sunday = date(2026, 3, 29)
    start = clock.local_to_utc(sunday, time(0))
    end = clock.local_to_utc(sunday + timedelta(days=1), time(0))
    assert end - start == timedelta(hours=23)


def test_schedule_values_are_utc(clock: SessionClock) -> None:
    schedule = clock.schedule_for(date(2026, 7, 1))
    assert schedule is not None
    assert schedule.pre_session_start == utc(2026, 7, 1, 5, 30)
    assert schedule.day_start == utc(2026, 6, 30, 23, 0)
    assert schedule.day_end == utc(2026, 7, 1, 23, 0)
    assert all(w.start.tzinfo is UTC for w in schedule.sessions)


# --------------------------------------------------------------------------- phases

# Monday 2026-03-30 (BST, London = UTC+1)
BST_MONDAY = [
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
]

# Monday 2026-10-26 (GMT, London = UTC)
GMT_MONDAY = [
    (utc(2026, 10, 26, 6, 29), P.CLOSED, None),
    (utc(2026, 10, 26, 6, 30), P.PRE_SESSION, None),
    (utc(2026, 10, 26, 7, 0), P.OPEN, "london_morning"),
    (utc(2026, 10, 26, 11, 0), P.NO_NEW_ENTRIES, None),
    (utc(2026, 10, 26, 13, 0), P.OPEN, "ldn_ny_overlap"),
    (utc(2026, 10, 26, 16, 30), P.NO_NEW_ENTRIES, None),
    (utc(2026, 10, 26, 19, 30), P.EXIT_ONLY, None),
    (utc(2026, 10, 26, 20, 0), P.FORCE_FLAT, None),
    (utc(2026, 10, 26, 23, 59), P.FORCE_FLAT, None),
    (utc(2026, 10, 27, 0, 0), P.CLOSED, None),
]


@pytest.mark.parametrize(("t", "phase", "session"), BST_MONDAY + GMT_MONDAY)
def test_phase_at(clock: SessionClock, t: datetime, phase: P, session: str | None) -> None:
    state = clock.state_at(t)
    assert state.phase is phase
    assert state.session == session
    assert state.entries_allowed is (phase is P.OPEN)


def test_weekend_and_friday_evening(clock: SessionClock) -> None:
    assert clock.state_at(utc(2026, 10, 23, 19, 30)).phase is P.FORCE_FLAT  # Fri 20:30 BST
    assert clock.state_at(utc(2026, 10, 24, 12, 0)).phase is P.CLOSED  # Saturday
    state = clock.state_at(utc(2026, 10, 24, 12, 0))
    assert state.trading_day is None
    assert state.force_flat_at is None
    assert state.must_be_flat


def test_state_carries_trading_day_and_cutoff(clock: SessionClock) -> None:
    state = clock.state_at(utc(2026, 3, 30, 8, 0))
    assert state.trading_day == date(2026, 3, 30)
    assert state.force_flat_at == utc(2026, 3, 30, 19, 0)
    assert state.local_time.hour == 9


@pytest.mark.parametrize(
    ("phase", "flat"),
    [
        (P.CLOSED, True),
        (P.PRE_SESSION, True),
        (P.OPEN, False),
        (P.NO_NEW_ENTRIES, False),
        (P.EXIT_ONLY, False),
        (P.FORCE_FLAT, True),
    ],
)
def test_must_be_flat(phase: P, flat: bool) -> None:
    assert phase.must_be_flat is flat


def test_naive_time_rejected(clock: SessionClock) -> None:
    with pytest.raises(NaiveDatetimeError):
        clock.state_at(datetime(2026, 3, 30, 8, 0))  # noqa: DTZ001


# --------------------------------------------------------------------------- state machine


def test_first_update_always_emits_so_restart_triggers_force_flat(clock: SessionClock) -> None:
    machine = SessionStateMachine(clock)
    transition = machine.update(utc(2026, 3, 30, 20, 0))  # 21:00 BST, restart after cutoff
    assert transition is not None
    assert transition.is_startup
    assert transition.phase is P.FORCE_FLAT
    assert transition.current.must_be_flat


def test_no_transition_without_change(clock: SessionClock) -> None:
    machine = SessionStateMachine(clock)
    machine.update(utc(2026, 3, 30, 6, 0))
    assert machine.update(utc(2026, 3, 30, 6, 1)) is None
    assert machine.state is not None
    assert machine.state.time == utc(2026, 3, 30, 6, 1)


def test_full_day_sweep_yields_canonical_sequence(clock: SessionClock) -> None:
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
