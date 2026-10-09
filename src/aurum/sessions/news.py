"""News blackout: no new entries around high-impact US releases (risk.yaml ``news_blackout``).

Windows are New York wall-clock times, so they follow US daylight time; ``reason_at`` converts
each instant with ``zoneinfo``. Two kinds:

* daily windows, every Monday-Friday (the 08:30 and 10:00 ET release slots), whether or not a
  release is scheduled that day: deterministic and backtestable without an event feed;
* the FOMC window on each listed statement date.

Only entries are blocked. An open position keeps its broker-side stop and target.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from aurum.core.timeutil import to_utc
from aurum.settings import NewsBlackoutConfig, TimeWindow


class NewsBlackout:
    def __init__(self, config: NewsBlackoutConfig) -> None:
        self._cfg = config
        self._tz = ZoneInfo(config.timezone)

    @property
    def last_fomc_date(self) -> date:
        """Latest listed FOMC statement date (``aurum doctor`` warns when it is near)."""
        return max(self._cfg.fomc_decisions)

    def reason_at(self, t: datetime) -> str | None:
        """Why entries are blocked at instant ``t``, or None if they are not."""
        local = to_utc(t).astimezone(self._tz)
        if local.weekday() >= 5:
            return None
        clock = local.time().replace(tzinfo=None)
        for w in self._cfg.daily_windows:
            if w.start <= clock < w.end:
                return self._describe(w)
        fomc = self._cfg.fomc_decisions.get(local.date())
        w = self._cfg.fomc_window
        if fomc is not None and w.start <= clock < w.end:
            return f"{self._describe(w)} ({fomc})"
        return None

    def _describe(self, w: TimeWindow) -> str:
        return f"news blackout {w.start:%H:%M}-{w.end:%H:%M} {self._cfg.timezone}: {w.reason}"
