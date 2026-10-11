"""opening_range_breakout 0.1.0: the implemented strategy against the feasibility traces.

Read-only, no P&L. Drives ``aurum.strategies.opening_range_breakout`` over the same
development-period bars as ``scripts/opening_range_breakout_feasibility.py`` and compares every
range-date with that script's CSV row: status, width, spending time, stop distance, eligible
decision times, ``data_gap`` and ``blackout_skipped``. The strategy is called at every decision
close with entries allowed, as the engine does, and sees only bars completed at the call
(``BarStore.view_at``). No fill, exit, return or P&L is computed; nothing is written to the
ledger. Bar files are selected by name before any is read: only trading dates up to
2025-09-30 (the day before the sealed period) are opened. Whole files are loaded.

Run from the repository root:
``uv run --no-sync python scripts/opening_range_breakout_trace_check.py``.
Writes docs/strategies/evidence/opening_range_breakout_2026-10-11_trace_check.md and exits 1
on any mismatch.
"""

# ruff: noqa: E501, I001  (report prose; aurum is imported before polars, see below)
# The VM's polars needs aurum._platform to run first, so aurum is imported before it.
import aurum  # noqa: F401

import csv
import hashlib
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from aurum.adapters.storage.layout import DataLayout, days_in
from aurum.adapters.storage.specs import pinned_spec
from aurum.core.models import Signal
from aurum.core.timeframes import Timeframe
from aurum.data.market_hours import MarketHours
from aurum.features.view import BarSeries, BarStore
from aurum.sessions.calendar import TradingCalendar
from aurum.sessions.news import NewsBlackout
from aurum.settings import load_settings
from aurum.strategies.opening_range_breakout import (
    OpeningRangeBreakout,
    OpeningRangeBreakoutParams,
)

OUT = Path("docs/strategies/evidence")
FEASIBILITY = OUT / "opening_range_breakout_2026-10-11_feasibility.csv"
STEM = "opening_range_breakout_2026-10-11_trace_check"
SYMBOL = "XAUUSD"
FIRST, LAST = date(2020, 12, 15), date(2025, 9, 30)
VARIANTS = {"m15": Timeframe.M15, "m5": Timeframe.M5}
TRACE_DAY = date(2024, 3, 1)
FIELDS = ("status", "width_points", "spend_time_utc", "d_opposite_points", "eligible_evaluated",
          "data_gap", "blackout_skipped")  # fmt: skip


def main() -> int:
    settings = load_settings()
    cfg = settings.config
    layout = DataLayout(settings.data_dir)
    if cfg.backtest.sealed_period.first <= LAST:
        raise SystemExit(f"{LAST} is not before the sealed period")
    pin = cfg.backtest.symbol_snapshot
    spec = pinned_spec(settings.data_dir, pin.file, pin.sha256).spec
    calendar = TradingCalendar(cfg.sessions, MarketHours(cfg.data.market))
    news = NewsBlackout(cfg.risk.news_blackout)

    series: dict[Timeframe, BarSeries] = {}
    digests: dict[str, tuple[int, str]] = {}
    for tf in (Timeframe.M15, Timeframe.M5):
        # Name filter first: no file dated in the sealed period (or after LAST) is opened.
        days = [
            d
            for d in days_in(layout.canonical_bars_dir(SYMBOL, tf))
            if FIRST - timedelta(days=1) <= d <= LAST
        ]
        paths = [layout.canonical_bars(SYMBOL, tf, d) for d in days]
        h = hashlib.sha256()
        for p in paths:
            h.update(p.name.encode())
            h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode())
        digests[tf.value] = (len(paths), h.hexdigest())
        series[tf] = BarSeries.from_frame(tf, pl.read_parquet(paths).sort("open_time"))
    store = BarStore(series)

    expected: dict[tuple[str, str, int, str], dict[str, str]] = {}
    with FEASIBILITY.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            expected[(row["date"], row["range"], int(row["or_bars"]), row["variant"])] = row

    def session_at(t: datetime) -> str | None:
        s = calendar.state_at(t)
        return s.session if s.entries_allowed else None

    dates = calendar.market.trading_dates(FIRST, LAST)
    rows: list[list[object]] = []
    mismatches: list[str] = []
    trace: list[str] = []
    for (variant, tf), n in ((v, n) for v in VARIANTS.items() for n in (2, 4)):
        strategy = OpeningRangeBreakout(
            OpeningRangeBreakoutParams(or_bars=n),  # type: ignore[arg-type]
            symbol=spec.name, point=spec.point, decision_timeframe=tf, session_at=session_at,
            blackout=news.reason_at, stops_level_points=spec.stops_level_points,
        )  # fmt: skip
        step = timedelta(seconds=tf.seconds)
        signals: list[Signal] = []
        for day in dates:
            sched = calendar.schedule_for(day)
            assert sched is not None
            for window in sched.sessions:
                t = window.start
                assert tf.is_aligned(t), t
                while t < window.end:
                    if session_at(t) is not None and (signal := strategy.on_bar(store.view_at(t))):
                        signals.append(signal)
                    t += step
        outcomes = strategy.outcomes()
        by_key = {(o.day.isoformat(), o.range): o for o in outcomes}
        status = Counter(o.status for o in outcomes)
        same = 0
        for (d, rng, nb, v), row in expected.items():
            if (nb, v) != (n, variant):
                continue
            o = by_key.get((d, rng))
            got = None if o is None else {
                "status": o.status,
                "width_points": "" if o.width_points is None else str(o.width_points),
                "spend_time_utc": o.spend_time.strftime("%Y-%m-%dT%H:%M:%SZ") if o.spend_time else "",
                "d_opposite_points": "" if o.stop_points is None else str(o.stop_points),
                "eligible_evaluated": str(o.eligible),
                "data_gap": str(o.data_gap),
                "blackout_skipped": str(o.blackout_skipped),
            }  # fmt: skip
            want = {k: row[k] for k in FIELDS}
            if got == want:
                same += 1
            else:
                mismatches.append(f"{d} {rng} {n} {variant}: expected {want}, got {got}")
        by_signal = {(o.day, o.range): o for o in outcomes if o.status == "signal"}
        signal_ok = len(signals) == status["signal"] and all(
            (o := next((x for x in by_signal.values() if x.spend_time == s.time), None)) is not None
            and o.stop == s.stop_loss
            and o.side is s.side
            for s in signals
        )
        if not signal_ok:
            mismatches.append(f"{variant} or_bars {n}: signals do not match the signal statuses")
        expected_n = sum(1 for (_, _, nb, v) in expected if (nb, v) == (n, variant))
        rows.append(
            [
                variant,
                n,
                len(outcomes),
                expected_n,
                same,
                len(signals),
                *(
                    status[s]
                    for s in (
                        "signal",
                        "stop_below_minimum",
                        "stop_above_cap",
                        "no_break",
                        "data_gap",
                        "range_missing",
                        "no_decision_time",
                    )
                ),
                strategy.diagnostics()["data_gap"],
                strategy.diagnostics()["blackout_skipped"],
            ]
        )
        for s in signals:
            if s.time.date() == TRACE_DAY and n == 2:
                trace.append(
                    f"- {variant}, or_bars 2: `{s.side}` at {s.time:%H:%M}Z, stop {s.stop_loss:.2f}, target {s.take_profit:.2f}, max_hold {s.max_hold}"
                )

    report = render(digests, len(dates), rows, mismatches, trace)
    (OUT / f"{STEM}.md").write_text(report, encoding="utf-8", newline="\n")
    print(f"written to {OUT / (STEM + '.md')}; mismatches: {len(mismatches)}")
    return 1 if mismatches else 0


def render(
    digests: dict[str, tuple[int, str]],
    n_dates: int,
    rows: list[list[object]],
    mismatches: list[str],
    trace: list[str],
) -> str:
    header = [
        "variant",
        "or_bars",
        "range-dates",
        "feasibility rows",
        "identical",
        "signals emitted",
        "signal",
        "stop_below_minimum",
        "stop_above_cap",
        "no_break",
        "data_gap",
        "range_missing",
        "no_decision_time",
        "Σ data_gap instants",
        "Σ blackout_skipped",
    ]
    lines = [
        "# opening_range_breakout 0.1.0: implementation against the feasibility traces (2026-10-11)",
        "",
        "Generated read-only by `scripts/opening_range_breakout_trace_check.py`. **No P&L**: no fill,",
        "exit, return or P&L is computed and nothing is written to the ledger. Whole development bar",
        "files are loaded; the strategy sees only bars completed at each call. This is not a backtest:",
        "there is no broker, risk engine or account, so every signal counted here is before the risk",
        "engine.",
        "",
        "## Inputs",
        "",
        f"- Trading dates {FIRST} … {LAST} from the market calendar: {n_dates}. Bar files dated",
        f"  {FIRST - timedelta(days=1)} … {LAST}, selected by name; none in the sealed period was opened.",
        *(
            f"- Canonical {tf} bars: {k} files, digest of (name, sha256) `{h}`."
            for tf, (k, h) in digests.items()
        ),
        "- Expected values: `opening_range_breakout_2026-10-11_feasibility.csv` (the draft's per-range-date",
        "  rows). Compared per range-date: status, `W`, spending time, `d`, eligible decision times,",
        "  `data_gap` and `blackout_skipped`. `target_r` 1.5 (it changes no status).",
        "- Calls: every decision-timeframe close with entries allowed (as the engine calls the strategy).",
        "",
        "## Result",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *("| " + " | ".join(str(c) for c in r) + " |" for r in rows),
        "",
        f"**Mismatches: {len(mismatches)}.**",
        *(f"- {m}" for m in mismatches[:50]),
        "",
        "Signals emitted equal the `signal` statuses, each at its range's spending time with the same",
        "side and stop.",
        "",
        f"## Trace, {TRACE_DAY} (spec section 17)",
        "",
        *trace,
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
