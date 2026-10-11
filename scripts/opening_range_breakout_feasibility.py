"""opening_range_breakout 0.1.0 draft: feasibility evidence (read-only, no P&L).

Applies the draft spec's range, eligibility, breakout and stop rules (sections 5, 6, 8, 12) to
development-period bars and counts what the rules would do up to the close that spends each
range. Nothing after that close is read; no fill, exit or P&L is computed; nothing is written
to the ledger. Bar files are selected by name before any is read: only trading dates up to
2025-09-30 (the day before the sealed period) are opened.

It reproduces exactly what the draft's data-informed choices were based on, and nothing more:
the opposite-side stop (O2) with the midpoint alternative it was compared with, and the two
news-blackout treatments (O3). It is not the strategy implementation.

Run from the repository root: ``uv run --no-sync python scripts/opening_range_breakout_feasibility.py``.
Writes docs/strategies/evidence/opening_range_breakout_2026-10-11_feasibility.{md,csv}.
"""

# ruff: noqa: E501, I001  (report prose; aurum is imported before polars, see below)
# The VM's polars needs aurum._platform to run first, so aurum is imported before it.
import aurum  # noqa: F401

import csv
import hashlib
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from aurum.adapters.storage.layout import DataLayout, days_in
from aurum.adapters.storage.specs import pinned_spec
from aurum.core.timeframes import Timeframe
from aurum.data.market_hours import MarketHours
from aurum.sessions.calendar import TradingCalendar
from aurum.sessions.news import NewsBlackout
from aurum.settings import load_settings

OUT = Path("docs/strategies/evidence")
STEM = "opening_range_breakout_2026-10-11_feasibility"
SYMBOL = "XAUUSD"
FIRST, LAST = date(2020, 12, 15), date(2025, 9, 30)  # evaluated trading period (O7)
LONDON, NEW_YORK = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")
# Draft 0.1.0 rule constants (spec sections 5, 6, 8, 14).
RANGES = {
    "london": (time(8, 0), LONDON, "london_morning"),
    "newyork": (time(9, 30), NEW_YORK, "ldn_ny_overlap"),
}
OR_BARS = (2, 4)
VARIANTS = {"m15": Timeframe.M15, "m5": Timeframe.M5}
WINDOW = timedelta(minutes=120)
BUFFER_POINTS = 20
MIN_STOP_POINTS = 500
CAP_POINTS = 2000
TRACE_DAY = date(2024, 3, 1)

Bar = tuple[float, float, float]  # high, low, close


@dataclass
class Outcome:
    """One range on one trading date for one variant and ``or_bars``, under the draft rules."""

    status: str
    width: int | None = None
    spend_time: datetime | None = None
    d_opposite: int | None = None
    d_midpoint: int | None = None
    data_gap: int = 0
    blackout_skipped: int = 0
    eligible: int = 0
    trace: list[str] = field(default_factory=list)


def main() -> int:
    settings = load_settings()
    cfg = settings.config
    layout = DataLayout(settings.data_dir)
    sealed_first = cfg.backtest.sealed_period.first
    if sealed_first <= LAST:
        raise SystemExit(f"{LAST} is not before the sealed period ({sealed_first})")
    snap = pinned_spec(
        settings.data_dir, cfg.backtest.symbol_snapshot.file, cfg.backtest.symbol_snapshot.sha256
    )
    point = snap.spec.point
    market = MarketHours(cfg.data.market)
    calendar = TradingCalendar(cfg.sessions, market)
    news = NewsBlackout(cfg.risk.news_blackout)

    files: dict[str, list[Path]] = {}
    bars: dict[Timeframe, dict[datetime, Bar]] = {}
    for tf in (Timeframe.M15, Timeframe.M5):
        # Name filter first: no file dated in the sealed period (or after LAST) is opened.
        days = [
            d
            for d in days_in(layout.canonical_bars_dir(SYMBOL, tf))
            if FIRST - timedelta(days=1) <= d <= LAST
        ]
        paths = [layout.canonical_bars(SYMBOL, tf, d) for d in days]
        files[tf.value] = paths
        frame = pl.concat(
            [pl.read_parquet(p, columns=["open_time", "high", "low", "close"]) for p in paths]
        )
        bars[tf] = {
            o: (h, lo, c)
            for o, h, lo, c in zip(
                frame["open_time"], frame["high"], frame["low"], frame["close"], strict=True
            )
        }

    state_cache: dict[datetime, tuple[str | None, bool]] = {}
    news_cache: dict[datetime, bool] = {}

    def session_ok(t: datetime, session: str) -> bool:
        if t not in state_cache:
            s = calendar.state_at(t)
            state_cache[t] = (s.session, s.entries_allowed)
        name, allowed = state_cache[t]
        return allowed and name == session

    def blackout(t: datetime) -> bool:
        if t not in news_cache:
            news_cache[t] = news.reason_at(t) is not None
        return news_cache[t]

    def stop_distance(close: float, high: float, low: float, frac: float) -> int:
        """Rule 8 (frac 1.0 = opposite side) or the midpoint alternative (frac 0.5)."""
        width = high - low
        if close > high:  # long: stop below
            stop = math.floor(round((high - frac * width) / point - BUFFER_POINTS, 6)) * point
            return round((close - stop) / point)
        stop = math.ceil(round((low + frac * width) / point + BUFFER_POINTS, 6)) * point
        return round((stop - close) / point)

    def evaluate(
        day: date, rng: str, n: int, variant: str, spend_in_blackout: bool, trace: bool = False
    ) -> Outcome:
        anchor_time, tz, session = RANGES[rng]
        tf = VARIANTS[variant]
        step = timedelta(minutes=15 if tf is Timeframe.M15 else 5)
        anchor = datetime.combine(day, anchor_time, tz).astimezone(UTC)
        range_bars = [bars[Timeframe.M15].get(anchor + timedelta(minutes=15 * k)) for k in range(n)]
        present = [b for b in range_bars if b is not None]
        range_ok = len(present) == n
        high = max(b[0] for b in present) if range_ok else math.nan
        low = min(b[1] for b in present) if range_ok else math.nan
        out = Outcome(status="no_decision_time")
        if range_ok:
            out.width = round((high - low) / point)
        if trace:
            for k, b in enumerate(range_bars):
                o = anchor + timedelta(minutes=15 * k)
                out.trace.append(
                    f"range bar {o:%H:%M}–{o + timedelta(minutes=15):%H:%M}Z: "
                    + (f"H {b[0]:.2f} L {b[1]:.2f}" if b else "missing")
                )
            if range_ok:
                out.trace.append(f"H {high:.2f}, L {low:.2f}, W {out.width} points")
        range_end = anchor + timedelta(minutes=15 * n)
        missing_seen = not range_ok
        t = range_end + step
        while t <= range_end + WINDOW:
            bar = bars[tf].get(t - step)  # the bar closing at t
            if bar is None:
                missing_seen = True
            if not session_ok(t, session):  # rules 6.1–6.3: not a decision instant at all
                t += step
                continue
            is_blackout = blackout(t)
            outside = bar is not None and range_ok and (bar[2] > high or bar[2] < low)
            if trace:
                out.trace.append(
                    f"{t:%H:%M}Z ({t.astimezone(LONDON):%H:%M} London, {t.astimezone(NEW_YORK):%H:%M} New York): "
                    + (f"close {bar[2]:.2f}" if bar else "bar missing")
                    + (", blackout" if is_blackout else "")
                    + (", outside" if outside else "")
                )
            if is_blackout and not spend_in_blackout:  # rule 12.3: neither signals nor spends
                if outside and not missing_seen:
                    out.blackout_skipped += 1
                t += step
                continue
            if not is_blackout:
                out.eligible += 1
            if missing_seen:  # read set incomplete: no signal, one data_gap per eligible time
                if not is_blackout:
                    out.data_gap += 1
                t += step
                continue
            if outside:
                assert bar is not None
                out.spend_time = t
                out.d_opposite = stop_distance(bar[2], high, low, 1.0)
                out.d_midpoint = stop_distance(bar[2], high, low, 0.5)
                if is_blackout:
                    out.status = "spent_in_blackout"
                elif out.d_opposite < MIN_STOP_POINTS:
                    out.status = "stop_below_minimum"
                elif out.d_opposite > CAP_POINTS:
                    out.status = "stop_above_cap"
                else:
                    out.status = "signal"
                return _finish(out, range_ok)
            t += step
        if out.eligible:
            out.status = "data_gap" if out.data_gap else "no_break"
        return _finish(out, range_ok)

    def window(day: date, rng: str, n: int, variant: str) -> tuple[list[datetime], int, str]:
        """Eligible decision times of one range-day from the calendar alone (no prices), the
        number of blackout instants skipped, and the DST regime of that day."""
        anchor_time, tz, session = RANGES[rng]
        step = timedelta(minutes=15 if VARIANTS[variant] is Timeframe.M15 else 5)
        anchor = datetime.combine(day, anchor_time, tz).astimezone(UTC)
        range_end = anchor + timedelta(minutes=15 * n)
        eligible, skipped, t = [], 0, range_end + step
        while t <= range_end + WINDOW:
            if session_ok(t, session):
                if blackout(t):
                    skipped += 1
                else:
                    eligible.append(t)
            t += step
        ny = datetime.combine(day, time(9, 30), NEW_YORK).astimezone(LONDON).time()
        return eligible, skipped, "normal" if ny == time(14, 30) else "mismatch"

    dates = market.trading_dates(FIRST, LAST)
    keys = [(r, n, v) for r in RANGES for n in OR_BARS for v in VARIANTS]
    results: dict[tuple[str, int, str], list[tuple[date, Outcome]]] = {k: [] for k in keys}
    alt: dict[tuple[str, int, str], list[tuple[date, Outcome]]] = {k: [] for k in keys}
    for day in dates:
        for k in keys:
            results[k].append((day, evaluate(day, *k, spend_in_blackout=False)))
            alt[k].append((day, evaluate(day, *k, spend_in_blackout=True)))

    write_csv(results)
    windows = {k: [window(day, *k) for day in dates] for k in keys}
    report = render(cfg, point, files, dates, keys, results, alt, evaluate, windows)
    (OUT / f"{STEM}.md").write_text(report, encoding="utf-8", newline="\n")
    print("written to", OUT)
    return 0


def _finish(out: Outcome, range_ok: bool) -> Outcome:
    if not range_ok and out.status in ("no_break", "data_gap", "no_decision_time"):
        out.status = "range_missing" if out.eligible else out.status
    return out


def pct(xs: list[int], q: float) -> int:
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]


def share(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "–"


def table(header: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def files_digest(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.name.encode())
        h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode())
    return h.hexdigest()


def write_csv(results: dict[tuple[str, int, str], list[tuple[date, Outcome]]]) -> None:
    with (OUT / f"{STEM}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(
            [
                "date",
                "range",
                "or_bars",
                "variant",
                "status",
                "width_points",
                "spend_time_utc",
                "d_opposite_points",
                "d_midpoint_points",
                "eligible_evaluated",
                "data_gap",
                "blackout_skipped",
            ]
        )
        for (rng, n, v), rows in results.items():
            for day, o in rows:
                w.writerow(
                    [
                        day.isoformat(),
                        rng,
                        n,
                        v,
                        o.status,
                        "" if o.width is None else o.width,
                        o.spend_time.strftime("%Y-%m-%dT%H:%M:%SZ") if o.spend_time else "",
                        "" if o.d_opposite is None else o.d_opposite,
                        "" if o.d_midpoint is None else o.d_midpoint,
                        o.eligible,
                        o.data_gap,
                        o.blackout_skipped,
                    ]
                )


def render(cfg, point, files, dates, keys, results, alt, evaluate, windows) -> str:  # type: ignore[no-untyped-def]
    out = [
        "# opening_range_breakout 0.1.0 draft: feasibility evidence (2026-10-11)",
        "",
        "Generated read-only by `scripts/opening_range_breakout_feasibility.py`. **No P&L**: no fill,",
        "exit or price after the close that spends a range is read or computed, and nothing is",
        f"written to the ledger. Per-range-day rows: `{STEM}.csv`.",
        "",
        "## Inputs",
        "",
        f"- Trading dates {FIRST} … {LAST} from the market calendar (`configs/data.yaml`): {len(dates)}.",
        f"- Sealed period from `configs/backtest.yaml`: first {cfg.backtest.sealed_period.first}. Bar files were selected by",
        f"  name (dates {FIRST - timedelta(days=1)} … {LAST}) before reading; none dated in the sealed period was opened.",
        *(
            f"- Canonical {tf} bars: {len(ps)} files, {ps[0].stem} … {ps[-1].stem}; digest of (name, sha256) "
            f"`{files_digest(ps)}`."
            for tf, ps in files.items()
        ),
        f"- Symbol point {point} (pinned snapshot `{cfg.backtest.symbol_snapshot.file}`).",
        "- Session eligibility: `TradingCalendar.state_at(T)` (session name and `entries_allowed`), the",
        "  same calendar the backtests use, early closes included. News: `NewsBlackout.reason_at(T)`",
        "  on `configs/risk.yaml` `news_blackout`, the risk engine's own object.",
        f"- Draft rules: anchors 08:00 London / 09:30 New York; `or_bars` {list(OR_BARS)}; window 120 min;",
        f"  stop {BUFFER_POINTS} points beyond the opposite side; bounds {MIN_STOP_POINTS} / {CAP_POINTS} points.",
        "",
        "Statuses per range-day (draft rules, blackout closes neither signal nor spend):",
        "",
        "- `signal`: the spending close gives `500 ≤ d ≤ 2,000`; `stop_below_minimum`, `stop_above_cap`:",
        "  the spending close is outside the bounds; `no_break`: no eligible close outside the range;",
        "  `data_gap`: the range exists but a read-set bar is missing before any spend;",
        "  `range_missing`: a range bar is missing; `no_decision_time`: no eligible instant that day.",
        "",
        "## 1. Counts per range, `or_bars` and variant (whole period)",
        "",
    ]
    rows = []
    for k in keys:
        st = Counter(o.status for _, o in results[k])
        spent = st["signal"] + st["stop_below_minimum"] + st["stop_above_cap"]
        widths = [o.width for _, o in results[k] if o.width is not None]
        ds = [o.d_opposite for _, o in results[k] if o.d_opposite is not None]
        rows.append(
            [
                f"{k[0]}, {k[1]}, {k[2]}",
                len(results[k]),
                st["no_decision_time"],
                st["range_missing"],
                st["data_gap"],
                st["no_break"],
                spent,
                st["stop_below_minimum"],
                st["stop_above_cap"],
                st["signal"],
                f"{pct(widths, 0.1)} / {pct(widths, 0.5)} / {pct(widths, 0.9)}",
                f"{pct(ds, 0.1)} / {pct(ds, 0.5)} / {pct(ds, 0.9)} / {pct(ds, 0.99)}",
                share(st["stop_below_minimum"], spent),
                share(st["stop_above_cap"], spent),
                sum(o.data_gap for _, o in results[k]),
                sum(o.blackout_skipped for _, o in results[k]),
            ]
        )
    out += [
        table(
            [
                "range, or_bars, variant",
                "range-days",
                "no decision time",
                "range missing",
                "data_gap days",
                "no break",
                "spent",
                "d < 500",
                "d > 2,000",
                "signals",
                "W p10 / p50 / p90",
                "d p10 / p50 / p90 / p99",
                "share < 500",
                "share > 2,000",
                "`data_gap` count",
                "`blackout_skipped` count",
            ],
            rows,
        ),
        "",
    ]

    out += [
        "## 2. Signals and bound shares by year",
        "",
        "`signals` / `spent` (share below 500, share above 2,000):",
        "",
    ]
    years = sorted({d.year for d in dates})
    yrows = []
    for k in keys:
        cells = []
        for y in years:
            st = Counter(o.status for d, o in results[k] if d.year == y)
            spent = st["signal"] + st["stop_below_minimum"] + st["stop_above_cap"]
            cells.append(
                f"{st['signal']} / {spent} ({share(st['stop_below_minimum'], spent)}, {share(st['stop_above_cap'], spent)})"
            )
        yrows.append([f"{k[0]}, {k[1]}, {k[2]}", *cells])
    out += [table(["range, or_bars, variant", *map(str, years)], yrows), ""]
    full = [y for y in years if y not in (FIRST.year, LAST.year)]
    avg_rows = []
    for n in OR_BARS:
        for v in VARIANTS:
            per = [
                sum(1 for d, o in results[(r, n, v)] if d.year in full and o.status == "signal")
                / len(full)
                for r in RANGES
            ]
            avg_rows.append(
                [
                    f"{v}, {n}",
                    f"{per[0]:.1f}",
                    f"{per[1]:.1f}",
                    f"{sum(per):.1f}",
                    f"{sum(per) / 52:.1f}",
                ]
            )
    out += [
        f"Signals per year, averaged over the full years {full[0]}–{full[-1]}:",
        "",
        table(["variant, or_bars", "london", "newyork", "total", "per week"], avg_rows),
        "",
    ]

    out += [
        "## 3. O2: stop basis (opposite side vs range midpoint)",
        "",
        "Same spending closes; only the stop rule differs (midpoint = `H − W/2` for a long, ± 20 points).",
        "",
    ]
    o2 = []
    for k in keys:
        spent = [o for _, o in results[k] if o.d_opposite is not None]
        o2.append(
            [
                f"{k[0]}, {k[1]}, {k[2]}",
                len(spent),
                share(sum(o.d_opposite < MIN_STOP_POINTS for o in spent), len(spent)),
                share(sum(o.d_midpoint < MIN_STOP_POINTS for o in spent), len(spent)),
            ]
        )  # type: ignore[operator]
    out += [
        table(
            [
                "range, or_bars, variant",
                "spending closes",
                "d < 500, opposite side",
                "d < 500, midpoint",
            ],
            o2,
        ),
        "",
    ]

    out += [
        "## 4. O3: news-blackout treatment, on one population",
        "",
        "Population: range-days on which rule (a) met no missing range or read-set bar (status",
        "`signal`, a stop bound, or `no_break`); rule (b) reads a subset of the same bars, because it",
        "spends at or before (a)'s spending close. (a) the draft rule: a close at a blackout instant neither",
        "signals nor spends; (b) the alternative: it spends the range, and the signal is one the risk",
        "engine must reject. Only windows that contain a blackout instant can differ.",
        "",
    ]
    o3 = []
    for k in keys:
        pairs = [
            (a, b)
            for (_, a), (_, b) in zip(results[k], alt[k], strict=True)
            if a.status not in ("data_gap", "range_missing", "no_decision_time")
        ]
        a_spent = sum(a.spend_time is not None for a, _ in pairs)
        a_after = sum(a.spend_time is not None and a.blackout_skipped > 0 for a, _ in pairs)
        b_spent = sum(b.spend_time is not None for _, b in pairs)
        b_lost = sum(b.status == "spent_in_blackout" for _, b in pairs)
        o3.append(
            [
                f"{k[0]}, {k[1]}, {k[2]}",
                len(pairs),
                a_spent,
                a_after,
                share(a_after, a_spent),
                b_spent,
                b_lost,
                share(b_lost, b_spent),
            ]
        )
    out += [
        table(
            [
                "range, or_bars, variant",
                "range-days",
                "(a) spent",
                "(a) spent after a skipped close outside",
                "share",
                "(b) spent",
                "(b) spent at a blackout instant (lost)",
                "share",
            ],
            o3,
        ),
        "",
    ]

    out += [
        "## 5. Eligible decision times (calendar only, no prices)",
        "",
        "Every eligible decision time of each range-day's window (rules 6.1–6.4), whether or not",
        "the range would be spent earlier. Local times; `timeout` = last eligible time + 120 min, the",
        "latest `max_hold` deadline. DST regime: `normal` when 09:30 New York is 14:30 London,",
        "`mismatch` when it is 13:30 London.",
        "",
    ]
    wrows = []
    for k in keys:
        tz = LONDON if k[0] == "london" else NEW_YORK
        combos: Counter[tuple[str, str, str, int, int]] = Counter()
        for eligible, skipped, regime in windows[k]:
            if eligible:
                combos[
                    (
                        regime,
                        f"{eligible[0].astimezone(tz):%H:%M}",
                        f"{eligible[-1].astimezone(tz):%H:%M}",
                        len(eligible),
                        skipped,
                    )
                ] += 1
            else:
                combos[(regime, "–", "–", 0, skipped)] += 1
        for (regime, f0, l0, c, sk), days in sorted(combos.items()):
            last = next(
                (
                    e[-1]
                    for e, _, r in windows[k]
                    if e and r == regime and f"{e[-1].astimezone(tz):%H:%M}" == l0 and len(e) == c
                ),
                None,
            )
            timeout = (
                f"{(last + WINDOW).astimezone(LONDON):%H:%M} London / {(last + WINDOW).astimezone(NEW_YORK):%H:%M} New York"
                if last
                else "–"
            )
            wrows.append([f"{k[0]}, {k[1]}, {k[2]}", regime, f0, l0, c, sk, timeout, days])
    out += [
        table(
            [
                "range, or_bars, variant",
                "DST regime",
                "first (local)",
                "last (local)",
                "eligible times",
                "blackout instants skipped",
                "timeout of the last",
                "days",
            ],
            wrows,
        ),
        "",
        "Local = London for `london`, New York for `newyork`. Rows with few days are early closes.",
        "",
    ]

    out += [f"## 6. Trace, {TRACE_DAY} (spec section 17)", ""]
    for rng in RANGES:
        for v in VARIANTS:
            o = evaluate(TRACE_DAY, rng, 2, v, spend_in_blackout=False, trace=True)
            out += [
                f"**{rng}, or_bars 2, {v}:** status `{o.status}`"
                + (f", d {o.d_opposite} points" if o.d_opposite is not None else ""),
                "",
            ]
            out += [f"- {line}" for line in o.trace] + [""]
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
