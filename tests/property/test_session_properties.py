"""The session clock agrees with an independent oracle built on London wall-clock time.

The oracle never does UTC arithmetic: it converts the instant to Europe/London and compares
wall-clock times, so any DST mistake in the UTC-based implementation shows up as a mismatch.
"""

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aurum.core.enums import SessionPhase as P
from aurum.sessions import SessionClock
from aurum.settings import AppConfig, SessionsConfig, load_config

LONDON = ZoneInfo("Europe/London")
instants = st.datetimes(
    min_value=datetime(2020, 1, 1),  # noqa: DTZ001 - hypothesis bounds are naive by API
    max_value=datetime(2032, 1, 1),  # noqa: DTZ001
    timezones=st.just(UTC),
)


def oracle(cfg: SessionsConfig, t: datetime) -> tuple[P, str | None]:
    local = t.astimezone(LONDON)
    wall = local.time()
    if local.weekday() not in cfg.weekday_numbers:
        return P.CLOSED, None
    if wall >= cfg.force_flat_at:
        return P.FORCE_FLAT, None
    if wall >= cfg.no_new_entries_after:
        return P.EXIT_ONLY, None
    first = min(w.start for w in cfg.sessions.values())
    first_dt = datetime.combine(local.date(), first)
    pre = (first_dt - timedelta(minutes=cfg.pre_session_minutes)).time()
    if wall < pre:
        return P.CLOSED, None
    if wall < first:
        return P.PRE_SESSION, None
    for name, w in cfg.sessions.items():
        if w.start <= wall < w.end:
            return P.OPEN, name
    return P.NO_NEW_ENTRIES, None


@pytest.fixture(scope="module")
def cfg() -> SessionsConfig:
    return load_config(Path(__file__).resolve().parents[2] / "configs").sessions


@given(t=instants)
def test_matches_wall_clock_oracle(cfg: SessionsConfig, t: datetime) -> None:
    state = SessionClock(cfg).state_at(t)
    assert (state.phase, state.session) == oracle(cfg, t)


@given(t=instants)
def test_no_entries_outside_open_and_flat_outside_hours(cfg: SessionsConfig, t: datetime) -> None:
    state = SessionClock(cfg).state_at(t)
    wall = t.astimezone(LONDON).time()
    if state.entries_allowed:
        assert any(w.start <= wall < w.end for w in cfg.sessions.values())
    if wall >= cfg.force_flat_at or wall < time(6, 30):
        assert state.must_be_flat


_RANK = {
    P.CLOSED: 0,
    P.PRE_SESSION: 1,
    P.OPEN: 2,
    P.NO_NEW_ENTRIES: 2,
    P.EXIT_ONLY: 3,
    P.FORCE_FLAT: 4,
}


@given(t=instants, delta=st.timedeltas(timedelta(0), timedelta(hours=23)))
def test_phases_never_regress_within_a_trading_day(
    cfg: SessionsConfig, t: datetime, delta: timedelta
) -> None:
    clock = SessionClock(cfg)
    a, b = clock.state_at(t), clock.state_at(t + delta)
    if a.trading_day is not None and a.trading_day == b.trading_day:
        assert _RANK[b.phase] >= _RANK[a.phase]


def test_fixture_is_repo_config(cfg: SessionsConfig, app_config: AppConfig) -> None:
    assert cfg == app_config.sessions
