"""Data quality checks (`aurum data qa`), per trading date (New York 18:00 -> 16:59).

Checks, all on stored data (no broker access):

* **Coverage / gaps.** Every minute of every market session (``MarketHours``, minus the opening
  grace) should have a canonical M1 bar: gold trades continuously while open. Missing minutes are
  grouped into gaps. Gaps inside the configured trading sessions (sessions.yaml) and weekday
  trading dates with no data at all are *unexplained* unless the date is listed in data.yaml
  (``closed_dates`` / ``early_closes``, each with a reason). Gaps elsewhere in market hours are
  reported too.
* **Daily break and weekend (ADR-0004).** No bar may exist outside market hours: the daily break
  16:59-18:00 New York (= server 23:59-01:00), weekends, and closed dates.
* **Stale quotes.** On tick days, every interval without a tick longer than
  ``risk.stale_quote_seconds`` inside a trading session, split by tick fidelity (thinned history
  overstates staleness, ADR-0007).
* **Spread flags.** Counts of crossed and outlier-spread ticks.
* **Live vs history.** For days where the collector and the history backfill both have ticks:
  rows only one side has, compared on (time, bid, ask). Flags differ by design.
* **Collector journal.** Gaps, outages, pauses and disconnects it recorded.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import polars as pl

from aurum.adapters.storage.layout import MT5_HISTORY, DataLayout, days_in
from aurum.adapters.storage.live_ticks import CollectorJournal, LiveTickStore
from aurum.adapters.storage.parquet import read_parquet
from aurum.core.timeframes import Timeframe
from aurum.data.market_hours import MarketHours
from aurum.data.schemas import DAY_SOURCES, RAW_TICKS
from aurum.sessions.clock import SessionClock
from aurum.settings import KnownGap

MINUTE = timedelta(minutes=1)
UTC_MS = pl.Datetime("ms", "UTC")


@dataclass(frozen=True)
class Gap:
    start: datetime
    end: datetime
    """Exclusive."""
    minutes: int
    trading_date: date
    sessions: tuple[str, ...]
    """Trading-session windows (sessions.yaml) the gap overlaps; empty = outside sessions."""


@dataclass
class StaleSummary:
    intervals: int = 0
    max_seconds: float = 0.0
    longest: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class QaResult:
    first_trading_date: date | None
    last_trading_date: date | None
    trading_dates: int
    closed_dates: dict[str, str]
    early_closes: dict[str, str]
    missing_days: list[date]
    session_gaps: list[Gap]
    """Unexplained gaps inside the trading sessions."""
    explained_session_gaps: list[dict[str, Any]]
    """Session gaps inside a ``qa.known_gaps`` entry, with its reason."""
    market_gaps: list[Gap]
    """Gaps in market hours outside the trading sessions (reported, not gate-relevant)."""
    outside_hours: list[Gap]
    stale_quotes: dict[str, dict[str, StaleSummary]]
    """fidelity -> session -> summary."""
    tick_flags: dict[str, int]
    live_vs_history: list[dict[str, Any]]
    collector: dict[str, Any]

    @property
    def unexplained(self) -> int:
        return len(self.missing_days) + len(self.session_gaps) + len(self.outside_hours)


def _session_windows(clock: SessionClock, start: date, end: date) -> pl.DataFrame:
    rows = []
    d = start - timedelta(days=1)
    while d <= end + timedelta(days=1):
        schedule = clock.schedule_for(d)
        if schedule is not None:
            rows += [(w.name, w.start, w.end) for w in schedule.sessions]
        d += timedelta(days=1)
    return pl.DataFrame(
        rows,
        schema={
            "session": pl.String,
            "start": pl.Datetime("ms", "UTC"),
            "end": pl.Datetime("ms", "UTC"),
        },
        orient="row",
    )


def _overlapping(windows: pl.DataFrame, start: datetime, end: datetime) -> tuple[str, ...]:
    hit = windows.filter((pl.col("start") < end) & (pl.col("end") > start))
    return tuple(sorted(set(hit["session"].to_list())))


def _runs(minutes: pl.DataFrame, by_date: bool = True) -> pl.DataFrame:
    """Consecutive minutes -> runs (start, end exclusive, minutes, trading_date)."""
    if minutes.is_empty():
        return pl.DataFrame(
            schema={"start": UTC_MS, "end": UTC_MS, "minutes": pl.UInt32, "trading_date": pl.Date}
        )
    keys = ["trading_date"] if by_date else []
    return (
        minutes.sort("t")
        .with_columns(
            run=((pl.col("t") - pl.col("t").shift(1)) != pl.duration(minutes=1))
            .fill_null(True)
            .cum_sum()
        )
        .group_by(["run", *keys], maintain_order=True)
        .agg(
            start=pl.col("t").min(),
            end=pl.col("t").max() + pl.duration(minutes=1),
            minutes=pl.len(),
        )
        .drop("run")
    )


def coverage(
    m1_times: pl.Series,
    market: MarketHours,
    clock: SessionClock,
    start: date,
    end: date,
) -> tuple[list[date], list[Gap], list[Gap], list[Gap], int]:
    """(missing_days, session_gaps, market_gaps, outside_hours, trading_dates) for trading dates
    in ``[start, end]``. ``m1_times``: open times of every canonical M1 bar in the period."""
    dates = market.trading_dates(start, end)
    sessions = [s for s in (market.session(d) for d in dates) if s is not None]
    windows = _session_windows(clock, start, end)
    if not sessions:
        return [], [], [], [], 0
    expected = pl.DataFrame(
        {
            "trading_date": [s.trading_date for s in sessions],
            "lo": [_ceil_minute(s.open + market.grace) for s in sessions],
            "hi": [s.close - MINUTE for s in sessions],
            "open": [s.open for s in sessions],
            "close": [s.close for s in sessions],
        }
    ).with_columns(pl.col("lo", "hi", "open", "close").cast(UTC_MS))
    grid = expected.select(
        "trading_date",
        t=pl.datetime_ranges("lo", "hi", interval="1m", time_unit="ms", time_zone="UTC"),
    ).explode("t")
    present = pl.DataFrame({"t": m1_times}).with_columns(pl.col("t").cast(UTC_MS)).unique()
    lo, hi = check_span(market, start, end)
    present = present.filter((pl.col("t") >= lo) & (pl.col("t") < hi))
    missing = grid.join(present, on="t", how="anti")

    per_day = grid.join(present, on="t", how="semi").group_by("trading_date").len()
    days_with_data = set(per_day["trading_date"].to_list())
    missing_days = sorted(set(dates) - days_with_data)
    missing = missing.filter(~pl.col("trading_date").is_in(missing_days))

    session_gaps: list[Gap] = []
    market_gaps: list[Gap] = []
    for r in _runs(missing).iter_rows(named=True):
        overlap = _overlapping(windows, r["start"], r["end"])
        gap = Gap(r["start"], r["end"], r["minutes"], r["trading_date"], overlap)
        (session_gaps if overlap else market_gaps).append(gap)

    in_market = present.sort("t").join_asof(
        expected.select("open", "close", "trading_date").sort("open"),
        left_on="t",
        right_on="open",
    )
    outside = in_market.filter(pl.col("close").is_null() | (pl.col("t") >= pl.col("close")))
    outside_runs = _runs(outside.select("t"), by_date=False)
    outside_gaps = [
        Gap(
            r["start"],
            r["end"],
            r["minutes"],
            r["start"].date(),
            _overlapping(windows, r["start"], r["end"]),
        )
        for r in outside_runs.iter_rows(named=True)
    ]
    return missing_days, session_gaps, market_gaps, outside_gaps, len(dates)


def check_span(market: MarketHours, start: date, end: date) -> tuple[datetime, datetime]:
    """Instants whose bars belong to trading dates ``[start, end]``: from the close of the
    previous session to the open of the next one, so breaks, weekends and closed dates around
    the range are checked too."""
    before, after = start - timedelta(days=1), end + timedelta(days=1)
    while (prev := market.session(before)) is None:
        before -= timedelta(days=1)
    while (nxt := market.session(after)) is None:
        after += timedelta(days=1)
    return prev.close, nxt.open


def _ceil_minute(t: datetime) -> datetime:
    floor = t.replace(second=0, microsecond=0)
    return floor if floor == t else floor + MINUTE


def stale_quotes(
    ticks: pl.DataFrame, windows: pl.DataFrame, threshold_seconds: float, *, keep: int = 10
) -> dict[str, StaleSummary]:
    """Intervals between consecutive ticks longer than ``threshold_seconds`` that start inside a
    session window, per session. ``ticks`` is one or more days of normalized ticks."""
    if ticks.height < 2:
        return {}
    gaps = (
        ticks.select("time")
        .sort("time")
        .with_columns(next=pl.col("time").shift(-1))
        .drop_nulls()
        .with_columns(seconds=(pl.col("next") - pl.col("time")).dt.total_milliseconds() / 1000)
        .filter(pl.col("seconds") > threshold_seconds)
    )
    if gaps.is_empty():
        return {}
    tagged = (
        gaps.sort("time")
        .join_asof(windows.sort("start"), left_on="time", right_on="start")
        .filter(pl.col("end").is_not_null() & (pl.col("time") < pl.col("end")))
    )
    out: dict[str, StaleSummary] = {}
    for session, part in tagged.group_by("session"):
        longest = part.sort("seconds", descending=True).head(keep)
        out[str(session[0])] = StaleSummary(
            intervals=part.height,
            max_seconds=float(part["seconds"].max()),  # type: ignore[arg-type]
            longest=[
                {"from": r["time"].isoformat(), "seconds": r["seconds"]}
                for r in longest.iter_rows(named=True)
            ],
        )
    return out


def live_vs_history(layout: DataLayout, symbol: str) -> list[dict[str, Any]]:
    """Per day with both sources: rows (time, bid, ask) only in live / only in history."""
    store = LiveTickStore(layout, symbol)
    history_days = set(days_in(layout.raw_ticks_dir(symbol, MT5_HISTORY)))
    key = ["time", "bid", "ask"]

    def numbered(df: pl.DataFrame) -> pl.DataFrame:
        """Number repeated (time, bid, ask) rows so that multisets compare correctly."""
        return df.select(key).with_columns(n=pl.int_range(pl.len()).over(key))

    out = []
    for day in store.days():
        if day not in history_days:
            continue
        live = numbered(store.read_day(day))
        hist = numbered(read_parquet(layout.raw_ticks(symbol, day), RAW_TICKS))
        out.append(
            {
                "day": day.isoformat(),
                "live_rows": live.height,
                "history_rows": hist.height,
                "only_live": live.join(hist, on=[*key, "n"], how="anti").height,
                "only_history": hist.join(live, on=[*key, "n"], how="anti").height,
            }
        )
    return out


def collector_summary(layout: DataLayout, symbol: str) -> dict[str, Any]:
    events = CollectorJournal(layout, symbol).events()
    counts: dict[str, int] = {}
    for e in events:
        counts[e["event"]] = counts.get(e["event"], 0) + 1
    keep = ("gap", "outage", "pause", "disconnected", "history_changed", "connect_failed")
    return {"counts": counts, "events": [e for e in events if e["event"] in keep][-200:]}


def run_qa(
    layout: DataLayout,
    symbol: str,
    market: MarketHours,
    clock: SessionClock,
    *,
    start: date,
    end: date,
    stale_quote_seconds: float,
    known_gaps: Sequence[KnownGap] = (),
) -> QaResult:
    m1_dir = layout.canonical_bars_dir(symbol, Timeframe.M1)
    span_lo, span_hi = check_span(market, start, end)
    m1_days = [d for d in days_in(m1_dir) if span_lo.date() <= d <= span_hi.date()]
    times = (
        pl.concat(
            [
                read_parquet(layout.canonical_bars(symbol, Timeframe.M1, d)).select("open_time")
                for d in m1_days
            ]
        )["open_time"]
        if m1_days
        else pl.Series("open_time", [], dtype=pl.Datetime("ms", "UTC"))
    )
    missing_days, session_gaps, market_gaps, outside, n_dates = coverage(
        times, market, clock, start, end
    )
    session_gaps, explained = explain(session_gaps, known_gaps)

    windows = _session_windows(clock, start, end)
    fidelity = _fidelity_by_day(layout, symbol)
    stale: dict[str, dict[str, StaleSummary]] = {}
    flags = {"ticks": 0, "crossed": 0, "spread_outlier": 0}
    for d in days_in(layout.normalized_ticks_dir(symbol)):
        if not start - timedelta(days=1) <= d <= end:
            continue
        ticks = read_parquet(layout.normalized_ticks(symbol, d))
        flags["ticks"] += ticks.height
        flags["crossed"] += int(ticks["crossed"].sum())
        flags["spread_outlier"] += int(ticks["spread_outlier"].sum())
        bucket = stale.setdefault(fidelity.get(d, "unknown"), {})
        for session, s in stale_quotes(ticks, windows, stale_quote_seconds).items():
            agg = bucket.setdefault(session, StaleSummary())
            agg.intervals += s.intervals
            agg.max_seconds = max(agg.max_seconds, s.max_seconds)
            agg.longest = sorted(agg.longest + s.longest, key=lambda x: -float(x["seconds"]))[:10]
    dates = market.trading_dates(start, end)
    return QaResult(
        first_trading_date=dates[0] if dates else None,
        last_trading_date=dates[-1] if dates else None,
        trading_dates=n_dates,
        closed_dates={
            d.isoformat(): r for d, r in market.cfg.closed_dates.items() if start <= d <= end
        },
        early_closes={
            d.isoformat(): f"{e.close:%H:%M} {e.reason}"
            for d, e in market.cfg.early_closes.items()
            if start <= d <= end
        },
        missing_days=missing_days,
        session_gaps=session_gaps,
        explained_session_gaps=explained,
        market_gaps=market_gaps,
        outside_hours=outside,
        stale_quotes=stale,
        tick_flags=flags,
        live_vs_history=live_vs_history(layout, symbol),
        collector=collector_summary(layout, symbol),
    )


def _fidelity_by_day(layout: DataLayout, symbol: str) -> dict[date, str]:
    path = layout.day_manifest(symbol)
    if not path.exists():
        return {}
    manifest = read_parquet(path, DAY_SOURCES).drop_nulls("tick_fidelity")
    return dict(zip(manifest["day"].to_list(), manifest["tick_fidelity"].to_list(), strict=True))


def explain(gaps: list[Gap], known: Sequence[KnownGap]) -> tuple[list[Gap], list[dict[str, Any]]]:
    """Split ``gaps`` into (unexplained, explained-with-reason) by ``known`` gap windows."""
    unexplained, explained = [], []
    for gap in gaps:
        match = next((k for k in known if k.start <= gap.start and gap.end <= k.end), None)
        if match is None:
            unexplained.append(gap)
        else:
            explained.append({**asdict(gap), "reason": match.reason})
    return unexplained, explained
