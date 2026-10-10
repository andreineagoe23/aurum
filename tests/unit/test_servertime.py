"""MT5 server clock (New York + 7h) <-> UTC, anchored on values measured in Phase 1.

tests/property/test_servertime_properties.py covers round trips and monotonicity 2015-2035.
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from aurum.adapters.mt5.servertime import OffsetCheck, ServerTimeZone, check_offset

TZ = ServerTimeZone()  # Fusion Markets: America/New_York + 7h


def server(y: int, mo: int, d: int, h: int, mi: int = 0) -> float:
    """Server wall-clock time written as epoch seconds, the way MetaTrader5 returns it."""
    return datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp()


def utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_measured_phase1_sample() -> None:
    # 2026-10-09: tick time_msc 1791516327052 observed at system UTC 1791505527.3 (+10800 s).
    assert TZ.to_utc(1791516327.052) == datetime.fromtimestamp(1791505527.052, tz=UTC)
    # Vectorised: server 03:35:42.123 (UTC+3) and a US-winter value (UTC+2).
    summer = int(TZ.to_server_seconds(datetime(2026, 10, 9, 0, 35, 42, tzinfo=UTC))) * 1000 + 123
    winter = int(TZ.to_server_seconds(datetime(2026, 1, 13, 14, 0, tzinfo=UTC))) * 1000
    got = TZ.to_utc_ms(np.array([summer, winter], dtype=np.int64))
    assert got.tolist() == [summer - 3 * 3_600_000, winter - 2 * 3_600_000]
    assert TZ.to_utc_ms(np.array([], dtype=np.int64)).size == 0


def test_known_conversions() -> None:
    for server_wall, expected_utc, offset_h in [
        # US summer time (EDT, UTC-4) -> server UTC+3
        (server(2026, 7, 1, 12), utc(2026, 7, 1, 9), 3),
        # US winter time (EST, UTC-5) -> server UTC+2
        (server(2026, 1, 15, 12), utc(2026, 1, 15, 10), 2),
        # Between the US (Mar 8) and EU (Mar 29) switches: already +3, unlike EET
        (server(2026, 3, 16, 12), utc(2026, 3, 16, 9), 3),
        # Between the EU (Oct 25) and US (Nov 1) switches: still +3
        (server(2026, 10, 28, 12), utc(2026, 10, 28, 9), 3),
        # Daily break: server midnight == 17:00 New York, all year
        (server(2026, 7, 2, 0), utc(2026, 7, 1, 21), 3),
        (server(2026, 12, 2, 0), utc(2026, 12, 1, 22), 2),
    ]:
        assert TZ.to_utc(server_wall) == expected_utc
        assert TZ.to_server_seconds(expected_utc) == server_wall
        assert TZ.offset_at(expected_utc) == timedelta(hours=offset_h)
    eet = ServerTimeZone(timezone="Europe/Athens", shift_hours=0)  # other rules configurable
    assert eet.offset_at(utc(2026, 3, 16, 12)) == timedelta(hours=2)


def test_us_dst_edges() -> None:
    # US clocks change Sun 2026-03-08 02:00 EST = 07:00 UTC (server 09:00 -> 10:00 jump).
    assert TZ.offset_at(utc(2026, 3, 8, 6, 59)) == timedelta(hours=2)
    assert TZ.offset_at(utc(2026, 3, 8, 7, 0)) == timedelta(hours=3)
    # Sun 2026-11-01 02:00 EDT = 06:00 UTC: server wall 09:00 -> 08:00 (repeats one hour).
    assert TZ.offset_at(utc(2026, 11, 1, 5, 59)) == timedelta(hours=3)
    assert TZ.offset_at(utc(2026, 11, 1, 6, 0)) == timedelta(hours=2)
    # A repeated wall time resolves to its first occurrence; the market is closed then.
    assert TZ.to_utc(server(2026, 11, 1, 8, 30)) == utc(2026, 11, 1, 5, 30)


@pytest.mark.parametrize(
    ("quote_offset_s", "status"),
    [
        (10_799.2, OffsetCheck.VERIFIED),
        (10_800 - 3600 * 50, OffsetCheck.STALE_QUOTE),  # last tick Friday night, now Sunday
        (10_800 + 3600, OffsetCheck.MISMATCH),  # server really at +4h
    ],
)
def test_check_offset(quote_offset_s: float, status: OffsetCheck) -> None:
    now = utc(2026, 10, 9, 0, 35)
    m = check_offset(TZ, now.timestamp() + quote_offset_s, now)
    assert m.status is status
    assert m.expected == timedelta(hours=3)
    assert m.residual == timedelta(seconds=quote_offset_s - 10_800)
