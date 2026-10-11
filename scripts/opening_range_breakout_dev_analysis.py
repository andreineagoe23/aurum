"""opening_range_breakout 0.1.0: registered first-stage assessment of the 12 development trials
(spec section 16). Read-only: opens the trial ledger read-only and reads the existing run
files; runs no simulation, retry, stress, replay, baseline or sealed test, and selects no final
candidate (no frozen configuration).

    uv run --no-sync python scripts/opening_range_breakout_dev_analysis.py

Writes docs/strategies/evidence/opening_range_breakout_2026-10-11_dev_{analysis.md,points.csv,
dsr_trials.csv}.
"""

# ruff: noqa: E501, I001  (report prose; aurum is imported before polars, see below)
# The VM's polars needs aurum._platform to run first, so aurum is imported before it.
import aurum  # noqa: F401

import csv
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from aurum.adapters.storage.layout import RunLayout, ledger_path
from aurum.adapters.storage.specs import pinned_spec
from aurum.data.market_hours import MarketHours
from aurum.ledger.trial_ledger import _COLUMNS
from aurum.sessions.calendar import TradingCalendar
from aurum.settings import load_settings
from aurum.strategies.opening_range_breakout import declared_grid
from aurum.validation.walkforward import (
    assess_dsr,
    cents,
    defined_sharpe,
    max_drawdown,
    walk_forward,
)

OUT = Path("docs/strategies/evidence")
STEM = "opening_range_breakout_2026-10-11_dev"
STRATEGY = "opening_range_breakout"
VARIANTS = ("m15", "m5")
PERIOD = "2020-12-15..2025-09-30"
GRID_IDS = {v: f"{STRATEGY}/{v}/{PERIOD}" for v in VARIANTS}
TP_GRID_IDS = tuple(f"trend_pullback/{v}/{PERIOD}" for v in VARIANTS)
DEFAULT = (2, 1.5)
N_TRIALS = 66  # 54 trend_pullback + 12 opening_range_breakout research trials (O6)
MIN_SELECTION_TRADES = 100
BALANCE = 10_000.0
SEALED_FIRST = datetime(2025, 10, 1, tzinfo=UTC)
LONDON, NEW_YORK = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")
SESSIONS = ("london_morning", "ldn_ny_overlap")
RANGE_OF = {"london_morning": "london", "ldn_ny_overlap": "newyork"}


def d(s: str) -> date:
    return date.fromisoformat(s)


TRAIN = ("training", d("2020-12-15"), d("2021-09-30"))
FOLDS = (
    ("fold 1", d("2021-10-01"), d("2022-09-30")),
    ("fold 2", d("2022-10-01"), d("2023-09-30")),
    ("fold 3", d("2023-10-01"), d("2024-09-30")),
    ("fold 4", d("2024-10-01"), d("2025-09-30")),
)
OOS_FIRST, OOS_LAST = FOLDS[0][1], FOLDS[-1][2]

Point = tuple[float, ...]


def utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@dataclass
class Run:
    variant: str
    point: Point
    trial_id: str
    run_id: str
    metrics: dict[str, Any]
    trades: pl.DataFrame
    journal: pl.DataFrame


def fmt_pt(p: Point | None) -> str:
    return "none" if p is None else f"({p[0]}, {p[1]:.1f})"


def money(x: float | None) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.2f}"


def num(x: float | None, nd: int = 3) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return "inf" if math.isinf(x) else f"{x:.{nd}f}"


def table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def dst_regime(t: datetime) -> str:
    """UK season of the signal's London date, and whether 09:30 New York is 14:30 London
    (normal) or 13:30 London (mismatch week)."""
    day = t.astimezone(LONDON).date()
    ny = datetime.combine(day, time(9, 30), NEW_YORK).astimezone(LONDON).time()
    uk = "UK summer" if datetime.combine(day, time(12), LONDON).dst() else "UK winter"
    return f"{uk}, {'normal' if ny == time(14, 30) else 'mismatch'}"


# ------------------------------------------------------------------------------ load


def load() -> dict[str, Any]:
    settings = load_settings()
    cfg = settings.config
    db = ledger_path(settings.data_dir)
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    layout = RunLayout(settings.runs_dir)
    snap = pinned_spec(
        settings.data_dir, cfg.backtest.symbol_snapshot.file, cfg.backtest.symbol_snapshot.sha256
    )
    per_lot = snap.spec.money_per_price_unit_per_lot
    calendar = TradingCalendar(cfg.sessions, MarketHours(cfg.data.market))
    cache: dict[datetime, str] = {}

    def session_of(t: datetime) -> str:
        if t not in cache:
            cache[t] = calendar.state_at(t).session or "none"
        return cache[t]

    def trials(grid_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM trials WHERE json_extract(params_json, '$.grid_id') = ? ORDER BY seq",
            (grid_id,),
        ).fetchall()
        return [dict(zip(_COLUMNS.split(", "), r, strict=True)) for r in rows]

    runs: list[Run] = []
    for v in VARIANTS:
        for row in trials(GRID_IDS[v]):
            params = json.loads(row["params_json"])
            sp = params["strategy_params"]
            t = pl.read_parquet(layout.trades(params["run_id"]))
            t = t.with_columns(
                pl.col("signal_time")
                .map_elements(session_of, return_dtype=pl.String)
                .alias("session"),
                pl.col("signal_time").map_elements(dst_regime, return_dtype=pl.String).alias("dst"),
                (
                    (pl.col("entry_price") - pl.col("stop_loss")).abs() * pl.col("volume") * per_lot
                ).alias("risk_usd"),
            )
            runs.append(
                Run(v, (sp["or_bars"], sp["target_r"]), row["trial_id"], params["run_id"],
                    json.loads(row["metrics_json"]), t, pl.read_parquet(layout.journal(params["run_id"])))
            )  # fmt: skip
    tp: list[tuple[str, str, str, pl.DataFrame]] = []
    for gid in TP_GRID_IDS:
        for row in trials(gid):
            params = json.loads(row["params_json"])
            sp = params["strategy_params"]
            label = f"({sp['h1_ema']}, {sp['stop_atr_mult']}, {sp['target_r']})"
            t = pl.read_parquet(layout.trades(params["run_id"]), columns=["exit_time", "net_pnl"])
            tp.append((row["trial_id"], f"trend_pullback {row['variant']}", label, t))
    return {
        "cfg": cfg,
        "conn": conn,
        "runs": runs,
        "tp": tp,
        "market": MarketHours(cfg.data.market),
    }


# ------------------------------------------------------------------------------ metrics


def metrics(t: pl.DataFrame) -> dict[str, Any]:
    n = t.height
    if n == 0:
        return {"trades": 0}
    net = t["net_pnl"].to_list()
    pos = sum(x for x in net if x > 0)
    neg = -sum(x for x in net if x < 0)
    return {
        "trades": n,
        "net": sum(net),
        "gross": float(t["gross_pnl"].sum()),
        "net_exp": sum(net) / n,
        "gross_exp": float(t["gross_pnl"].sum()) / n,
        "net_r": float((t["net_pnl"] / t["risk_usd"]).mean()),
        "gross_r": float((t["gross_pnl"] / t["risk_usd"]).mean()),
        "pf": pos / neg if neg > 0 else float("inf"),
        "max_dd": max_drawdown(t.sort("exit_time")["net_pnl"].to_list()),
        "win_rate": sum(1 for x in net if x > 0) / n,
        "hold_mean": float(t["holding_seconds"].mean()) / 60,
        "hold_median": float(t["holding_seconds"].median()) / 60,
        "spread": float(t["spread_cost"].sum()),
        "slippage": float(t["slippage_cost"].sum()),
        "commission": float(t["commission"].sum()),
        "long": t.filter(pl.col("side") == "BUY").height,
        "short": t.filter(pl.col("side") == "SELL").height,
        "worst_case": int(t["worst_case"].sum()),
    }


def in_window(t: pl.DataFrame, first: date, last: date) -> pl.DataFrame:
    return t.filter(
        (pl.col("signal_time") >= utc(first))
        & (pl.col("signal_time") < utc(last) + timedelta(days=1))
    )


SPLIT_HEADER = ["slice", "trades", "net USD", "net USD/trade", "net R/trade", "PF"]


def split_rows(t: pl.DataFrame, key: pl.Expr, label: str = "") -> list[list[Any]]:
    out = []
    for (k,), g in sorted(
        t.with_columns(key.alias("_k")).group_by(["_k"]), key=lambda x: str(x[0][0])
    ):
        m = metrics(g)
        out.append(
            [
                f"{label}{k}",
                m["trades"],
                money(m["net"]),
                money(m["net_exp"]),
                num(m["net_r"]),
                num(m["pf"], 2),
            ]
        )
    return out


def fold_rows(t: pl.DataFrame, windows: tuple[tuple[str, date, date], ...]) -> list[list[Any]]:
    out = []
    for name, a, b in windows:
        m = metrics(in_window(t, a, b))
        out.append(
            [
                f"{name} ({a}..{b})",
                m["trades"],
                money(m.get("net")),
                money(m.get("net_exp")),
                num(m.get("net_r")),
                num(m.get("pf"), 2),
            ]
        )
    return out


def detail(t: pl.DataFrame, title: str, windows: tuple[tuple[str, date, date], ...]) -> str:
    m = metrics(t)
    if not m["trades"]:
        return f"#### {title}\n\nNo trades."
    exits = ", ".join(f"{k} {n}" for k, n in sorted(Counter(t["exit_reason"].to_list()).items()))
    head = table(
        ["measure", "value"],
        [
            ["closed trades (long / short)", f"{m['trades']} ({m['long']} / {m['short']})"],
            ["net P&L USD", money(m["net"])],
            ["gross P&L USD (mid to mid)", money(m["gross"])],
            ["net expectancy USD/trade", money(m["net_exp"])],
            ["gross expectancy USD/trade", money(m["gross_exp"])],
            ["net / gross expectancy R/trade", f"{num(m['net_r'])} / {num(m['gross_r'])}"],
            ["net profit factor", num(m["pf"], 3)],
            ["max drawdown, closed-trade, USD", money(m["max_dd"])],
            ["win rate (not a criterion)", num(m["win_rate"])],
            ["holding minutes, mean / median", f"{num(m['hold_mean'], 1)} / {num(m['hold_median'], 1)}"],
            ["cost attribution USD: spread / slippage / commission",
             f"{money(m['spread'])} / {money(m['slippage'])} / {money(m['commission'])}"],
            ["exit reasons", exits],
            ["worst-case (intra-bar) flagged trades", m["worst_case"]],
        ],
    )  # fmt: skip
    parts = [f"#### {title}", "", head, "", table(SPLIT_HEADER, fold_rows(t, windows))]
    parts += ["", table(SPLIT_HEADER, split_rows(t, pl.col("signal_time").dt.year(), "year "))]
    parts += ["", table(SPLIT_HEADER, split_rows(t, pl.col("side")))]
    parts += ["", table(SPLIT_HEADER, split_rows(t, pl.col("session"), "session "))]
    parts += ["", table(SPLIT_HEADER, split_rows(t, pl.col("dst")))]
    return "\n".join(parts)


def rejections(j: pl.DataFrame) -> Counter[str]:
    """Risk-engine rejection reasons from a run journal, numbers masked."""
    c: Counter[str] = Counter()
    for detail_ in j.filter(pl.col("event") == "risk_rejected")["detail"].to_list():
        for part in detail_.split("; "):
            c[re.sub(r"-?\d[\d,.]*", "#", part)] += 1
    return c


# ------------------------------------------------------------------------------ analysis


def main() -> int:
    ctx = load()
    runs: list[Run] = ctx["runs"]
    conn: sqlite3.Connection = ctx["conn"]
    grid = [(p["or_bars"], p["target_r"]) for p in declared_grid()]
    by = {(r.variant, r.point): r for r in runs}
    if sorted(by) != sorted((v, p) for v in VARIANTS for p in grid) or len(ctx["tp"]) != 54:
        print(
            f"expected 12 ORB and 54 trend_pullback trials, found {len(runs)} and {len(ctx['tp'])}"
        )
        return 1
    oos_days = ctx["market"].trading_dates(OOS_FIRST, OOS_LAST)
    day_set = set(oos_days)
    off_calendar: list[int] = []

    def daily(t: pl.DataFrame, count: bool = False) -> list[float]:
        x = t.filter(
            (pl.col("exit_time") >= utc(OOS_FIRST))
            & (pl.col("exit_time") < utc(OOS_LAST) + timedelta(days=1))
        )
        per: dict[date, float] = {}
        for ex, pnl in zip(x["exit_time"].to_list(), x["net_pnl"].to_list(), strict=True):
            if count and ex.date() not in day_set:
                off_calendar.append(1)
            per[ex.date()] = per.get(ex.date(), 0.0) + pnl
        return [per.get(dd, 0.0) / BALANCE for dd in oos_days]

    # DSR trial set: the 54 trend_pullback + 12 ORB research trials, same OOS market days.
    dsr_rows, trial_srs = [], {}
    for trial_id, who, label, t in ctx["tp"]:
        sr = defined_sharpe(daily(t, count=True))
        trial_srs[trial_id] = sr
        dsr_rows.append(
            {
                "trial_id": trial_id,
                "trial": who,
                "point": label,
                "daily_sr": "" if sr is None else repr(sr),
            }
        )
    for v in VARIANTS:
        for p in grid:
            r = by[(v, p)]
            sr = defined_sharpe(daily(r.trades, count=True))
            trial_srs[r.trial_id] = sr
            dsr_rows.append(
                {
                    "trial_id": r.trial_id,
                    "trial": f"{STRATEGY} {v}",
                    "point": fmt_pt(p),
                    "daily_sr": "" if sr is None else repr(sr),
                }
            )
    with (OUT / f"{STEM}_dsr_trials.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(dsr_rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(dsr_rows)
    undefined = sorted(k for k, s in trial_srs.items() if s is None)

    # 2. All 12 points, grid order.
    desc_rows, points_csv = [], []
    for v in VARIANTS:
        for p in grid:
            r = by[(v, p)]
            m, mm = metrics(r.trades), r.metrics
            rej = sum(rejections(r.journal).values())
            desc_rows.append([
                v, fmt_pt(p) + (" **default**" if p == DEFAULT else ""), m["trades"], money(m["net"]),
                money(m["net_exp"]), num(m["net_r"]), money(m["gross_exp"]), num(m["pf"], 3),
                money(m["max_dd"]), num(m["win_rate"], 3), int(mm["status_signal"]),
                int(mm["stop_below_minimum"]), int(mm["stop_above_cap"]), rej, m["worst_case"],
            ])  # fmt: skip
            points_csv.append({
                "variant": v, "or_bars": p[0], "target_r": p[1], "trial_id": r.trial_id,
                **{k: m[k] for k in ("trades", "net", "gross", "net_exp", "gross_exp", "net_r", "gross_r", "pf", "max_dd",
                                     "win_rate", "hold_mean", "spread", "slippage", "commission", "long", "short", "worst_case")},
                **{k: int(mm[k]) for k in ("status_signal", "stop_below_minimum", "stop_above_cap", "blackout_skipped", "data_gap")},
                "risk_rejected": rej,
            })  # fmt: skip
    with (OUT / f"{STEM}_points.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(points_csv[0]), lineterminator="\n")
        w.writeheader()
        for row in points_csv:
            w.writerow({k: (round(x, 6) if isinstance(x, float) else x) for k, x in row.items()})

    out = [
        "# opening_range_breakout 0.1.0: first-stage assessment of the 12 development trials (2026-10-11)",
        "",
        "Generated read-only by `scripts/opening_range_breakout_dev_analysis.py` from the existing",
        "12 runs (ledger opened read-only; no simulation). Rules: frozen spec section 16. Inventory:",
        f"`{STEM}_batch.md`. Launch and identity evidence: `{STEM}_launch.md`.",
        "",
        "**Status: provisional, for review.** This applies the registered first-stage gates (1–4,",
        "6–9, 12, 13). Gates 5, 10 and 11 are **not evaluated** (no stress re-run, no frozen",
        "configuration, no best-case replay). No final candidate is selected and no full-period",
        "figure is used to choose anything.",
        "",
        "Definitions (spec section 16, as trend_pullback):",
        "",
        "- Trade P&L = booked net P&L (all costs). Slices by **entry decision time** (`signal_time`,",
        "  UTC); session = the calendar session of that time (`london_morning` = the `london` range,",
        "  `ldn_ny_overlap` = the `newyork` range). DST = the UK season of the London date and",
        "  whether 09:30 New York is 14:30 London (normal) or 13:30 London (mismatch week).",
        "- Net expectancy = Σ net P&L ÷ closed trades. PF = Σ wins ÷ |Σ losses|. Max drawdown =",
        "  closed-trade, by exit time, cumulative from 0, USD.",
        "- R per trade = P&L ÷ the fill-to-stop risk at entry, |entry price − stop| × volume × 100",
        "  USD per price unit per lot (pinned snapshot). The spec's R is from `ref`, which the trades",
        "  table does not store; the fill is slightly worse than `ref` (section 9).",
        "",
        "## 1. Defaults (or_bars 2, target_r 1.5), full period, descriptive only",
        "",
        "2020-12-15 … 2025-09-30, one fixed point over the whole period: **not** walk-forward",
        "evidence.",
        "",
    ]
    windows = (TRAIN, *FOLDS)
    for v in VARIANTS:
        out += [detail(by[(v, DEFAULT)].trades, f"{v} default {fmt_pt(DEFAULT)}", windows), ""]
    out += [
        "## 2. All 12 points, full period (descriptive only)",
        "",
        "Grid order, not ranked; not a selection basis. `signal` / `stop<min` / `stop>cap` are the",
        "strategy's range-date statuses (before the risk engine); `risk rej` = risk-engine",
        "rejections in the run journal; `worst` = trades decided by a worst-case intra-bar",
        f"assumption. Machine-readable: `{STEM}_points.csv`.",
        "",
        table(
            [
                "variant",
                "point (or_bars, target_r)",
                "trades",
                "net USD",
                "net USD/trade",
                "net R/trade",
                "gross USD/trade",
                "PF",
                "max DD USD",
                "win rate",
                "signal",
                "stop<min",
                "stop>cap",
                "risk rej",
                "worst",
            ],
            desc_rows,
        ),
        "",
        "Risk-engine rejections by reason (numbers masked), summed over each variant's 6 runs:",
        "",
    ]
    for v in VARIANTS:
        c: Counter[str] = Counter()
        for p in grid:
            c += rejections(by[(v, p)].journal)
        out.append(
            f"- {v}: "
            + (
                "; ".join(
                    f"{n} × `{k}`" for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
                )
                or "none"
            )
        )
    out += [
        "",
        "## 3. Registered anchored walk-forward (OOS evidence)",
        "",
        "Fold k's point has the highest net expectancy over **all development trades entered before",
        f"fold k**, among points with ≥ {MIN_SELECTION_TRADES} such trades (`walkforward.walk_forward`).",
        "Ties are exact (integer cents): the default wins, then the lexicographically smallest",
        "(or_bars, target_r). No eligible point → the fold has no trades (no fallback). The fold's",
        "OOS trades are the selected point's trades entered in it, from its own run (no rescaling).",
        "",
    ]
    sealed = sealed_facts(conn)
    verdicts = {}
    for v in VARIANTS:
        entries = {
            p: [
                (t, cents(x))
                for t, x in zip(
                    by[(v, p)].trades["signal_time"].to_list(),
                    by[(v, p)].trades["net_pnl"].to_list(),
                    strict=True,
                )
            ]
            for p in grid
        }
        folds = [(utc(a), utc(b) + timedelta(days=1)) for _, a, b in FOLDS]
        sel_rows, parts = [], []
        for (name, a, b), (s, ftrades) in zip(
            FOLDS,
            walk_forward(entries, folds, default=DEFAULT, min_trades=MIN_SELECTION_TRADES),
            strict=True,
        ):
            fold_t = (
                in_window(by[(v, s.point)].trades, a, b)
                if s.point
                else by[(v, DEFAULT)].trades.head(0)
            )
            assert fold_t.height == len(ftrades)
            parts.append(fold_t)
            m = metrics(fold_t)
            sel_rows.append([
                name, fmt_pt(s.point), s.eligible, s.trades,
                money(s.net_cents / 100 / s.trades) if s.trades else "n/a",
                ", ".join(fmt_pt(p) for p in s.tied) if len(s.tied) > 1 else "none",
                m["trades"], money(m.get("net")), money(m.get("net_exp")),
            ])  # fmt: skip
        oos = pl.concat(parts)
        out += [
            f"### {v}",
            "",
            table(
                [
                    "fold",
                    "selected point",
                    "eligible points (≥100 prior)",
                    "prior trades",
                    "prior net USD/trade",
                    "exact ties",
                    "OOS trades",
                    "OOS net USD",
                    "OOS net USD/trade",
                ],
                sel_rows,
            ),
            "",
            detail(oos, f"{v} concatenated OOS (folds 1–4)", FOLDS),
            "",
            "Monthly (entry month, UTC):",
            "",
            table(SPLIT_HEADER, split_rows(oos, pl.col("signal_time").dt.strftime("%Y-%m"))),
            "",
        ]
        verdicts[v] = gates(v, oos, daily(oos), trial_srs, sealed)
        out += [verdicts[v]["text"], ""]
    out += [
        "## 4. Summary",
        "",
        table(
            ["gate", *VARIANTS],
            [
                [g, *(verdicts[v]["gates"][g] for v in VARIANTS)]
                for g in verdicts[VARIANTS[0]]["gates"]
            ],
        ),
        "",
        *(f"- **{v}:** {verdicts[v]['overall']}" for v in VARIANTS),
        "",
        "- Not evaluated: gate 5 (spread ×1.5 re-run), gate 10 (neighbourhood of the frozen",
        "  configuration, which is not selected) and gate 11 (best-case intra-bar replay).",
        "- The null baseline (`reference_fixed_time`) is cited, not compared: a like-for-like rerun",
        "  over 2020-12-15 … 2025-09-30 is not authorised, and its trades were not read.",
        f"- Trial count: 12 for this family, {N_TRIALS} cumulative (N for the DSR).",
        f"- Trades exiting on a date outside the market calendar in folds 1–4 (the 66 DSR trial series): {len(off_calendar)}.",
        "",
        "## 5. DSR trial set (gate 12 inputs)",
        "",
        f"- N = {N_TRIALS}: the 54 trend_pullback 0.2.0 + A1 trials (batches `{TP_GRID_IDS[0]}`, `{TP_GRID_IDS[1]}`)",
        f"  and the 12 {STRATEGY} trials. `reference_fixed_time` trials are excluded (not research trials).",
        f"- Each trial's daily SR over the same {len(oos_days)} market days of folds 1–4 (`{oos_days[0]}` … `{oos_days[-1]}`), from its own run.",
        f"- Defined SRs (the sample V is computed over): {N_TRIALS - len(undefined)}. Undefined (kept in N, left out of V): {len(undefined)}"
        + (f": {', '.join(f'`{u}`' for u in undefined)}" if undefined else "")
        + ".",
        f"- Per-trial values: `{STEM}_dsr_trials.csv`.",
        "",
        "## 6. Sealed period (gate 13)",
        "",
        f"- {STRATEGY} trials: {sealed['orb_trials']}, latest `data_end` {sealed['orb_data_end']} (London midnight of",
        f"  2025-10-01). {STRATEGY} trials whose `data_end` is after {SEALED_FIRST.date()} 00:00 UTC: {sealed['orb_touching']}.",
        f"- Sealed openings: {sealed['openings']} ({sealed['orb_openings']} by {STRATEGY}). Parity accesses: {sealed['parity']} ({sealed['orb_parity']} by {STRATEGY}).",
        f"- Historical exceptions to the literal ledger-wide condition (listed apart, not a candidate failure; no row deleted or rewritten): {len(sealed['touching'])}"
        " `reference_fixed_time` trials recorded 2026-10-09/10, before the seal existed:",
        "",
        table(
            ["trial", "strategy", "data_start", "data_end"],
            [[f"`{t[0]}`", t[1], t[2][:10], t[3][:10]] for t in sealed["touching"]],
        ),
        "",
    ]
    (OUT / f"{STEM}_analysis.md").write_text("\n".join(out), encoding="utf-8", newline="\n")
    print("written to", OUT, {v: verdicts[v]["overall"] for v in VARIANTS})
    return 0


def sealed_facts(conn: sqlite3.Connection) -> dict[str, Any]:
    first = SEALED_FIRST.isoformat()
    orb = conn.execute(
        "SELECT COUNT(*), MAX(data_end), SUM(data_end > ?) FROM trials WHERE strategy = ?",
        (first, STRATEGY),
    ).fetchone()
    return {
        "orb_trials": orb[0], "orb_data_end": orb[1], "orb_touching": orb[2],
        "touching": conn.execute("SELECT trial_id, strategy, data_start, data_end FROM trials WHERE data_end > ? ORDER BY seq", (first,)).fetchall(),
        "openings": conn.execute("SELECT COUNT(*) FROM sealed_openings").fetchone()[0],
        "orb_openings": conn.execute("SELECT COUNT(*) FROM sealed_openings WHERE strategy = ?", (STRATEGY,)).fetchone()[0],
        "parity": conn.execute("SELECT COUNT(*) FROM parity_accesses").fetchone()[0],
        "orb_parity": conn.execute("SELECT COUNT(*) FROM parity_accesses WHERE strategy = ?", (STRATEGY,)).fetchone()[0],
    }  # fmt: skip


def gates(
    v: str,
    oos: pl.DataFrame,
    rets: list[float],
    trial_srs: dict[str, float | None],
    sealed: dict[str, Any],
) -> dict[str, Any]:
    g: dict[str, str] = {}
    fold_ms = [metrics(in_window(oos, a, b)) for _, a, b in FOLDS]
    m = metrics(oos)
    n = m["trades"]
    g["1 sample (≥30 per fold, ≥200)"] = (
        f"{'pass' if all(f['trades'] >= 30 for f in fold_ms) and n >= 200 else 'FAIL'}: {[f['trades'] for f in fold_ms]}, total {n}"
    )
    if n:
        g["2 positive after costs"] = (
            f"{'pass' if m['net_exp'] > 0 and m['net'] > 0 else 'FAIL'}: {money(m['net_exp'])} USD/trade, {money(m['net'])} USD"
        )
        pos = sum(1 for f in fold_ms if f["trades"] and f["net_exp"] > 0)
        g["3 ≥3 of 4 folds positive"] = f"{'pass' if pos >= 3 else 'FAIL'}: {pos} of 4"
        g["4 PF ≥ 1.10"] = f"{'pass' if m['pf'] >= 1.10 else 'FAIL'}: {num(m['pf'], 3)}"
    else:
        for k in ("2 positive after costs", "3 ≥3 of 4 folds positive", "4 PF ≥ 1.10"):
            g[k] = "FAIL: no OOS trades"
    g["5 spread ×1.5 re-run"] = "not evaluated (stress re-runs not authorised)"
    if n:
        g["6 max DD ≤ 1,000 USD"] = (
            f"{'pass' if m['max_dd'] <= 1000 else 'FAIL'}: {money(m['max_dd'])}"
        )
        per_month = (oos.with_columns(pl.col("signal_time").dt.strftime("%Y-%m").alias("_m")).group_by("_m")
                     .agg(pl.col("net_pnl").sum()).sort(["net_pnl", "_m"], descending=[True, False]))  # fmt: skip
        best_month, best_net = per_month.row(0)
        rest = m["net"] - best_net
        g["7 no single month"] = (
            f"{'pass' if rest > 0 else 'FAIL'}: without {best_month} ({money(best_net)}) = {money(rest)}"
        )
    else:
        g["6 max DD ≤ 1,000 USD"] = g["7 no single month"] = "FAIL: no OOS trades"
    sides = {s: metrics(oos.filter(pl.col("side") == s)) for s in ("BUY", "SELL")}
    ok8 = all(x["trades"] >= 30 and x.get("net_exp", -1) >= 0 for x in sides.values())
    g["8 not one direction"] = f"{'pass' if ok8 else 'FAIL'}: " + ", ".join(
        f"{s} {x['trades']} @ {money(x.get('net_exp'))}" for s, x in sides.items()
    )
    sess = {s: metrics(oos.filter(pl.col("session") == s)) for s in SESSIONS}
    ok9 = all(x["trades"] >= 30 and x.get("net_exp", -1) >= 0 for x in sess.values())
    g["9 not one session"] = f"{'pass' if ok9 else 'FAIL'}: " + ", ".join(
        f"{s} ({RANGE_OF[s]}) {x['trades']} @ {money(x.get('net_exp'))}" for s, x in sess.items()
    )
    g["10 neighbourhood"] = "not evaluated (frozen configuration not selected)"
    flagged = int(oos["worst_case"].sum()) if n else 0
    g["11 intra-bar (Phase 3b)"] = (
        f"not evaluated (best-case replay not authorised); worst-case flagged {flagged} of {n}"
        + (f" = {flagged / n:.2%}" if n else "")
    )
    dsr = assess_dsr(rets, trial_srs, n=N_TRIALS)
    res = dsr.result
    g["12 DSR ≥ 0.95"] = (
        f"{'pass' if dsr.passed else 'FAIL'}: "
        + (f"{res.dsr:.4f}" if res else "undefined")
        + ("" if res else f" ({dsr.reason})")
    )
    g["13 sealed untouched"] = (
        f"{'pass' if not (sealed['orb_touching'] or sealed['orb_openings'] or sealed['orb_parity']) else 'FAIL'}: "
        f"{sealed['orb_touching']} {STRATEGY} runs read 2025-10-01 or later; sealed openings {sealed['orb_openings']}, "
        f"parity accesses {sealed['orb_parity']} by {STRATEGY}; {len(sealed['touching'])} historical reference_fixed_time exceptions listed apart (section 6)"
    )
    failed = [k.split(" ")[0] for k, x in g.items() if x.startswith("FAIL")]
    overall = (
        f"fails registered gate(s) {', '.join(failed)}; under section 16 this variant is rejected and evaluation stops (gates 5, 10, 11 are not run)."
        if failed
        else "meets every first-stage gate; it cannot pass until gates 5, 10 and 11 are evaluated, each only once authorised."
    )
    inputs = (
        f"DSR inputs: SR {res.sr:.5f} per day, T {res.t} market days, skew {res.skew:.4f}, kurtosis {res.kurtosis:.4f}, "
        f"V {res.variance:.3e} (sample variance, ddof 1, of {dsr.v_sample} defined trial SRs), N {dsr.n}, SR0 {res.sr0:.5f}; "
        f"undefined trial SRs {len(dsr.undefined)}."
        if res
        else f"DSR undefined: {dsr.reason}. N {dsr.n}, V sample {dsr.v_sample}, undefined trial SRs {len(dsr.undefined)}."
    )
    text = "\n".join(
        [
            f"#### {v}: registered thresholds on the concatenated OOS trades",
            "",
            table(["gate", "result"], [[k, x] for k, x in g.items()]),
            "",
            inputs,
        ]
    )
    return {"gates": g, "text": text, "overall": overall}


if __name__ == "__main__":
    sys.exit(main())
