"""opening_range_breakout 0.1.0: inventory of the 12 development trials (no analysis).

Read-only: opens the trial ledger read-only and re-hashes the existing run files; runs no
simulation and reads no trade, equity or P&L figure. It records what was run and whether the
record is complete: batch contexts, attempts, outcomes, trials, published artifacts, failures,
retries, pending artifacts, sealed/parity journals, the range-date status coverage of each
trial (from its run summary) against the feasibility CSV, and the ledger backups.

    uv run --no-sync python scripts/opening_range_breakout_dev_batch.py

Writes docs/strategies/evidence/opening_range_breakout_2026-10-11_dev_{batch.md,index.csv,
contexts.json}.
"""

# ruff: noqa: E501, I001  (report prose; aurum is imported before polars, see below)
# The VM's polars needs aurum._platform to run first, so aurum is imported before it.
import aurum  # noqa: F401

import csv
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from aurum.adapters.storage.layout import RunLayout, ledger_path
from aurum.adapters.storage.parquet import sha256_file
from aurum.ledger.trial_ledger import _COLUMNS, TrialLedger
from aurum.settings import load_settings
from aurum.strategies.opening_range_breakout import STATUSES, declared_grid

OUT = Path("docs/strategies/evidence")
STEM = "opening_range_breakout_2026-10-11_dev"
FEASIBILITY = OUT / "opening_range_breakout_2026-10-11_feasibility.csv"
STRATEGY = "opening_range_breakout"
VARIANTS = ("m15", "m5")
FIRST, LAST = date(2020, 12, 15), date(2025, 9, 30)
GRID_IDS = {v: f"{STRATEGY}/{v}/{FIRST}..{LAST}" for v in VARIANTS}
SEALED_FIRST = datetime(2025, 10, 1, tzinfo=UTC)
LAST_DATA_END = datetime(2025, 9, 30, 23, tzinfo=UTC)  # London midnight of 2025-10-01
PRE_BACKUP = "trials_20261011T0838Z_before_opening_range_breakout_dev_grid.sqlite"
POST_BACKUP = "trials_20261011T{post}_after_opening_range_breakout_dev_grid.sqlite"
DIAGNOSTICS = ("data_gap", "blackout_skipped", "stop_below_minimum", "stop_above_cap")


def table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def expected_coverage() -> dict[tuple[str, int], dict[str, int]]:
    """Per (variant, or_bars): range-date statuses and summed counters from the feasibility
    CSV (the same rules; target_r changes no status)."""
    out: dict[tuple[str, int], Counter[str]] = {}
    with FEASIBILITY.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            c = out.setdefault((row["variant"], int(row["or_bars"])), Counter())
            c[f"status_{row['status']}"] += 1
            c["range_dates"] += 1
            c["data_gap"] += int(row["data_gap"])
            c["blackout_skipped"] += int(row["blackout_skipped"])
    for c in out.values():
        c["stop_below_minimum"] = c["status_stop_below_minimum"]
        c["stop_above_cap"] = c["status_stop_above_cap"]
    return {k: dict(v) for k, v in out.items()}


def main(post_stamp: str) -> int:
    settings = load_settings()
    db = ledger_path(settings.data_dir)
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    layout = RunLayout(settings.runs_dir)
    backups = db.parent / "backups"

    contexts = {
        gid: {"context_hash": h, "frozen_at": frozen, "context": json.loads(ctx)}
        for gid, h, ctx, frozen in conn.execute(
            "SELECT grid_id, context_hash, context_json, frozen_at FROM grid_batches ORDER BY seq"
        )
        if gid in GRID_IDS.values()
    }
    (OUT / f"{STEM}_contexts.json").write_text(
        json.dumps(contexts, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    attempts = [
        dict(zip(("attempt_id", "grid_id", "context_hash", "started_at", "finished_at", "status",
                  "stage", "error", "trial_id", "retry_of", "params_json"), r, strict=True))
        for r in conn.execute(
            "SELECT a.attempt_id, a.grid_id, a.context_hash, a.started_at, o.finished_at,"
            " o.status, o.stage, o.error, o.trial_id, a.retry_of, a.params_json"
            " FROM attempts a LEFT JOIN attempt_outcomes o USING (attempt_id)"
            " WHERE a.strategy = ? ORDER BY a.seq",
            (STRATEGY,),
        )
    ]  # fmt: skip
    trials = []
    for raw in conn.execute(
        f"SELECT {_COLUMNS} FROM trials WHERE strategy = ? ORDER BY seq", (STRATEGY,)
    ).fetchall():
        trials.append(dict(zip(_COLUMNS.split(", "), raw, strict=True)))

    # Ledger verification: every trial row (all strategies) re-hashed; integrity; journals.
    bad_hash = []
    for raw in conn.execute(f"SELECT {_COLUMNS} FROM trials ORDER BY seq").fetchall():
        row = dict(zip(_COLUMNS.split(", "), raw, strict=True))
        stored = row.pop("record_hash")
        if TrialLedger._hash(row) != stored:
            bad_hash.append(row["trial_id"])
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    tables = ("trials", "attempts", "attempt_outcomes", "grid_batches", "sealed_openings",
              "parity_accesses")  # fmt: skip
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    orb_openings = conn.execute(
        "SELECT COUNT(*) FROM sealed_openings WHERE strategy = ?", (STRATEGY,)
    ).fetchone()[0]
    orb_parity = conn.execute(
        "SELECT COUNT(*) FROM parity_accesses WHERE strategy = ?", (STRATEGY,)
    ).fetchone()[0]
    outside = [a["attempt_id"] for a in attempts if a["grid_id"] not in GRID_IDS.values()]

    # Artifacts, coverage and the index.
    by_attempt = {a["attempt_id"]: a for a in attempts}
    expected = expected_coverage()
    index_rows, problems, coverage_rows, coverage_bad = [], [], [], []
    durations: dict[str, list[float]] = {v: [] for v in VARIANTS}
    data_ends = []
    for t in trials:
        params = json.loads(t["params_json"])
        variant, sp = t["variant"], params["strategy_params"]
        a = by_attempt[params["attempt_id"]]
        run_id = params["run_id"]
        man_path = layout.manifest(run_id)
        published = man_path.is_file()
        found = (
            {k: sha256_file(layout.run_dir(run_id) / f"{k}.parquet") for k in params["outputs"]}
            if published
            else {}
        )
        man = json.loads(man_path.read_text(encoding="utf-8")) if published else {}
        checks = {
            "published": published,
            "outputs": found == params["outputs"] == man.get("outputs"),
            "manifest trial": man.get("trial_id") == t["trial_id"],
            "manifest attempt": man.get("attempt_id") == a["attempt_id"],
            "data hash": man.get("data_hash") == params["data_hash"],
            "context": man.get("context_hash") == params["context_hash"] == a["context_hash"]
            == contexts[GRID_IDS[variant]]["context_hash"],
            "outcome": (a["status"], a["trial_id"]) == ("succeeded", t["trial_id"]),
            "grid": params["grid_id"] == GRID_IDS[variant],
        }  # fmt: skip
        if not all(checks.values()):
            problems.append((t["trial_id"], {k: v for k, v in checks.items() if not v}))
        start, end = (
            datetime.fromisoformat(a["started_at"]),
            datetime.fromisoformat(a["finished_at"]),
        )
        durations[variant].append((end - start).total_seconds())
        data_ends.append(datetime.fromisoformat(t["data_end"]))
        summary = (
            json.loads((layout.run_dir(run_id) / "summary.json").read_text(encoding="utf-8"))
            if published
            else {}
        )
        observed = {
            k: int(summary.get(k, -1)) for k in (*(f"status_{s}" for s in STATUSES), *DIAGNOSTICS)
        }
        observed["range_dates"] = sum(observed[f"status_{s}"] for s in STATUSES)
        want = {k: expected[(variant, sp["or_bars"])].get(k, 0) for k in observed}
        same = observed == want
        if not same:
            coverage_bad.append(
                (
                    t["trial_id"],
                    {k: (want[k], observed[k]) for k in observed if want[k] != observed[k]},
                )
            )
        coverage_rows.append([
            variant, sp["or_bars"], sp["target_r"], observed["range_dates"],
            *(observed[f"status_{s}"] for s in STATUSES),
            observed["data_gap"], observed["blackout_skipped"], "yes" if same else "**no**",
        ])  # fmt: skip
        index_rows.append({
            "variant": variant, "or_bars": sp["or_bars"], "target_r": sp["target_r"],
            "attempt_id": a["attempt_id"], "trial_id": t["trial_id"], "run_id": run_id,
            "context_hash": params["context_hash"], "started_at": a["started_at"],
            "finished_at": a["finished_at"], "status": a["status"],
            "trades_sha256": found.get("trades", ""), "equity_sha256": found.get("equity", ""),
            "journal_sha256": found.get("journal", ""), "data_hash": params["data_hash"],
            "record_hash": t["record_hash"], "code_version": t["code_version"],
        })  # fmt: skip
    with (OUT / f"{STEM}_index.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(index_rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(index_rows)

    staging = layout.root / ".staging"
    staged = sorted(p.name for p in staging.iterdir()) if staging.exists() else []
    staged_orb = [s for s in staged if STRATEGY in s]
    failed = [a for a in attempts if a["status"] == "failed"]
    unfinished = [a for a in attempts if a["status"] is None]
    retries = [a for a in attempts if a["retry_of"]]
    declared = declared_grid()
    trial_points = {v: Counter() for v in VARIANTS}  # type: ignore[var-annotated]
    for t in trials:
        sp = json.loads(t["params_json"])["strategy_params"]
        trial_points[t["variant"]][(sp["or_bars"], sp["target_r"])] += 1
    missing_points = {
        v: [p for p in declared if trial_points[v][(p["or_bars"], p["target_r"])] != 1]
        for v in VARIANTS
    }
    bk = {}
    post = POST_BACKUP.format(post=post_stamp)
    for name in (PRE_BACKUP, post):
        bk[name] = hashlib.sha256((backups / name).read_bytes()).hexdigest()

    ctx = {v: contexts[GRID_IDS[v]]["context"] for v in VARIANTS}
    differ = sorted(k for k in ctx["m15"] if ctx["m15"][k] != ctx["m5"][k])
    c = ctx["m15"]
    sec = {v: sorted(d) for v, d in durations.items()}
    logs = {v: Path(f"reports/grid_logs/orb_{v}.log") for v in VARIANTS}
    log_tail = {
        v: [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()][-2:]
        for v, p in logs.items()
    }  # fmt: skip
    status_header = ["variant", "or_bars", "target_r", "range-dates", *STATUSES,
                     "Σ data_gap", "Σ blackout_skipped", "= feasibility"]  # fmt: skip
    lines = [
        "# opening_range_breakout 0.1.0: development batch inventory (2026-10-11)",
        "",
        "Generated read-only by `scripts/opening_range_breakout_dev_batch.py` from the trial ledger",
        "(opened read-only) and the existing run files. No simulation was run to produce it, and",
        "**no trade, equity or P&L figure is read or reported**: analysis is not authorised yet.",
        "",
        "## Batches and frozen contexts",
        "",
        table(
            ["batch id", "context hash", "frozen at"],
            [
                [f"`{g}`", f"`{contexts[g]['context_hash']}`", contexts[g]["frozen_at"]]
                for g in GRID_IDS.values()
            ],
        ),
        "",
        f"Full contexts: `{STEM}_contexts.json`. Context keys that differ between the batches: {differ}.",
        "",
        table(
            ["context part", "value"],
            [
                ["code: source sha256", f"`{c['code']['source_sha256']}`"],
                [
                    "code: environment sha256",
                    f"`{c['code']['environment_sha256']}` (equal to the trend_pullback batches')",
                ],
                ["code: Python", c["code"]["python"]],
                ["spec", f"`{c['spec']['path']}` `{c['spec']['sha256']}`"],
                ["config hash", f"`{c['config_hash']}`"],
                ["costs", json.dumps(c["costs"], sort_keys=True)],
                ["risk engine", c["risk"]["engine"]],
                ["account", json.dumps(c["account"], sort_keys=True)],
                [
                    "symbol snapshot",
                    f"`{c['symbol']['snapshot']}` file `{c['symbol']['file_sha256']}`",
                ],
                [
                    "input data",
                    f"`{c['data']['sha256']}`, {c['data']['files']} files from {c['data']['first_data_day']}",
                ],
                [
                    "history bars: strategy / engine / loaded",
                    f"{c['data']['warmup_bars']} / {c['data']['engine_history_bars']} / {c['data']['history_bars']}",
                ],
                ["declared grid", f"`{c['grid']}`"],
                [
                    "dates / purpose / sealed opened",
                    f"{c['first']}..{c['last']} / {c['purpose']} / {c['sealed_opened']}",
                ],
            ],
        ),
        "",
        "## Points, attempts, trials and outcomes",
        "",
        table(
            ["", "m15", "m5"],
            [
                ["grid points (declared)", len(declared), len(declared)],
                [
                    "attempts",
                    *(sum(a["grid_id"] == GRID_IDS[v] for a in attempts) for v in VARIANTS),
                ],
                [
                    "succeeded / failed / unfinished",
                    *(
                        f"{sum(a['status'] == 'succeeded' for a in attempts if a['grid_id'] == GRID_IDS[v])}/"
                        f"{sum(a['status'] == 'failed' for a in attempts if a['grid_id'] == GRID_IDS[v])}/"
                        f"{sum(a['status'] is None for a in attempts if a['grid_id'] == GRID_IDS[v])}"
                        for v in VARIANTS
                    ),
                ],
                [
                    "retries",
                    *(sum(1 for a in retries if a["grid_id"] == GRID_IDS[v]) for v in VARIANTS),
                ],
                ["trials", *(sum(t["variant"] == v for t in trials) for v in VARIANTS)],
                [
                    "declared points without exactly one trial",
                    *(len(missing_points[v]) for v in VARIANTS),
                ],
                [
                    "published runs",
                    *(
                        sum(r["variant"] == v and r["trades_sha256"] != "" for r in index_rows)
                        for v in VARIANTS
                    ),
                ],
                [
                    "first attempt started",
                    *(
                        min(a["started_at"] for a in attempts if a["grid_id"] == GRID_IDS[v])
                        for v in VARIANTS
                    ),
                ],
                [
                    "last outcome",
                    *(
                        max(a["finished_at"] or "" for a in attempts if a["grid_id"] == GRID_IDS[v])
                        for v in VARIANTS
                    ),
                ],
                [
                    "seconds per point: min / median / max",
                    *(f"{s[0]:.0f} / {s[len(s) // 2]:.0f} / {s[-1]:.0f}" for s in sec.values()),
                ],
            ],
        ),
        "",
        f"- **{len(declared) * 2} grid points, {len(attempts)} attempts, {len(trials)} trials**; ORB attempts outside the two batches: {len(outside)}.",
        f"- Failed attempts: {len(failed)}{(' ' + str([(a['attempt_id'], a['stage'], a['error']) for a in failed])) if failed else ''}. "
        f"Unfinished: {len(unfinished)}. Retries: {len(retries)} (none requested: no `--retry-failed`).",
        f"- Recorded trials whose run is not published (artifacts pending): {sum(1 for r in index_rows if not r['trades_sha256'])}. "
        f"Staging directory entries for this strategy: {staged_orb or 'none'}.",
        "- Grid logs (git-ignored), last lines:",
        *(
            f"  - `{logs[v].as_posix()}`: " + " / ".join(f"`{ln.strip()}`" for ln in log_tail[v])
            for v in VARIANTS
        ),
        "",
        "## Execution",
        "",
        "- Reviewed state `b5ea565`, clean worktree; m15 then m5, one process at a time, the",
        "  approved commands without `--retry-failed`.",
        "- `uv run` cannot start from the agent's process on this VM (os error 448, a junction in",
        "  the venv's interpreter path). The commands ran as the venv's own interpreter build",
        "  (`cpython-3.12.15`) with `-S` and only `C:\\venvs\\aurum\\Lib\\site-packages` on the path,",
        "  then `python -m aurum.cli backtest grid ...`: nothing synced or installed. The recorded",
        "  environment sha256 equals the one the trend_pullback batches recorded under",
        "  `uv run --no-sync`; without `-S` it would not have.",
        "- The source sha256 hashes this checkout's working-tree bytes. Git content equals `b5ea565`;",
        "  two source files (`backtest/parity.py`, `strategies/opening_range_breakout.py`) have CRLF",
        "  line endings in this checkout (`core.autocrlf`), so a fresh LF checkout hashes differently.",
        "- The live tick collector was not running (last heartbeat 2026-10-10 16:42Z); no input",
        "  file of the period can change, and it was not started or touched.",
        "",
        "## Range-date status coverage",
        "",
        "Expected: every one of the 1,237 trading dates has both ranges, so 2,474 range-dates per",
        "trial, with exactly the statuses and counters of the feasibility CSV for the trial's variant",
        "and `or_bars` (the same rules; `target_r` changes no status). Observed: the strategy's",
        "diagnostics in each run's `summary.json`. These are strategy statuses before the risk engine.",
        "",
        table(status_header, coverage_rows),
        "",
        f"Trials whose coverage differs from the feasibility CSV: {len(coverage_bad)}"
        + (f" {coverage_bad}" if coverage_bad else "")
        + ".",
        "",
        "## Ledger verification",
        "",
        f"- `PRAGMA integrity_check`: {integrity}.",
        f"- Every trial row re-hashed against its `record_hash` (read-only): {len(bad_hash)} mismatches of {counts['trials']}.",
        f"- Ledger tables: {counts}.",
        f"- Sealed openings: {counts['sealed_openings']} in all, {orb_openings} by {STRATEGY}. Parity accesses: {counts['parity_accesses']} in all, {orb_parity} by {STRATEGY}.",
        f"- {STRATEGY} trials: {len(trials)}, latest `data_end` {max(data_ends).isoformat()} "
        f"({'not after' if max(data_ends) <= LAST_DATA_END else '**after**'} London midnight of 2025-10-01; no bar dated 2025-10-01 or later is read).",
        "",
        "## Artifact verification",
        "",
        "For each trial: the three Parquet outputs re-hashed and compared with the hashes recorded in",
        "the trial and in the manifest; manifest trial id, attempt id, data hash and context hash",
        "compared with the ledger and the frozen batch; outcome = succeeded with this trial; grid id.",
        f"Problems: {len(problems)}" + (f" {problems}" if problems else "") + ".",
        "",
        f"Index of all {len(index_rows)} trials (variant, point, attempt, trial, run, context, timings,",
        f"output and record hashes): `{STEM}_index.csv`. Run files: `reports/runs/<run_id>/` (git-ignored).",
        "",
        "## Backups",
        "",
        table(
            ["backup (C:\\aurum-data\\ledger\\backups)", "sha256", "taken"],
            [
                [
                    PRE_BACKUP,
                    f"`{bk[PRE_BACKUP]}`",
                    "2026-10-11 08:38Z, before any ORB attempt (64 trials, 54 attempts)",
                ],
                [
                    post,
                    f"`{bk[post]}`",
                    f"after both batches ({counts['trials']} trials, {counts['attempts']} attempts)",
                ],
            ],
        ),
        "",
        "Both taken with the SQLite backup API (consistent copy); `integrity_check` ok. The pre-run",
        "backup's sha256 equals the post-run backup of the trend_pullback batches (2026-10-10 16:00Z):",
        "the ledger did not change between the two batches.",
        "",
    ]
    (OUT / f"{STEM}_batch.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    ok = not (problems or coverage_bad or bad_hash or failed or unfinished or missing_points["m15"]
              or missing_points["m5"] or integrity != "ok" or orb_openings or orb_parity)  # fmt: skip
    print(f"written to {OUT}; complete and consistent: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
