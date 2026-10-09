"""Spread statistics by session (`aurum data spreads`), to calibrate ``risk.max_spread_points``.

Computed with DuckDB straight over the ``normalized_ticks`` Parquet partitions. Spreads are
integer points (0.01 USD for a 2-digit XAUUSD).

* **Quote-weighted** distribution per trading session (sessions.yaml windows, per London date),
  for all market hours, and by London hour of day.
* **Decision-time** distribution: the spread in force at every M5 close inside a session, i.e.
  the last quote at or before that instant. A strategy (Phase 5) decides on completed M5/M15
  bars, so this is the spread the risk engine's ``max_spread_points`` check would see.
* **News-like spikes:** minutes whose mean spread exceeds ``spike_factor`` x the session's
  median, merged into events (consecutive minutes) and counted by London hour.

Days are chosen by the caller. The proposal uses tick days of ``exact`` fidelity only, because
thinned history (ADR-0006) can misstate spreads.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from aurum.sessions.clock import SessionClock

QUANTILES = (0.5, 0.9, 0.95, 0.99, 0.999)
CANDIDATE_CAPS = (15, 20, 25, 30, 40, 50, 75, 100)


@dataclass(frozen=True)
class SpreadProposal:
    max_spread_points: int
    basis: str


def _windows(clock: SessionClock, days: list[date]) -> pl.DataFrame:
    rows = []
    for d in sorted({d + timedelta(days=k) for d in days for k in (-1, 0, 1)}):
        schedule = clock.schedule_for(d)
        if schedule is not None:
            rows += [(w.name, w.start, w.end) for w in schedule.sessions]
    return pl.DataFrame(
        rows,
        schema={
            "session": pl.String,
            "start": pl.Datetime("ms", "UTC"),
            "stop": pl.Datetime("ms", "UTC"),
        },
        orient="row",
    )


def _quantile_cols(expr: str) -> str:
    qs = ", ".join(str(q) for q in QUANTILES)
    return (
        f"count(*) AS n, avg({expr}) AS mean, quantile_cont({expr}, [{qs}]) AS q, "
        f"max({expr}) AS max"
    )


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    names = [d[0] for d in cur.description or []]
    out = []
    for values in cur.fetchall():
        row = dict(zip(names, values, strict=True))
        if "q" in row:
            for q, v in zip(QUANTILES, row.pop("q"), strict=True):
                row[f"p{q * 100:g}"] = round(float(v), 2)
        if "mean" in row:
            row["mean"] = round(float(row["mean"]), 3)
        out.append(row)
    return out


def spread_stats(
    files: list[Path],
    clock: SessionClock,
    days: list[date],
    *,
    spike_factor: float,
    top_events: int = 20,
) -> dict[str, Any]:
    if not files:
        return {"days": 0}
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.register("windows", _windows(clock, days).to_arrow())
    paths = ", ".join(f"'{p.as_posix()}'" for p in files)
    con.execute(
        f"CREATE TEMP VIEW ticks AS SELECT time, spread_points FROM read_parquet([{paths}])"
    )
    con.execute(
        "CREATE TEMP TABLE tagged AS SELECT w.session, t.time, t.spread_points FROM ticks t "
        "JOIN windows w ON t.time >= w.start AND t.time < w.stop"
    )
    by_session = _rows(
        con,
        f"SELECT session, {_quantile_cols('spread_points')} FROM tagged GROUP BY 1 ORDER BY 1",
    )
    all_hours = _rows(
        con, f"SELECT 'all_market_hours' AS session, {_quantile_cols('spread_points')} FROM ticks"
    )
    by_hour = _rows(
        con,
        "SELECT hour(timezone('Europe/London', time)) AS london_hour, "
        f"{_quantile_cols('spread_points')} FROM ticks GROUP BY 1 ORDER BY 1",
    )
    # Decision instants: every M5 close strictly inside or at the end of a session window.
    con.execute(
        "CREATE TEMP TABLE decisions AS SELECT session, unnest(generate_series("
        "start + INTERVAL 5 MINUTE, stop, INTERVAL 5 MINUTE)) AS at FROM windows"
    )
    con.execute(
        "CREATE TEMP TABLE decided AS SELECT d.session, d.at, t.time AS quote_time, "
        "t.spread_points FROM decisions d ASOF JOIN ticks t ON t.time <= d.at "
        "WHERE d.at - t.time <= INTERVAL 1 MINUTE"
    )
    at_decision = _rows(
        con,
        f"SELECT session, {_quantile_cols('spread_points')} FROM decided GROUP BY 1 ORDER BY 1",
    )
    decision_all = _rows(
        con, f"SELECT 'all_sessions' AS session, {_quantile_cols('spread_points')} FROM decided"
    )
    blocked = _rows(
        con,
        "SELECT "
        + ", ".join(
            f"avg(CASE WHEN spread_points > {c} THEN 1.0 ELSE 0.0 END) AS cap_{c}"
            for c in CANDIDATE_CAPS
        )
        + " FROM decided",
    )[0]
    spikes = _spikes(con, spike_factor, top_events)
    return {
        "days": len(days),
        "first_day": min(days),
        "last_day": max(days),
        "quote_weighted": {"by_session": by_session + all_hours, "by_london_hour": by_hour},
        "at_decision_m5_close": decision_all + at_decision,
        "decisions_blocked_by_cap": {k: round(float(v), 5) for k, v in blocked.items()},
        "spikes": spikes,
    }


def _spikes(con: duckdb.DuckDBPyConnection, factor: float, top: int) -> dict[str, Any]:
    con.execute(
        "CREATE TEMP TABLE minute_max AS SELECT session, "
        "epoch_ms(time) // 60000 AS minute, max(spread_points) AS spread_max, "
        "avg(spread_points) AS spread_mean "
        "FROM tagged GROUP BY 1, 2"
    )
    con.execute(
        "CREATE TEMP TABLE medians AS SELECT session, median(spread_points) AS med "
        "FROM tagged GROUP BY 1"
    )
    con.execute(
        "CREATE TEMP TABLE spike_minutes AS SELECT m.session, m.minute, m.spread_max, s.med "
        f"FROM minute_max m JOIN medians s USING (session) WHERE m.spread_mean > {factor} * s.med"
    )
    events = _rows(
        con,
        "WITH runs AS (SELECT *, minute - row_number() OVER (PARTITION BY session ORDER BY minute) "
        "AS grp FROM spike_minutes) SELECT session, "
        "strftime(timezone('Europe/London', to_timestamp(min(minute) * 60)), '%Y-%m-%d %a %H:%M') "
        "AS london_start, count(*) AS minutes, max(spread_max) AS max_spread, any_value(med) AS "
        "session_median FROM runs GROUP BY session, grp ORDER BY max_spread DESC, london_start "
        f"LIMIT {top}",
    )
    by_hour = _rows(
        con,
        "SELECT hour(timezone('Europe/London', to_timestamp(minute * 60))) AS london_hour, "
        "count(*) AS spike_minutes FROM spike_minutes GROUP BY 1 ORDER BY 1",
    )
    total = con.execute("SELECT count(*) FROM spike_minutes").fetchone()
    return {
        "factor": factor,
        "spike_minutes": int(total[0]) if total else 0,
        "largest_events": events,
        "by_london_hour": by_hour,
    }


def propose_max_spread(stats: dict[str, Any]) -> SpreadProposal | None:
    """p99 of the decision-time spread across both sessions, rounded up to 5 points."""
    rows = [r for r in stats.get("at_decision_m5_close", []) if r["session"] == "all_sessions"]
    if not rows or not rows[0]["n"]:
        return None
    p99 = float(rows[0]["p99"])
    cap = int(math.ceil(p99 / 5) * 5)
    return SpreadProposal(
        max_spread_points=cap,
        basis=(
            f"p99 of the spread at {rows[0]['n']} M5 decision instants in the trading sessions "
            f"= {p99:g} points, rounded up to a multiple of 5"
        ),
    )
