"""Server clock conversion properties over 2015-2035."""

from datetime import UTC, datetime, timedelta

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from aurum.adapters.mt5.servertime import ServerTimeZone
from aurum.core.timeutil import EPOCH

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


server_ms = st.integers(
    min_value=int(TZ.to_server_seconds(datetime(2015, 1, 1, tzinfo=UTC))) * 1000,
    max_value=int(TZ.to_server_seconds(datetime(2035, 1, 1, tzinfo=UTC))) * 1000,
)
# Values clustered around the US DST switches (server Sunday 08:00-11:00) to hit the edges.
dst_edges = st.sampled_from(
    [
        int(TZ.to_server_seconds(datetime(2026, 3, 8, 6, 0, tzinfo=UTC))) * 1000,
        int(TZ.to_server_seconds(datetime(2026, 11, 1, 5, 0, tzinfo=UTC))) * 1000,
    ]
).flatmap(lambda base: st.integers(min_value=base, max_value=base + 4 * 3_600_000))


@given(st.lists(server_ms | dst_edges, min_size=1, max_size=50))
def test_vectorised_ms_conversion_equals_scalar(values: list[int]) -> None:
    got = TZ.to_utc_ms(np.array(values, dtype=np.int64))
    want = [round((TZ.to_utc(v / 1000) - EPOCH).total_seconds() * 1000) for v in values]
    assert got.tolist() == want
