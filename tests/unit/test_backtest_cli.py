"""`aurum backtest run/report/compare` end to end on a synthetic data store."""

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aurum.adapters.storage.layout import RunLayout, ledger_path
from aurum.backtest.storage import read_manifest, read_trades
from aurum.cli import app
from aurum.ledger.trial_ledger import TrialLedger
from tests.sim_store import write_store

runner = CliRunner()
TICK_DAY, BAR_DAY = date(2026, 9, 15), date(2026, 9, 14)  # Tuesday (exact ticks), Monday (bars)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    data, runs = tmp_path / "data", tmp_path / "runs"
    write_store(data, tick_days=[TICK_DAY], bar_days=[BAR_DAY])
    monkeypatch.setenv("AURUM_DATA_DIR", str(data))
    monkeypatch.setenv("AURUM_RUNS_DIR", str(runs))
    return data, runs


def run(*extra: str) -> str:
    args = ["backtest", "run", "reference_fixed_time", "--from", "2026-09-14", "--to", "2026-09-15"]
    result = runner.invoke(app, [*args, *extra])
    assert result.exit_code == 0, result.output
    return result.output


def run_ids(runs: Path) -> list[str]:
    return RunLayout(runs).runs()


def test_run_writes_outputs_and_records_a_trial(env: tuple[Path, Path]) -> None:
    data, runs = env
    output = run("--variant", "m5")
    (run_id,) = run_ids(runs)
    assert run_id in output
    manifest = read_manifest(RunLayout(runs), run_id)
    assert manifest["tick_days"] == ["2026-09-15"]
    assert manifest["params"]["latency_ms"] == 200
    assert manifest["params"]["variant"] == "m5"
    trades = read_trades(RunLayout(runs), run_id)
    # Two entries per day (08:00 BUY, 14:30 SELL), one tick day and one bar day.
    assert trades.height == 4
    assert set(trades["entry_mode"]) == {"ticks", "bars"}
    with TrialLedger(ledger_path(data)) as ledger:
        (trial,) = ledger.list_trials()
    assert trial.trial_id == manifest["trial_id"]
    assert trial.params["run_id"] == run_id
    assert trial.metrics["trades"] == 4
    summary = json.loads((runs / run_id / "summary.json").read_text(encoding="utf-8"))
    assert summary["tick_days"] == 1
    assert summary["bar_days"] == 1


def test_rerun_is_byte_identical_and_overrides_are_not(env: tuple[Path, Path]) -> None:
    _, runs = env
    run()
    run()
    run("--latency-ms", "0", "--slippage-points", "0")
    first, second, third = run_ids(runs)
    same = runner.invoke(app, ["backtest", "compare", first, second])
    assert same.exit_code == 0, same.output
    differs = runner.invoke(app, ["backtest", "compare", first, third])
    assert differs.exit_code == 1


def test_report_shows_run_and_detects_tampering(env: tuple[Path, Path]) -> None:
    _, runs = env
    run()
    (run_id,) = run_ids(runs)
    ok = runner.invoke(app, ["backtest", "report", run_id])
    assert ok.exit_code == 0, ok.output
    assert "worst-case" in ok.output
    trades = RunLayout(runs).trades(run_id)
    trades.write_bytes(trades.read_bytes() + b"x")
    assert runner.invoke(app, ["backtest", "report", run_id]).exit_code == 1


def test_unknown_strategy_or_run_fails_cleanly(env: tuple[Path, Path]) -> None:
    bad = runner.invoke(
        app, ["backtest", "run", "nope", "--from", "2026-09-14", "--to", "2026-09-15"]
    )
    assert bad.exit_code == 1
    assert "unknown strategy" in bad.output
    phase5 = runner.invoke(
        app, ["backtest", "run", "trend_pullback", "--from", "2026-09-14", "--to", "2026-09-15"]
    )
    assert phase5.exit_code == 1
    assert "Phase 5" in phase5.output
    assert runner.invoke(app, ["backtest", "report", "missing"]).exit_code == 1
