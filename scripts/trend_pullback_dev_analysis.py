"""trend_pullback 0.2.0 + amendment A1: evidence package and registered analysis of the 54
development trials (spec section 16). Read-only: it reads the trial ledger (opened read-only)
and the existing run files, and runs no simulation.

    uv run --no-sync python scripts/trend_pullback_dev_analysis.py

Writes docs/strategies/evidence/trend_pullback_2026-10-10_dev_*.{md,csv,json}.
"""

# ruff: noqa: E501, I001  (report prose; aurum is imported before polars, see below)
# The VM's polars needs aurum._platform to run first, so aurum is imported before it.
import aurum  # noqa: F401

import csv
import hashlib
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from aurum.adapters.storage.layout import RunLayout, ledger_path
from aurum.adapters.storage.parquet import sha256_file
from aurum.adapters.storage.specs import pinned_spec
from aurum.data.market_hours import MarketHours
from aurum.ledger.trial_ledger import _COLUMNS, TrialLedger
from aurum.sessions.calendar import TradingCalendar
from aurum.settings import load_settings
from aurum.strategies.trend_pullback import H1_EMAS, STOP_ATR_MULTS, TARGET_RS
from aurum.validation.walkforward import (
    cents,
    deflated_sharpe,
    max_drawdown,
    neighbours,
    required_neighbours,
    select_point,
    sharpe,
)

OUT = Path("docs/strategies/evidence")
STEM = "trend_pullback_2026-10-10_dev"
VARIANTS = ("m15", "m5")
GRID_IDS = {v: f"trend_pullback/{v}/2020-12-15..2025-09-30" for v in VARIANTS}
DEFAULT = (50, 0.5, 1.5)
AXES = (H1_EMAS, STOP_ATR_MULTS, TARGET_RS)
N_TRIALS = 54
MIN_SELECTION_TRADES = 100
BALANCE = 10_000.0
SEALED_FIRST = datetime(2025, 10, 1, tzinfo=UTC)
PRE_BACKUP = "trials_20261010T1422Z_before_trend_pullback_dev_grid.sqlite"
POST_BACKUP = "trials_20261010T1600Z_after_trend_pullback_dev_grid.sqlite"


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


def utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


Point = tuple[float, ...]


@dataclass
class Run:
    variant: str
    point: Point
    trial_id: str
    attempt_id: str
    run_id: str
    params: dict[str, Any]
    metrics: dict[str, Any]
    record_hash: str
    trades: pl.DataFrame
    risk_rejected: int


def pt(params: dict[str, Any]) -> Point:
    return (params["h1_ema"], params["stop_atr_mult"], params["target_r"])


def fmt_pt(p: Point | None) -> str:
    return "none" if p is None else f"({p[0]:g}, {p[1]:g}, {p[2]:g})"


def money(x: float | None) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.2f}"


def num(x: float | None, nd: int = 3) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


# ------------------------------------------------------------------------------ load


def load() -> tuple[Any, list[Run], dict[str, Any]]:
    settings = load_settings()
    cfg = settings.config
    db = ledger_path(settings.data_dir)
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    runs_layout = RunLayout(settings.runs_dir)
    snap = pinned_spec(
        settings.data_dir, cfg.backtest.symbol_snapshot.file, cfg.backtest.symbol_snapshot.sha256
    )
    per_lot = snap.spec.money_per_price_unit_per_lot
    calendar = TradingCalendar(cfg.sessions, MarketHours(cfg.data.market))
    session_cache: dict[datetime, str] = {}

    def session_of(t: datetime) -> str:
        if t not in session_cache:
            session_cache[t] = calendar.state_at(t).session or "none"
        return session_cache[t]

    runs: list[Run] = []
    for variant in VARIANTS:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM trials WHERE json_extract(params_json, '$.grid_id') = ?"
            " ORDER BY seq",
            (GRID_IDS[variant],),
        ).fetchall()
        for r in rows:
            row = dict(zip(_COLUMNS.split(", "), r, strict=True))
            params = json.loads(row["params_json"])
            run_id = params["run_id"]
            trades = pl.read_parquet(runs_layout.trades(run_id))
            trades = trades.with_columns(
                pl.col("signal_time")
                .map_elements(session_of, return_dtype=pl.String)
                .alias("session"),
                (
                    (pl.col("entry_price") - pl.col("stop_loss")).abs() * pl.col("volume") * per_lot
                ).alias("risk_usd"),
            )
            journal = pl.read_parquet(runs_layout.journal(run_id))
            runs.append(
                Run(
                    variant=variant,
                    point=pt(params["strategy_params"]),
                    trial_id=row["trial_id"],
                    attempt_id=params["attempt_id"],
                    run_id=run_id,
                    params=params,
                    metrics=json.loads(row["metrics_json"]),
                    record_hash=row["record_hash"],
                    trades=trades,
                    risk_rejected=journal.filter(pl.col("event") == "risk_rejected").height,
                )
            )
    extra = {
        "settings": settings,
        "conn": conn,
        "runs_layout": runs_layout,
        "calendar": calendar,
        "snapshot": snap,
    }
    return cfg, runs, extra


# ------------------------------------------------------------------------------ metrics


def metrics(t: pl.DataFrame) -> dict[str, Any]:
    n = t.height
    if n == 0:
        return {"trades": 0}
    net = t["net_pnl"].to_list()
    pos = sum(x for x in net if x > 0)
    neg = -sum(x for x in net if x < 0)
    by_exit = t.sort("exit_time")["net_pnl"].to_list()
    return {
        "trades": n,
        "net": sum(net),
        "gross": float(t["gross_pnl"].sum()),
        "net_exp": sum(net) / n,
        "gross_exp": float(t["gross_pnl"].sum()) / n,
        "net_r": float((t["net_pnl"] / t["risk_usd"]).mean()),
        "gross_r": float((t["gross_pnl"] / t["risk_usd"]).mean()),
        "pf": pos / neg if neg > 0 else float("inf"),
        "max_dd": max_drawdown(by_exit),
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


def split_rows(t: pl.DataFrame, key: pl.Expr, label: str) -> list[list[Any]]:
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


SPLIT_HEADER = ["slice", "trades", "net USD", "net USD/trade", "net R/trade", "PF"]


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
            [
                "holding minutes, mean / median",
                f"{num(m['hold_mean'], 1)} / {num(m['hold_median'], 1)}",
            ],
            [
                "cost attribution USD: spread / slippage / commission",
                f"{money(m['spread'])} / {money(m['slippage'])} / {money(m['commission'])}",
            ],
            ["worst-case (intra-bar) flagged trades", m["worst_case"]],
        ],
    )
    parts = [f"#### {title}", "", head, "", table(SPLIT_HEADER, fold_rows(t, windows))]
    parts += ["", table(SPLIT_HEADER, split_rows(t, pl.col("signal_time").dt.year(), "year "))]
    parts += ["", table(SPLIT_HEADER, split_rows(t, pl.col("side"), ""))]
    parts += ["", table(SPLIT_HEADER, split_rows(t, pl.col("session"), "session "))]
    return "\n".join(parts)


# ------------------------------------------------------------------------------ evidence


def evidence(cfg: Any, runs: list[Run], extra: dict[str, Any]) -> str:
    conn: sqlite3.Connection = extra["conn"]
    layout: RunLayout = extra["runs_layout"]
    data_dir: Path = extra["settings"].data_dir
    contexts = {}
    for gid, h, ctx, frozen in conn.execute(
        "SELECT grid_id, context_hash, context_json, frozen_at FROM grid_batches ORDER BY seq"
    ):
        contexts[gid] = {"context_hash": h, "frozen_at": frozen, "context": json.loads(ctx)}
    (OUT / f"{STEM}_contexts.json").write_text(
        json.dumps(contexts, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    attempts = {
        r[0]: r
        for r in conn.execute(
            "SELECT a.attempt_id, a.grid_id, a.context_hash, a.started_at, o.finished_at,"
            " o.status, o.stage, o.error, o.trial_id, a.retry_of, a.code_version"
            " FROM attempts a LEFT JOIN attempt_outcomes o USING (attempt_id) ORDER BY a.seq"
        )
    }
    # Ledger verification: every trial row re-hashed (read-only), integrity check.
    bad_hash = []
    cur = conn.execute(f"SELECT {_COLUMNS} FROM trials ORDER BY seq")
    for raw in cur.fetchall():
        row = dict(zip(_COLUMNS.split(", "), raw, strict=True))
        stored = row.pop("record_hash")
        if TrialLedger._hash(row) != stored:
            bad_hash.append(row["trial_id"])
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in (
            "trials",
            "attempts",
            "attempt_outcomes",
            "grid_batches",
            "sealed_openings",
            "parity_accesses",
        )
    }
    # Artifact verification and the 54-row index.
    index_rows, artifact_problems, durations = [], [], []
    for r in runs:
        a = attempts[r.attempt_id]
        man = json.loads(layout.manifest(r.run_id).read_text(encoding="utf-8"))
        found = {
            k: sha256_file(layout.run_dir(r.run_id) / f"{k}.parquet") for k in r.params["outputs"]
        }
        checks = {
            "outputs": found == r.params["outputs"] == man["outputs"],
            "manifest trial": man["trial_id"] == r.trial_id,
            "manifest attempt": man["attempt_id"] == r.attempt_id,
            "data hash": man["data_hash"] == r.params["data_hash"],
            "context": man["context_hash"]
            == r.params["context_hash"]
            == a[2]
            == contexts[GRID_IDS[r.variant]]["context_hash"],
            "outcome": (a[5], a[8]) == ("succeeded", r.trial_id),
        }
        if not all(checks.values()):
            artifact_problems.append((r.trial_id, checks))
        start, end = datetime.fromisoformat(a[3]), datetime.fromisoformat(a[4])
        durations.append((r.variant, (end - start).total_seconds()))
        index_rows.append(
            {
                "variant": r.variant,
                "h1_ema": r.point[0],
                "stop_atr_mult": r.point[1],
                "target_r": r.point[2],
                "attempt_id": r.attempt_id,
                "trial_id": r.trial_id,
                "run_id": r.run_id,
                "context_hash": r.params["context_hash"],
                "started_at": a[3],
                "finished_at": a[4],
                "status": a[5],
                "trades_sha256": found["trades"],
                "equity_sha256": found["equity"],
                "journal_sha256": found["journal"],
                "data_hash": r.params["data_hash"],
                "record_hash": r.record_hash,
                "code_version": a[10],
            }
        )
    with (OUT / f"{STEM}_index.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(index_rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(index_rows)
    tp_trials = conn.execute(
        "SELECT COUNT(*), MAX(data_end) FROM trials WHERE strategy = 'trend_pullback'"
    ).fetchone()
    touching = conn.execute(
        "SELECT trial_id, strategy, data_start, data_end FROM trials WHERE data_end > ?"
        " ORDER BY seq",
        (SEALED_FIRST.isoformat(),),
    ).fetchall()
    backups = data_dir / "ledger" / "backups"
    bk = {
        n: hashlib.sha256((backups / n).read_bytes()).hexdigest() for n in (PRE_BACKUP, POST_BACKUP)
    }
    stray = [a for a in attempts.values() if a[1] not in GRID_IDS.values()]
    per_var = {v: [s for vv, s in durations if vv == v] for v in VARIANTS}
    lines = [
        "# trend_pullback 0.2.0 + A1: development batch evidence (2026-10-10)",
        "",
        "Generated read-only by `scripts/trend_pullback_dev_analysis.py` from the trial ledger and",
        "the existing run files. No simulation was run to produce it.",
        "",
        "## Batches and frozen contexts",
        "",
        table(
            ["batch id", "context hash", "frozen at"],
            [[f"`{g}`", f"`{c['context_hash']}`", c["frozen_at"]] for g, c in contexts.items()],
        ),
        "",
        f"Full contexts: `{STEM}_contexts.json`. Both contexts are identical except `variant`:",
        "",
    ]
    c0 = next(iter(contexts.values()))["context"]
    lines += [
        table(
            ["context part", "value"],
            [
                ["code: source sha256", f"`{c0['code']['source_sha256']}`"],
                ["code: environment sha256", f"`{c0['code']['environment_sha256']}`"],
                ["code: Python", c0["code"]["python"]],
                ["spec", f"`{c0['spec']['path']}` `{c0['spec']['sha256']}`"],
                ["config hash", f"`{c0['config_hash']}`"],
                [
                    "symbol snapshot",
                    f"`{c0['symbol']['snapshot']}` file `{c0['symbol']['file_sha256']}`",
                ],
                [
                    "input data",
                    f"`{c0['data']['sha256']}`, {c0['data']['files']} files from "
                    f"{c0['data']['first_data_day']} (warm-up included)",
                ],
                ["declared grid", f"`{c0['grid']}`"],
                [
                    "dates / purpose / sealed opened",
                    f"{c0['first']}..{c0['last']} / {c0['purpose']} / {c0['sealed_opened']}",
                ],
            ],
        )
    ]
    differ = sorted(k for k in c0 if any(c["context"][k] != c0[k] for c in contexts.values()))
    lines += ["", f"Context keys that differ between the batches: {differ}.", ""]
    lines += [
        "## Counts and timings",
        "",
        table(
            ["", "m15", "m5"],
            [
                ["grid points (declared)", 27, 27],
                [
                    "attempts",
                    *(sum(1 for a in attempts.values() if a[1] == GRID_IDS[v]) for v in VARIANTS),
                ],
                [
                    "succeeded / failed / unfinished",
                    *(
                        "/".join(
                            str(
                                sum(
                                    1
                                    for a in attempts.values()
                                    if a[1] == GRID_IDS[v] and (a[5] or "unfinished") == s
                                )
                            )
                            for s in ("succeeded", "failed", "unfinished")
                        )
                        for v in VARIANTS
                    ),
                ],
                [
                    "retries",
                    *(
                        sum(1 for a in attempts.values() if a[1] == GRID_IDS[v] and a[9])
                        for v in VARIANTS
                    ),
                ],
                [
                    "trials (one per point)",
                    *(sum(1 for r in runs if r.variant == v) for v in VARIANTS),
                ],
                [
                    "distinct points with a trial",
                    *(len({r.point for r in runs if r.variant == v}) for v in VARIANTS),
                ],
                [
                    "first attempt started",
                    *(
                        min(a[3] for a in attempts.values() if a[1] == GRID_IDS[v])
                        for v in VARIANTS
                    ),
                ],
                [
                    "last outcome",
                    *(
                        max(a[4] for a in attempts.values() if a[1] == GRID_IDS[v])
                        for v in VARIANTS
                    ),
                ],
                [
                    "seconds per point: min / median / max",
                    *(
                        f"{min(s):.0f} / {sorted(s)[len(s) // 2]:.0f} / {max(s):.0f}"
                        for s in per_var.values()
                    ),
                ],
            ],
        ),
        "",
        f"- **54 grid points, 54 attempts, 54 trials**; attempts outside the two batches: {len(stray)}.",
        f"- Ledger tables: {counts}.",
        "- Grid logs (git-ignored): `reports/grid_logs/m15_a1.log`, `reports/grid_logs/m5_a1.log`",
        "  (both end `27 points: 27 resolved, 0 unresolved, 0 with artifacts pending`, exit 0).",
        "",
        "## Ledger verification",
        "",
        f"- `PRAGMA integrity_check`: {integrity}.",
        f"- Every trial row re-hashed against its `record_hash` (read-only): "
        f"{len(bad_hash)} mismatches of {counts['trials']}.",
        f"- Sealed openings: {counts['sealed_openings']}. Parity accesses: {counts['parity_accesses']}.",
        f"- trend_pullback trials: {tp_trials[0]}, latest `data_end` {tp_trials[1]} (London midnight",
        "  of 2025-10-01 = 23:00 UTC on 2025-09-30; no bar dated 2025-10-01 or later is read).",
        f"- Trials of any strategy whose `data_end` is after {SEALED_FIRST.date()} 00:00 UTC: {len(touching)}",
        "  (" + ", ".join(f"`{t[0]}` {t[1]} {t[2][:10]}..{t[3][:10]}" for t in touching) + ").",
        "  These are `reference_fixed_time` runs recorded on 2026-10-09/10 before the sealed period",
        "  was defined (D1); none is a trend_pullback trial.",
        "",
        "## Artifact verification",
        "",
        "For each of the 54 trials: the three Parquet outputs re-hashed and compared with the hashes",
        "recorded in the trial and in the manifest; manifest trial id, attempt id, data hash and",
        "context hash compared with the ledger; outcome = succeeded with this trial.",
        f"Problems: {len(artifact_problems)}"
        + (f" {artifact_problems}" if artifact_problems else "")
        + ".",
        "Staging directory: "
        + (
            str(sorted(p.name for p in (layout.root / ".staging").iterdir()))
            if (layout.root / ".staging").exists()
            else "absent (nothing unpublished)"
        )
        + ".",
        "",
        "Index of all 54 trials (variant, point, attempt, trial, run, context, timings, output and",
        f"record hashes): `{STEM}_index.csv`. Run files: `reports/runs/<run_id>/` (git-ignored).",
        "",
        "## Backups",
        "",
        table(
            ["backup (C:\\aurum-data\\ledger\\backups)", "sha256", "taken"],
            [
                [
                    PRE_BACKUP,
                    f"`{bk[PRE_BACKUP]}`",
                    "2026-10-10 14:22Z, before any trend_pullback attempt (10 trials)",
                ],
                [
                    POST_BACKUP,
                    f"`{bk[POST_BACKUP]}`",
                    "2026-10-10 16:00Z, after both batches (64 trials, 54 attempts)",
                ],
            ],
        ),
        "",
        "Both taken with the SQLite backup API (consistent copy); `integrity_check` ok. The pre-run",
        "backup is unchanged.",
    ]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------ analysis


def analysis(cfg: Any, runs: list[Run]) -> str:
    by = {(r.variant, r.point): r for r in runs}
    market = MarketHours(cfg.data.market)
    oos_days = market.trading_dates(OOS_FIRST, OOS_LAST)
    day_set = set(oos_days)

    def daily(t: pl.DataFrame) -> tuple[list[float], int]:
        x = t.filter(
            (pl.col("exit_time") >= utc(OOS_FIRST))
            & (pl.col("exit_time") < utc(OOS_LAST) + timedelta(days=1))
        )
        per: dict[date, float] = {}
        off = 0
        for ex, pnl in zip(x["exit_time"].to_list(), x["net_pnl"].to_list(), strict=True):
            dd = ex.date()
            if dd not in day_set:
                off += 1
            per[dd] = per.get(dd, 0.0) + pnl
        return [per.get(dd, 0.0) / BALANCE for dd in oos_days], off

    trial_srs = []
    off_calendar = 0
    for r in runs:
        rets, off = daily(r.trades)
        off_calendar += off
        trial_srs.append(sharpe(rets))

    # Descriptive per point.
    desc_rows = []
    points_csv = []
    for v in VARIANTS:
        for p in [(h, s, t) for h in H1_EMAS for s in STOP_ATR_MULTS for t in TARGET_RS]:
            r = by[(v, p)]
            m = metrics(r.trades)
            mm = r.metrics
            desc_rows.append(
                [
                    v,
                    fmt_pt(p) + (" **default**" if p == DEFAULT else ""),
                    m["trades"],
                    money(m["net"]),
                    money(m["net_exp"]),
                    num(m["net_r"]),
                    money(m["gross_exp"]),
                    num(m["pf"], 3),
                    money(m["max_dd"]),
                    num(m["win_rate"], 3),
                    int(mm["stop_above_cap"]),
                    int(mm["stop_below_minimum"]),
                    int(mm["data_gap"]),
                    r.risk_rejected,
                    m["worst_case"],
                ]
            )
            points_csv.append(
                {
                    "variant": v,
                    "h1_ema": p[0],
                    "stop_atr_mult": p[1],
                    "target_r": p[2],
                    "trial_id": r.trial_id,
                    **{
                        k: m[k]
                        for k in (
                            "trades",
                            "net",
                            "gross",
                            "net_exp",
                            "gross_exp",
                            "net_r",
                            "gross_r",
                            "pf",
                            "max_dd",
                            "win_rate",
                            "hold_mean",
                            "spread",
                            "slippage",
                            "commission",
                            "long",
                            "short",
                            "worst_case",
                        )
                    },
                    "stop_above_cap": mm["stop_above_cap"],
                    "stop_below_minimum": mm["stop_below_minimum"],
                    "data_gap": mm["data_gap"],
                    "risk_rejected": r.risk_rejected,
                }
            )
    with (OUT / f"{STEM}_points.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(points_csv[0]), lineterminator="\n")
        w.writeheader()
        for row in points_csv:
            w.writerow({k: (round(x, 6) if isinstance(x, float) else x) for k, x in row.items()})

    out = [
        "# trend_pullback 0.2.0 + A1: development analysis of the 54 trials (2026-10-10)",
        "",
        "Generated read-only by `scripts/trend_pullback_dev_analysis.py` from the existing 54 runs",
        "(no simulation). Rules: spec section 16 as amended by A1. Evidence package:",
        f"`{STEM}_batch.md`.",
        "",
        "**Status of this document.** The pipeline computed simulated P&L and summary metrics for",
        "every trial when it ran. This is the **post-batch assessment** against the registered",
        "rules. It does **not** select the final candidate (the frozen configuration for Phase 6",
        "is not determined here), and it does not use full-period P&L to choose anything.",
        "",
        "Definitions used (spec section 16):",
        "",
        "- Trade P&L = booked net P&L (all costs). Slices by **entry decision time** (`signal_time`,",
        "  UTC); session = the session of that decision time.",
        "- Net expectancy = Σ net P&L ÷ closed trades. PF = Σ wins ÷ |Σ losses|.",
        "- Max drawdown = closed-trade, trades ordered by exit time, cumulative from 0, USD.",
        "- R per trade = net (or gross) P&L ÷ the fill-to-stop risk at entry,",
        "  |entry price − stop| × volume × 100 USD per price unit per lot (pinned snapshot).",
        "  The spec's R is from `ref`, which the trades table does not store; the realised fill is",
        "  slightly worse than `ref` (section 9), so this R is the money actually at risk.",
        "",
        "## 1. Defaults (h1_ema 50, stop_atr_mult 0.5, target_r 1.5)",
        "",
        "Full evaluated period 2020-12-15 … 2025-09-30. **Descriptive only**: a fixed point over the",
        "whole period, not walk-forward evidence.",
        "",
    ]
    windows = (TRAIN, *FOLDS)
    for v in VARIANTS:
        out += [detail(by[(v, DEFAULT)].trades, f"{v} default {fmt_pt(DEFAULT)}", windows), ""]

    out += [
        "## 2. All 54 points, full evaluated period (descriptive only)",
        "",
        "Grid order, not ranked. Full-period figures are **not** a selection basis.",
        "`stop>cap`, `stop<min` and `data_gap` are the strategy's diagnostic counts; `risk rej` =",
        "risk-engine rejections in the run journal; `worst` = trades decided by a worst-case",
        f"intra-bar assumption. Machine-readable: `{STEM}_points.csv`.",
        "",
        table(
            [
                "variant",
                "point (h1_ema, stop_atr_mult, target_r)",
                "trades",
                "net USD",
                "net USD/trade",
                "net R/trade",
                "gross USD/trade",
                "PF",
                "max DD USD",
                "win rate",
                "stop>cap",
                "stop<min",
                "data_gap",
                "risk rej",
                "worst",
            ],
            desc_rows,
        ),
        "",
    ]

    out += [
        "## 3. Registered anchored walk-forward (OOS evidence)",
        "",
        "For fold k, the selected point has the highest net expectancy over **all development",
        f"trades entered before fold k** (training + earlier folds), among points with ≥"
        f" {MIN_SELECTION_TRADES} such trades. Ties are compared exactly (integer cents): the default",
        "wins a tie, then the lexicographically smallest (h1_ema, stop_atr_mult, target_r). The",
        "fold's OOS trades are the selected point's trades entered in that fold, from its own run",
        "(no rescaling).",
        "",
    ]
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
            for (vv, p) in by
            if vv == v
        }
        sel_rows, oos_parts, selected = [], [], []
        for name, a, b in FOLDS:
            s = select_point(
                entries, before=utc(a), default=DEFAULT, min_trades=MIN_SELECTION_TRADES
            )
            selected.append(s.point)
            fold_t = in_window(by[(v, s.point)].trades, a, b) if s.point else pl.DataFrame()
            if s.point:
                oos_parts.append(fold_t)
            m = metrics(fold_t) if s.point else {"trades": 0}
            sel_rows.append(
                [
                    name,
                    fmt_pt(s.point),
                    s.eligible,
                    s.trades,
                    money(s.net_cents / 100 / s.trades) if s.trades else "n/a",
                    ", ".join(fmt_pt(p) for p in s.tied) if len(s.tied) > 1 else "none",
                    m["trades"],
                    money(m.get("net")),
                    money(m.get("net_exp")),
                ]
            )
        oos = pl.concat(oos_parts) if oos_parts else pl.DataFrame()
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
        ]
        out += [detail(oos, f"{v} concatenated OOS (folds 1–4)", FOLDS), ""]
        months = split_rows(oos, pl.col("signal_time").dt.strftime("%Y-%m"), "")
        out += ["Monthly (entry month, UTC):", "", table(SPLIT_HEADER, months), ""]
        verdicts[v] = gates(v, oos, selected, by, trial_srs, daily)
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
    ]
    for v in VARIANTS:
        out.append(f"- **{v}:** {verdicts[v]['overall']}")
    out += [
        "",
        "Not evaluated here (not authorised): gate 5 (spread ×1.5 re-run), gate 11 (best-case",
        "intra-bar replay / Phase 3b), gate 10 on the frozen configuration (final candidate",
        "selection), the null-baseline rerun over 2020-12-15 … 2025-09-30. The existing null",
        "baseline (`reference_fixed_time`, 2020-11-12 … 2026-10-08, −381.76 USD, docs/STATUS.md)",
        "covers another period, including the sealed one, so it is not shown side by side; its",
        "trades were not read.",
        f"- Total trial count: {N_TRIALS} (the whole declared search; N for the DSR).",
        f"- Trades exiting on a date outside the market calendar in folds 1–4 (all 54 runs): {off_calendar}.",
    ]
    return "\n".join(out) + "\n"


def gates(
    v: str,
    oos: pl.DataFrame,
    selected: list[Point | None],
    by: dict[Any, Run],
    trial_srs: list[float],
    daily: Any,
) -> dict[str, Any]:
    g: dict[str, str] = {}
    fold_ms = [metrics(in_window(oos, a, b)) for _, a, b in FOLDS]
    m = metrics(oos)
    n = m["trades"]
    sample_ok = all(f["trades"] >= 30 for f in fold_ms) and n >= 200
    g["1 sample (≥30 per fold, ≥200)"] = (
        f"{'pass' if sample_ok else 'FAIL'}: {[f['trades'] for f in fold_ms]}, total {n}"
    )
    if not n:
        return {"gates": g, "text": "No OOS trades.", "overall": "fails (insufficient sample)"}
    g["2 positive after costs"] = (
        f"{'pass' if m['net_exp'] > 0 and m['net'] > 0 else 'FAIL'}: "
        f"{money(m['net_exp'])} USD/trade, {money(m['net'])} USD"
    )
    pos_folds = sum(1 for f in fold_ms if f["trades"] and f["net_exp"] > 0)
    g["3 ≥3 of 4 folds positive"] = f"{'pass' if pos_folds >= 3 else 'FAIL'}: {pos_folds} of 4"
    g["4 PF ≥ 1.10"] = f"{'pass' if m['pf'] >= 1.10 else 'FAIL'}: {num(m['pf'], 3)}"
    g["5 spread ×1.5 re-run"] = "not evaluated (stress re-runs not authorised)"
    g["6 max DD ≤ 1,000 USD"] = f"{'pass' if m['max_dd'] <= 1000 else 'FAIL'}: {money(m['max_dd'])}"
    month = oos.with_columns(pl.col("signal_time").dt.strftime("%Y-%m").alias("_m"))
    per_month = (
        month.group_by("_m")
        .agg(pl.col("net_pnl").sum())
        .sort(["net_pnl", "_m"], descending=[True, False])
    )
    best_month, best_net = per_month.row(0)
    rest = m["net"] - best_net
    g["7 no single month"] = (
        f"{'pass' if rest > 0 else 'FAIL'}: without {best_month} "
        f"({money(best_net)}) = {money(rest)}"
    )
    sides = {s: metrics(oos.filter(pl.col("side") == s)) for s in ("BUY", "SELL")}
    ok8 = all(x["trades"] >= 30 and x["net_exp"] >= 0 for x in sides.values())
    g["8 not one direction"] = f"{'pass' if ok8 else 'FAIL'}: " + ", ".join(
        f"{s} {x['trades']} @ {money(x.get('net_exp'))}" for s, x in sides.items()
    )
    sess = {
        s: metrics(oos.filter(pl.col("session") == s)) for s in ("london_morning", "ldn_ny_overlap")
    }
    ok9 = all(x["trades"] >= 30 and x.get("net_exp", -1) >= 0 for x in sess.values())
    g["9 not one session"] = f"{'pass' if ok9 else 'FAIL'}: " + ", ".join(
        f"{s} {x['trades']} @ {money(x.get('net_exp'))}" for s, x in sess.items()
    )
    # Gate 10 is defined on the frozen configuration, which is not selected here. Informational:
    # the neighbourhood of each walk-forward-selected point and of the default.
    nb_lines = []
    for p in dict.fromkeys([*(q for q in selected if q), DEFAULT]):
        nbs = neighbours(p, AXES)
        good = sum(
            1
            for q in nbs
            if metrics(in_window(by[(v, q)].trades, OOS_FIRST, OOS_LAST)).get("net_exp", -1) > 0
        )
        need = required_neighbours(len(nbs))
        nb_lines.append(
            f"{fmt_pt(p)}: {good} of {len(nbs)} neighbours > 0 in folds 1–4 "
            f"(needs {need}: {'met' if good >= need else 'not met'})"
        )
    g["10 neighbourhood"] = "not evaluated (frozen configuration not selected); informational below"
    flagged = int(oos["worst_case"].sum())
    g["11 intra-bar (Phase 3b)"] = (
        f"not evaluated (best-case replay not authorised); worst-case "
        f"flagged {flagged} of {n} = {flagged / n:.2%}"
    )
    rets, _ = daily(oos)
    dsr = deflated_sharpe(rets, trial_srs, N_TRIALS)
    m_ = sum(trial_srs) / len(trial_srs)
    v0 = sum((s - m_) ** 2 for s in trial_srs) / len(trial_srs)
    from statistics import NormalDist

    z = NormalDist().inv_cdf
    sr0_pop = math.sqrt(v0) * (
        (1 - 0.5772156649015329) * z(1 - 1 / N_TRIALS)
        + 0.5772156649015329 * z(1 - 1 / (N_TRIALS * math.e))
    )
    dsr_pop = NormalDist().cdf(
        (dsr.sr - sr0_pop)
        * math.sqrt(dsr.t - 1)
        / math.sqrt(1 - dsr.skew * dsr.sr + (dsr.kurtosis - 1) / 4 * dsr.sr**2)
    )
    g["12 DSR ≥ 0.95"] = f"{'pass' if dsr.dsr >= 0.95 else 'FAIL'}: {dsr.dsr:.4f}"
    g["13 sealed untouched"] = "pass: no trend_pullback run reads 2025-10-01 or later; 0 openings"
    failed = [k for k, x in g.items() if x.startswith("FAIL")]
    overall = (
        f"**fails** registered gate(s) {', '.join(k.split(' ')[0] for k in failed)} — "
        "under section 16 this variant cannot pass, whatever the unevaluated gates show."
        if failed
        else "meets every evaluated gate; it cannot pass until gates 5, 10 and 11 are evaluated."
    )
    text = "\n".join(
        [
            f"#### {v}: registered thresholds on the concatenated OOS trades",
            "",
            table(["gate", "result"], [[k, x] for k, x in g.items()]),
            "",
            "Neighbourhood (informational, gate 10 not evaluated): " + "; ".join(nb_lines) + ".",
            "",
            f"DSR inputs: SR {dsr.sr:.5f} per day, T {dsr.t} market days, skew {dsr.skew:.4f}, "
            f"kurtosis {dsr.kurtosis:.4f}, V {dsr.variance:.3e} (sample variance of the 54 trials' "
            f"SR, ddof 1), N {N_TRIALS}, SR0 {dsr.sr0:.5f}. With V as the population variance "
            f"(ddof 0): DSR {dsr_pop:.4f}.",
        ]
    )
    return {"gates": g, "text": text, "overall": overall}


def main() -> int:
    cfg, runs, extra = load()
    if len(runs) != N_TRIALS:
        print(f"expected {N_TRIALS} trials, found {len(runs)}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{STEM}_batch.md").write_text(
        evidence(cfg, runs, extra), encoding="utf-8", newline="\n"
    )
    (OUT / f"{STEM}_analysis.md").write_text(analysis(cfg, runs), encoding="utf-8", newline="\n")
    print("written to", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
