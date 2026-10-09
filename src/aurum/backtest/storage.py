"""Write and read backtest runs (``RunLayout``: reports/runs/<run_id>/).

The Parquet outputs (trades, equity, journal) are deterministic: the same code, config and data
give byte-identical files, so a re-run can be checked by hash (Phase 3 exit gate). The manifest
records their SHA-256 together with the code commit, config hash and data hash.
"""

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from aurum.adapters.storage.layout import RunLayout
from aurum.adapters.storage.parquet import read_parquet, sha256_file, write_parquet
from aurum.data.schemas import BACKTEST_EQUITY, BACKTEST_JOURNAL, BACKTEST_TRADES

OUTPUTS = ("trades", "equity", "journal")


def new_run_id(created_at: datetime, strategy: str, variant: str) -> str:
    """``<UTC stamp to the microsecond>Z_<strategy>_<variant>_<6 hex>``: ids sort by creation
    time even for runs started within the same second."""
    return f"{created_at:%Y%m%dT%H%M%S%f}Z_{strategy}_{variant}_{secrets.token_hex(3)}"


def _json(path: Path, body: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(body, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_run(
    runs: RunLayout,
    run_id: str,
    *,
    trades: pl.DataFrame,
    equity: pl.DataFrame,
    journal: pl.DataFrame,
    manifest: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, str]:
    """Write every file of a run; returns the SHA-256 of each Parquet output (also stored in
    the manifest under ``outputs``). Refuses to overwrite an existing run."""
    run_dir = runs.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=False)
    write_parquet(trades, runs.trades(run_id), BACKTEST_TRADES, overwrite=False)
    write_parquet(equity, runs.equity(run_id), BACKTEST_EQUITY, overwrite=False)
    write_parquet(journal, runs.journal(run_id), BACKTEST_JOURNAL, overwrite=False)
    hashes = {
        "trades": sha256_file(runs.trades(run_id)),
        "equity": sha256_file(runs.equity(run_id)),
        "journal": sha256_file(runs.journal(run_id)),
    }
    _json(runs.summary(run_id), summary)
    _json(runs.manifest(run_id), {**manifest, "run_id": run_id, "outputs": hashes})
    return hashes


def read_manifest(runs: RunLayout, run_id: str) -> dict[str, Any]:
    path = runs.manifest(run_id)
    if not path.is_file():
        raise FileNotFoundError(f"no run {run_id!r} under {runs.root}")
    body: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return body


def read_summary(runs: RunLayout, run_id: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(runs.summary(run_id).read_text(encoding="utf-8"))
    return body


def read_trades(runs: RunLayout, run_id: str) -> pl.DataFrame:
    return read_parquet(runs.trades(run_id), BACKTEST_TRADES)


def verify_outputs(runs: RunLayout, run_id: str) -> dict[str, bool]:
    """Re-hash each Parquet output and compare with the manifest (True = unchanged)."""
    recorded = read_manifest(runs, run_id)["outputs"]
    paths = {
        "trades": runs.trades(run_id),
        "equity": runs.equity(run_id),
        "journal": runs.journal(run_id),
    }
    return {name: sha256_file(paths[name]) == recorded[name] for name in OUTPUTS}
