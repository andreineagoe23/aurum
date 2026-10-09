"""How often would the worst-case intra-bar rule decide a trade? (reports/phase_3.md §4)

Research measurement, not part of the platform. For hypothetical entries every 30 minutes
inside the London sessions (07:00-11:00 and 13:00-16:30 London) on every stored day: BUY at the
bid close of the M1 bar ending on :00/:30, stop = entry - s points, target = entry + 1.5 s
points, held up to 240 minutes within the same UTC day. An exit is **ambiguous** when the
first M1 bar touching either level touches both: on bar-only days the simulator then assumes
the stop came first (worst case) and flags the trade.

On exact tick days (ADR-0007) every ambiguous bar is also resolved with the stored ticks, to
measure how often the stop really came first.

    uv run python scripts/intrabar_ambiguity.py
"""

# ruff: noqa: I001  (aurum must be imported before polars on the VM; CLAUDE.md)
from collections import defaultdict

import aurum  # noqa: F401  (before polars; see aurum._platform)
import numpy as np
import polars as pl

from aurum.adapters.storage.layout import DataLayout, days_in
from aurum.core.timeframes import Timeframe
from aurum.settings import load_settings

SYMBOL = "XAUUSD"
POINT = 0.01
STOPS = (200, 300, 500, 1000)
TARGET_MULTIPLE = 1.5
HOLD_MINUTES = 240
SESSIONS_LONDON = ((7 * 60, 11 * 60), (13 * 60, 16 * 60 + 30))


def main() -> None:
    layout = DataLayout(load_settings().data_dir)
    manifest = pl.read_parquet(layout.day_manifest(SYMBOL))
    exact = {
        np.datetime64(d, "D")
        for d in manifest.filter(pl.col("tick_fidelity") == "exact")["day"].to_list()
    }
    m1_dir = layout.canonical_bars_dir(SYMBOL, Timeframe.M1)
    bars = pl.concat(
        [pl.read_parquet(layout.canonical_bars(SYMBOL, Timeframe.M1, d)) for d in days_in(m1_dir)]
    ).sort("open_time")
    ldn = bars["open_time"].dt.convert_time_zone("Europe/London")
    minute = (ldn.dt.hour().cast(pl.Int32) * 60 + ldn.dt.minute().cast(pl.Int32)).to_numpy()
    open_ms = bars["open_time"].dt.epoch("ms").to_numpy()
    high, low, close = (bars[c].to_numpy() for c in ("high", "low", "close"))
    day = bars["open_time"].dt.date().to_numpy().astype("datetime64[D]")
    year = bars["open_time"].dt.year().to_numpy()
    in_session = np.zeros(len(minute), dtype=bool)
    for start, end in SESSIONS_LONDON:
        in_session |= (minute >= start) & (minute < end)
    entries = np.flatnonzero(in_session & (minute % 30 == 29))

    counts: dict[tuple[int, int, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    on_ticks: dict[int, list[int]] = defaultdict(lambda: [0, 0])  # stop first, target first
    ticks: dict[np.datetime64, pl.DataFrame] = {}
    for i in entries:
        seg = slice(i + 1, min(i + 1 + HOLD_MINUTES, len(open_ms)))
        same_day = day[seg] == day[i]
        h, lo, t = high[seg][same_day], low[seg][same_day], open_ms[seg][same_day]
        kind = "exact" if day[i] in exact else "bars"
        for s in STOPS:
            sl, tp = close[i] - s * POINT, close[i] + TARGET_MULTIPLE * s * POINT
            hit_sl, hit_tp = np.flatnonzero(lo <= sl), np.flatnonzero(h >= tp)
            row = counts[(s, int(year[i]), kind)]
            row[0] += 1
            if not len(hit_sl) and not len(hit_tp):
                continue
            row[1] += 1
            if not (len(hit_sl) and len(hit_tp) and hit_sl[0] == hit_tp[0]):
                continue
            row[2] += 1
            if kind != "exact":
                continue
            if day[i] not in ticks:
                ticks[day[i]] = pl.read_parquet(layout.normalized_ticks(SYMBOL, day[i].item()))
            tk = ticks[day[i]]
            bar_start = t[hit_sl[0]]
            in_bar = tk.filter(
                (pl.col("time").dt.epoch("ms") >= bar_start)
                & (pl.col("time").dt.epoch("ms") < bar_start + 60_000)
            )["bid"].to_numpy()
            first_sl, first_tp = np.flatnonzero(in_bar <= sl), np.flatnonzero(in_bar >= tp)
            if len(first_sl) and (not len(first_tp) or first_sl[0] < first_tp[0]):
                on_ticks[s][0] += 1
            elif len(first_tp):
                on_ticks[s][1] += 1

    table = pl.DataFrame(
        [
            {
                "stop_points": s,
                "year": y,
                "days": kind,
                "entries": n,
                "exits_at_a_level": exited,
                "ambiguous": amb,
                "share": round(amb / exited, 4) if exited else None,
            }
            for (s, y, kind), (n, exited, amb) in sorted(counts.items())
        ]
    )
    pl.Config.set_tbl_rows(200)
    print(table)
    print(
        table.group_by("stop_points", "days")
        .agg(pl.col("exits_at_a_level").sum(), pl.col("ambiguous").sum())
        .with_columns((pl.col("ambiguous") / pl.col("exits_at_a_level")).round(4).alias("share"))
        .sort("days", "stop_points")
    )
    for s in STOPS:
        stop_first, target_first = on_ticks[s]
        print(
            f"exact days, stop {s}: ambiguous bars on ticks: stop first {stop_first}, "
            f"target first {target_first}"
        )


if __name__ == "__main__":
    main()
