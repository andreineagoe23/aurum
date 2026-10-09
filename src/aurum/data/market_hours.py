"""When XAUUSD trades (configs/data.yaml ``market``), for gap classification in the collector
and data QA. Pure: no I/O, no broker.

Trading date D (Monday..Friday) runs from ``open`` on D-1 to ``close`` on D, in the market
timezone (New York). Weekends and ``closed_dates`` have no session; ``early_closes`` end it early.
Times in and out are aware UTC.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aurum.core.timeutil import to_utc
from aurum.settings import MarketHoursConfig


@dataclass(frozen=True)
class MarketSession:
    trading_date: date
    open: datetime
    """UTC instant the session opens."""
    close: datetime
    """UTC instant the session closes (exclusive)."""


class MarketHours:
    def __init__(self, cfg: MarketHoursConfig) -> None:
        self.cfg = cfg
        self._zone = ZoneInfo(cfg.timezone)
        self.grace = timedelta(minutes=cfg.open_grace_minutes)

    def _utc(self, d: date, t: time) -> datetime:
        return datetime.combine(d, t, tzinfo=self._zone).astimezone(UTC)

    def session(self, trading_date: date) -> MarketSession | None:
        """The session of ``trading_date``, or None on weekends and closed dates."""
        if trading_date.weekday() >= 5 or trading_date in self.cfg.closed_dates:
            return None
        close = self.cfg.early_closes.get(trading_date, self.cfg.close)
        return MarketSession(
            trading_date=trading_date,
            open=self._utc(trading_date - timedelta(days=1), self.cfg.open),
            close=self._utc(trading_date, close),
        )

    def sessions_between(self, start: datetime, end: datetime) -> list[MarketSession]:
        """Sessions overlapping ``[start, end)``, oldest first."""
        start, end = to_utc(start), to_utc(end)
        first = start.astimezone(self._zone).date()
        last = end.astimezone(self._zone).date() + timedelta(days=1)
        out = []
        d = first
        while d <= last:
            s = self.session(d)
            if s is not None and s.open < end and s.close > start:
                out.append(s)
            d += timedelta(days=1)
        return out

    def is_open(self, t: datetime) -> bool:
        t = to_utc(t)
        sessions = self.sessions_between(t, t + timedelta(milliseconds=1))
        return any(s.open <= t < s.close for s in sessions)

    def expected_data_seconds(self, start: datetime, end: datetime) -> float:
        """Seconds of ``[start, end)`` in which ticks are expected: open market, excluding the
        first ``open_grace_minutes`` after each open."""
        start, end = to_utc(start), to_utc(end)
        total = 0.0
        for s in self.sessions_between(start, end):
            lo, hi = max(start, s.open + self.grace), min(end, s.close)
            if hi > lo:
                total += (hi - lo).total_seconds()
        return total

    def trading_dates(self, start: date, end: date) -> list[date]:
        """Trading dates in ``[start, end]`` that have a session."""
        out, d = [], start
        while d <= end:
            if self.session(d) is not None:
                out.append(d)
            d += timedelta(days=1)
        return out
