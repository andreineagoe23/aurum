"""Read-only checks of the live-collected XAUUSD ticks (owner, 2026-10-12): live vs history
fidelity for 2026-10-09 and Fusion's bid/ask spread by session. No broker access, no writes.

Read set, and nothing else (every file's sha256 is printed):

* ``raw_ticks/mt5_live/<symbol>/<day>/*.parquet`` for UTC days ``FIRST_DAY`` and later, only
  chunks whose first tick is before ``CUTOFF``; rows at or after ``CUTOFF`` are dropped;
* ``raw_ticks/mt5_history/<symbol>/2026-10-09.parquet`` (the one-day backfill);
* the pinned symbol snapshot (``backtest.symbol_snapshot``, sha256-checked) for ``point``;
* the YAML configs (sessions, ``risk.max_spread_points``).

Nothing dated before ``FIRST_DAY`` is opened: the sealed period ends 2026-10-08, and the script
refuses any day before 2026-10-09. It does not use ``aurum data qa`` or ``aurum data spreads``,
which read earlier days. Output: Markdown on stdout, identical on every run.

    python scripts/collected_ticks_check.py
"""

# ruff: noqa: I001  (aurum is imported before polars, see below)
# The VM's polars needs aurum._platform to run first, so aurum is imported before it.
import aurum  # noqa: F401

import hashlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from aurum.adapters.storage.layout import MT5_LIVE, DataLayout, days_in
from aurum.adapters.storage.live_ticks import ChunkName, epoch_ms
from aurum.adapters.storage.parquet import read_parquet
from aurum.adapters.storage.specs import pinned_spec
from aurum.data.schemas import RAW_TICKS
from aurum.sessions.clock import SessionClock
from aurum.settings import load_settings

FIRST_DAY = date(2026, 10, 9)
"""First UTC day read. The sealed period ends 2026-10-08."""
FIDELITY_DAY = date(2026, 10, 9)
"""The one day with both live-collected and backfilled history ticks."""
CUTOFF = datetime(2026, 10, 12, 15, 30, tzinfo=UTC)
"""Ticks at or after this instant are ignored, so reruns read the same rows."""
OUTSIDE = "outside_sessions"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def live_chunks(layout: DataLayout, symbol: str, day: date) -> list[Path]:
    if day < FIRST_DAY:
        raise SystemExit(f"refusing {day}: before {FIRST_DAY}")
    directory = layout.live_ticks_dir(symbol, day)
    named = [(ChunkName.parse(p), p) for p in directory.iterdir()]
    keep = [(n, p) for n, p in named if n is not None and n.first_ms < epoch_ms(CUTOFF)]
    return [p for _, p in sorted(keep, key=lambda np_: (np_[0].first_ms, np_[0].last_ms))]


def read_live(paths: list[Path]) -> pl.DataFrame:
    frame = pl.concat([read_parquet(p, RAW_TICKS) for p in paths])
    return frame.filter(pl.col("time") < CUTOFF)


def fidelity(live: pl.DataFrame, hist: pl.DataFrame) -> list[str]:
    """Rows only one side has, on (time, bid, ask), as in ``aurum.data.qa.live_vs_history``."""
    key = ["time", "bid", "ask"]

    def numbered(df: pl.DataFrame) -> pl.DataFrame:
        return df.select(key).with_columns(n=pl.int_range(pl.len()).over(key))

    lv, hs = numbered(live), numbered(hist)
    only_live = lv.join(hs, on=[*key, "n"], how="anti")
    only_hist = hs.join(lv, on=[*key, "n"], how="anti")
    lines = [
        "| measure | value |",
        "|---|---|",
        f"| live rows | {live.height:,} |",
        f"| history rows | {hist.height:,} |",
        f"| only in live | {only_live.height:,} |",
        f"| only in history | {only_hist.height:,} |",
        f"| live first / last tick | {fmt(live['time'].min())} / {fmt(live['time'].max())} |",
        f"| history first / last tick | {fmt(hist['time'].min())} / {fmt(hist['time'].max())} |",
        f"| flags differ on matched rows | {flag_diffs(live, hist):,} |",
    ]
    for name, rows in (("only in live", only_live), ("only in history", only_hist)):
        if rows.height:
            by_hour = rows.group_by(pl.col("time").dt.hour().alias("h")).len().sort("h").iter_rows()
            lines.append(
                f"| {name}, by UTC hour | " + ", ".join(f"{h:02d}h: {n}" for h, n in by_hour) + " |"
            )
    return lines


def flag_diffs(live: pl.DataFrame, hist: pl.DataFrame) -> int:
    key = ["time", "bid", "ask"]

    def numbered(df: pl.DataFrame) -> pl.DataFrame:
        return df.select([*key, "flags"]).with_columns(n=pl.int_range(pl.len()).over(key))

    both = numbered(live).join(numbered(hist), on=[*key, "n"], suffix="_h")
    return both.filter(pl.col("flags") != pl.col("flags_h")).height


def fmt(t: object) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z" if isinstance(t, datetime) else "-"


def session_windows(clock: SessionClock, first: date, last: date) -> pl.DataFrame:
    rows = []
    d = first - timedelta(days=1)
    while d <= last + timedelta(days=1):
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
    ).sort("start")


def spread_table(ticks: pl.DataFrame, windows: pl.DataFrame, point: float, cap: int) -> list[str]:
    valid = ticks.filter((pl.col("bid") > 0) & (pl.col("ask") > 0))
    tagged = (
        valid.sort("time")
        .with_columns(points=((pl.col("ask") - pl.col("bid")) / point).round(0).cast(pl.Int64))
        .join_asof(windows, left_on="time", right_on="start")
        .with_columns(
            session=pl.when(pl.col("end").is_not_null() & (pl.col("time") < pl.col("end")))
            .then(pl.col("session"))
            .otherwise(pl.lit(OUTSIDE))
        )
    )
    lines = [
        f"Invalid rows (bid or ask ≤ 0), left out: {ticks.height - valid.height:,}. "
        f"Crossed (ask < bid): {tagged.filter(pl.col('points') < 0).height:,}.",
        "",
        f"| segment | session | ticks | min | median | mean | p90 | p99 | max | > {cap} pts |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    segments = [
        ("2026-10-09 (first run)", tagged.filter(pl.col("time").dt.date() == FIDELITY_DAY)),
        (
            f"2026-10-11 … {fmt(CUTOFF)} (fetched at restart)",
            tagged.filter(pl.col("time").dt.date() > FIDELITY_DAY),
        ),
    ]
    for label, seg in segments:
        for session in ("london_morning", "ldn_ny_overlap", OUTSIDE, "all"):
            part = seg if session == "all" else seg.filter(pl.col("session") == session)
            if part.is_empty():
                lines.append(f"| {label} | {session} | 0 | | | | | | | |")
                continue
            p = part["points"]
            over = part.filter(pl.col("points") > cap).height
            lines.append(
                f"| {label} | {session} | {part.height:,} | {p.min()} | {p.median():.0f} | "
                f"{p.mean():.2f} | {p.quantile(0.9, 'nearest'):.0f} | "
                f"{p.quantile(0.99, 'nearest'):.0f} | {p.max()} | {100 * over / part.height:.2f}% |"
            )
    return lines


def main() -> None:
    settings = load_settings()
    layout = DataLayout(settings.data_dir)
    pin = settings.config.backtest.symbol_snapshot
    snapshot = pinned_spec(settings.data_dir, pin.file, pin.sha256)
    symbol, point = str(snapshot.spec.name), float(snapshot.spec.point)
    cap = settings.config.risk.max_spread_points

    live_days = [d for d in days_in(layout.raw_ticks_dir(symbol, MT5_LIVE)) if d >= FIRST_DAY]
    live_days = [d for d in live_days if d <= CUTOFF.date()]
    files = {d: live_chunks(layout, symbol, d) for d in live_days}
    history_path = layout.raw_ticks(symbol, FIDELITY_DAY)

    out = [
        "## Inputs",
        "",
        f"- Symbol `{symbol}`, point {point} (pinned snapshot `{pin.file}`, sha256 "
        f"`{snapshot.sha256[:12]}…`); `risk.max_spread_points` {cap}.",
        f"- Cutoff {fmt(CUTOFF)}; UTC days read: {', '.join(d.isoformat() for d in live_days)}.",
        f"- History: `{history_path.relative_to(settings.data_dir).as_posix()}` "
        f"sha256 `{sha256(history_path)}`.",
    ]
    for d, paths in files.items():
        digest = hashlib.sha256(
            "".join(f"{p.name}:{sha256(p)}\n" for p in paths).encode()
        ).hexdigest()
        out.append(f"- Live {d}: {len(paths)} chunks, digest of (name, sha256) `{digest}`.")

    live_fid = read_live(files[FIDELITY_DAY])
    hist = read_parquet(history_path, RAW_TICKS)
    out += ["", f"## Live vs history, {FIDELITY_DAY}", "", *fidelity(live_fid, hist)]

    ticks = pl.concat([read_live(paths) for paths in files.values()])
    windows = session_windows(SessionClock(settings.config.sessions), live_days[0], live_days[-1])
    out += [
        "",
        "## Spread by session (points; sessions.yaml windows; tick-weighted)",
        "",
        *spread_table(ticks, windows, point, cap),
    ]
    print("\n".join(out))


if __name__ == "__main__":
    main()
