"""Server clock conversion properties over 2015-2035."""

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from aurum.adapters.mt5.servertime import ServerTimeZone

TZ = ServerTimeZone()
instants = st.datetimes(
    min_value=datetime(2015, 1, 1),  # noqa: DTZ001 - hypothesis bounds are naive by design
    max_value=datetime(2035, 1, 1),  # noqa: DTZ001
    timezones=st.just(UTC),
)


def _in_repeated_hour(t: datetime) -> bool:
    """True for UTC instants whose server wall time occurs twice (US fall-back hour)."""
    return TZ.offset_at(t) != TZ.offset_at(t - timedelta(hours=1))


@given(instants)
def test_round_trip(t: datetime) -> None:
    if _in_repeated_hour(t):
        return  # second occurrence of a repeated wall time maps back to the first
    assert TZ.to_utc(TZ.to_server_seconds(t)) == t


@given(instants)
def test_offset_is_two_or_three_whole_hours(t: datetime) -> None:
    assert TZ.offset_at(t) in (timedelta(hours=2), timedelta(hours=3))


@given(instants)
def test_server_midnight_is_five_pm_new_york(t: datetime) -> None:
    server_wall = datetime.fromtimestamp(TZ.to_server_seconds(t), tz=UTC)
    ny = t.astimezone(TZ._zone)
    assert (server_wall.hour - ny.hour) % 24 == 7
    assert server_wall.minute == ny.minute


@given(instants, instants)
def test_monotonic(a: datetime, b: datetime) -> None:
    if a < b and not (_in_repeated_hour(a) or _in_repeated_hour(b)):
        assert TZ.to_server_seconds(a) < TZ.to_server_seconds(b)
