"""Trading calendar: session phases (ADR-0002) restricted to when the market actually trades.

``SessionClock`` knows the London session windows; ``MarketHours`` (configs/data.yaml) knows
when XAUUSD trades: closed dates, early closes and the daily close at 16:59 New York (the
server-midnight break, ADR-0004). This class combines them, per trading day D:

* D is a closed date (or a weekend) -> CLOSED all day, no entries.
* ``cutoff = min(force_flat_at, market close of D - market_close_buffer_minutes)``. FORCE_FLAT
  starts at ``cutoff``; EXIT_ONLY at ``min(no_new_entries_after, cutoff)``; session windows are
  clipped to that and dropped if nothing is left.

With the default config the cutoff is 20:00 London on normal days (the market closes at 21:59
London in summer) and earlier only on US-holiday early closes, e.g. 13:01 New York = 18:01
London -> flat by 17:46 London with a 15-minute buffer.

The London local date D maps to the market's trading date D: a trading date runs from 18:00
New York on D-1 to 16:59 New York on D, i.e. 23:00 London on D-1 to 21:59 London on D, which
contains every allowed session time (windows may not cross midnight).
"""

from dataclasses import replace
from datetime import date, timedelta

from aurum.data.market_hours import MarketHours
from aurum.sessions.clock import DaySchedule, SessionClock, SessionWindowUtc
from aurum.settings import SessionsConfig


class TradingCalendar(SessionClock):
    def __init__(self, config: SessionsConfig, market: MarketHours) -> None:
        super().__init__(config)
        self._market = market
        self._buffer = timedelta(minutes=config.market_close_buffer_minutes)
        self._cache: dict[date, DaySchedule | None] = {}

    @property
    def market(self) -> MarketHours:
        return self._market

    def schedule_for(self, local_date: date) -> DaySchedule | None:
        """UTC schedule of ``local_date`` with market closures applied, or None if there is no
        trading at all (weekend, non-trading weekday or closed date)."""
        if local_date not in self._cache:
            self._cache[local_date] = self._build(local_date)
        return self._cache[local_date]

    def _build(self, local_date: date) -> DaySchedule | None:
        base = super().schedule_for(local_date)
        session = self._market.session(local_date)
        if base is None or session is None:
            return None
        cutoff = min(base.force_flat_at, session.close - self._buffer)
        no_new = min(base.no_new_entries_after, cutoff)
        windows = tuple(
            SessionWindowUtc(name=w.name, start=w.start, end=min(w.end, no_new))
            for w in base.sessions
            if w.start < no_new
        )
        first = windows[0].start if windows else no_new
        return replace(
            base,
            sessions=windows,
            pre_session_start=min(base.pre_session_start, first),
            no_new_entries_after=no_new,
            force_flat_at=cutoff,
            market_close=session.close,
            early_close=local_date in self._market.cfg.early_closes,
        )
