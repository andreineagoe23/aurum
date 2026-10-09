"""MT5 server time <-> UTC (ADR-0004).

The MetaTrader5 Python API speaks *server wall-clock time encoded as epoch seconds*: every
``time``/``time_msc`` it returns, and every ``date_from``/``date_to`` it accepts, is the server's
local wall clock written as if it were UTC. Fusion Markets runs its server on New York + 7h
(UTC+2 in US winter time, UTC+3 in US summer time), so a naive reading is 2-3 hours in the future.

This module is the only place that conversion happens. Pure Python: no MetaTrader5 import.

DST edges: the New York clock repeats 01:00-02:00 in November and skips 02:00-03:00 in March
(server 08:00-10:00 on a Sunday, when gold does not trade). A repeated server wall time resolves
to its first occurrence (``fold=0``).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

import numpy as np
import numpy.typing as npt

from aurum.core.timeutil import EPOCH, to_utc

_HOUR_MS = 3_600_000


def _epoch_ms(t: datetime) -> int:
    return (t - EPOCH) // timedelta(milliseconds=1)


@dataclass(frozen=True)
class ServerTimeZone:
    """Server wall clock = wall clock of ``timezone`` shifted by ``shift_hours``."""

    timezone: str = "America/New_York"
    shift_hours: int = 7

    @property
    def _zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def _shift(self) -> timedelta:
        return timedelta(hours=self.shift_hours)

    def offset_at(self, utc: datetime) -> timedelta:
        """Server clock minus UTC at instant ``utc`` (e.g. +3h in US summer time)."""
        local_offset = to_utc(utc).astimezone(self._zone).utcoffset()
        assert local_offset is not None  # ZoneInfo always yields an offset for aware datetimes
        return local_offset + self._shift

    def to_utc(self, server_seconds: float) -> datetime:
        """UTC instant for an MT5 server timestamp (seconds; use ``ms / 1000`` for ``time_msc``)."""
        wall = (EPOCH + timedelta(seconds=server_seconds)).replace(tzinfo=None)
        local = (wall - self._shift).replace(tzinfo=self._zone, fold=0)
        return local.astimezone(UTC)

    def to_utc_ms(self, server_ms: npt.ArrayLike) -> npt.NDArray[np.int64]:
        """Vectorised ``to_utc`` for epoch milliseconds (e.g. a tick array's ``time_msc``).

        Exactly equal to ``to_utc`` per element: the offset can only change on a whole server
        hour (DST switches happen at 02:00 New York, and the shift is whole hours), so it is
        computed once per distinct server hour.
        """
        ms = np.asarray(server_ms, dtype=np.int64)
        if ms.size == 0:
            return ms.copy()
        hours, inverse = np.unique(ms // _HOUR_MS, return_inverse=True)
        offsets = np.array(
            [h * _HOUR_MS - _epoch_ms(self.to_utc(h * 3600)) for h in hours.tolist()],
            dtype=np.int64,
        )
        return ms - offsets[inverse.reshape(ms.shape)]

    def to_server_seconds(self, utc: datetime) -> float:
        """MT5 server timestamp (seconds) for a UTC instant, e.g. for ``copy_rates_range``."""
        utc = to_utc(utc)
        return (utc + self.offset_at(utc) - EPOCH).total_seconds()


class OffsetCheck(StrEnum):
    VERIFIED = "VERIFIED"
    """A fresh quote agrees with the configured offset."""
    STALE_QUOTE = "STALE_QUOTE"
    """The latest quote is older than tolerance (market closed?): offset not verifiable now."""
    MISMATCH = "MISMATCH"
    """The quote is *ahead* of the configured offset: the server clock rule is wrong."""


@dataclass(frozen=True)
class OffsetMeasurement:
    status: OffsetCheck
    expected: timedelta
    """Configured server offset at ``now``."""
    measured: timedelta
    """Latest quote's server timestamp minus true UTC now (includes quote age)."""

    @property
    def residual(self) -> timedelta:
        return self.measured - self.expected


def check_offset(
    zone: ServerTimeZone,
    latest_quote_server_seconds: float,
    now: datetime,
    tolerance: timedelta = timedelta(seconds=120),
) -> OffsetMeasurement:
    """Compare the newest quote's server timestamp with the configured offset at ``now``.

    A quote can be old (no ticks while the market is closed), never from the future. So a
    negative residual beyond ``tolerance`` is merely unverifiable, but a positive one means the
    configured rule is wrong (or the system clock is off) and timestamps must not be trusted.
    """
    now = to_utc(now)
    expected = zone.offset_at(now)
    measured = timedelta(seconds=latest_quote_server_seconds) - (now - EPOCH)
    residual = measured - expected
    if abs(residual) <= tolerance:
        status = OffsetCheck.VERIFIED
    elif residual < timedelta(0):
        status = OffsetCheck.STALE_QUOTE
    else:
        status = OffsetCheck.MISMATCH
    return OffsetMeasurement(status=status, expected=expected, measured=measured)
