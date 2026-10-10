"""Write and read backtest runs (``RunLayout``: reports/runs/<run_id>/).

The Parquet outputs (trades, equity, journal) are deterministic: the same code, config and data
give byte-identical files, so a re-run can be checked by hash (Phase 3 exit gate). The manifest
records their SHA-256 together with the code commit, config hash and data hash.

A run is written to ``RunLayout.staging_dir`` first and renamed into place only after its trial
is in the ledger (``publish_run``), so a write failure never leaves a trial without its files
being recoverable, and a published run always has a trial.
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


def stage_run(
    runs: RunLayout,
    run_id: str,
    *,
    trades: pl.DataFrame,
    equity: pl.DataFrame,
    journal: pl.DataFrame,
    manifest: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, str]:
    """Write every file of a run into its staging directory (before its trial is recorded);
    returns the SHA-256 of each Parquet output (also in the manifest under ``outputs``)."""
    staged = runs.staging_dir(run_id)
    staged.mkdir(parents=True, exist_ok=False)
    paths = {name: staged / f"{name}.parquet" for name in OUTPUTS}
    write_parquet(trades, paths["trades"], BACKTEST_TRADES, overwrite=False)
    write_parquet(equity, paths["equity"], BACKTEST_EQUITY, overwrite=False)
    write_parquet(journal, paths["journal"], BACKTEST_JOURNAL, overwrite=False)
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    _json(staged / "summary.json", summary)
    _json(staged / "manifest.json", {**manifest, "run_id": run_id, "outputs": hashes})
    return hashes


def publish_run(runs: RunLayout, run_id: str, *, trial_id: str, outputs: dict[str, str]) -> None:
    """Move a staged run to ``run_dir`` once its trial is recorded: adds the trial id to the
    manifest, checks the staged outputs against the hashes recorded with the trial, renames.
    Refuses to overwrite an existing run."""
    staged = runs.staging_dir(run_id)
    if runs.run_dir(run_id).exists():
        raise FileExistsError(f"run {run_id} already exists under {runs.root}")
    if not (staged / "manifest.json").is_file():
        raise FileNotFoundError(f"no staged files for run {run_id}")
    found = {name: sha256_file(staged / f"{name}.parquet") for name in OUTPUTS}
    if found != outputs:
        raise ValueError(f"staged outputs of run {run_id} differ from those recorded")
    manifest = json.loads((staged / "manifest.json").read_text(encoding="utf-8"))
    _json(staged / "manifest.json", {**manifest, "trial_id": trial_id})
    staged.rename(runs.run_dir(run_id))


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
