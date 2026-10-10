"""`aurum backtest run/report/compare` end to end on a synthetic data store."""

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
import yaml
from typer.testing import CliRunner

from aurum.adapters.storage.layout import DataLayout, RunLayout, ledger_path
from aurum.backtest.loading import load_market
from aurum.backtest.storage import new_run_id, read_manifest, read_trades
from aurum.cli import app
from aurum.core.errors import SealedPeriodError
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


def flat(output: str) -> str:
    """CLI output with rich's line wrapping undone."""
    return " ".join(output.split())


def test_run_writes_outputs_and_records_a_trial(env: tuple[Path, Path]) -> None:
    data, runs = env
    output = run("--variant", "m5")
    (run_id,) = run_ids(runs)
    assert run_id in output
    manifest = read_manifest(RunLayout(runs), run_id)
    assert manifest["tick_days"] == ["2026-09-15"]
    assert manifest["params"]["latency_ms"] == 200
    assert manifest["params"]["variant"] == "m5"
    assert f"trades sha256: {manifest['outputs']['trades']}" in output
    assert manifest["params"]["risk_engine"] == "PolicyRiskEngine"
    trades = read_trades(RunLayout(runs), run_id)
    # The 08:00 BUY trades on both days (one tick day, one bar day). The 14:30 SELL's
    # 3000-point stop needs ~30 USD at 0.01 lot: over the 25 USD budget (0.25% of 10,000).
    assert trades.height == 2
    assert set(trades["entry_mode"]) == {"ticks", "bars"}
    assert set(trades["volume"]) == {0.02}
    journal = pl.read_parquet(RunLayout(runs).journal(run_id))
    rejected = journal.filter(pl.col("event") == "risk_rejected")["detail"].to_list()
    assert len(rejected) == 2
    assert all("< volume_min 0.01" in r for r in rejected)
    with TrialLedger(ledger_path(data)) as ledger:
        (trial,) = ledger.list_trials()
    assert trial.trial_id == manifest["trial_id"]
    assert trial.params["run_id"] == run_id
    assert trial.metrics["trades"] == 2
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
    sealed = runner.invoke(
        app, ["backtest", "run", "trend_pullback", "--from", "2026-09-14", "--to", "2026-09-15"]
    )
    assert sealed.exit_code == 1
    assert "sealed final-test period" in flat(sealed.output)
    assert runner.invoke(app, ["backtest", "report", "missing"]).exit_code == 1
    bad_risk = runner.invoke(
        app,
        [
            "backtest",
            "run",
            "reference_fixed_time",
            "--from",
            "2026-09-14",
            "--to",
            "2026-09-15",
            "--risk",
            "yolo",
        ],
    )
    assert bad_risk.exit_code == 1
    assert "--risk must be policy or fixed" in bad_risk.output


def test_sealed_period_is_refused_then_opened_once_and_journalled(
    env: tuple[Path, Path], tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, runs = env
    # The reference strategy as if it were research (not exempt; its spec is set), with the
    # synthetic tick day inside the sealed period.
    configs = tmp_path / "configs"
    shutil.copytree(repo_root / "configs", configs)
    backtest = configs / "backtest.yaml"
    cfg = yaml.safe_load(backtest.read_text(encoding="utf-8"))
    cfg["sealed_period"] = {"first": "2026-09-15", "last": "2026-10-08", "exempt_families": []}
    backtest.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("AURUM_CONFIG_DIR", str(configs))
    base = ["backtest", "run", "reference_fixed_time", "--from", "2026-09-14", "--to"]

    refused = runner.invoke(app, [*base, "2026-09-15"])
    assert refused.exit_code == 1
    assert "overlaps the sealed final-test period" in flat(refused.output)
    # A run ending the day before the seal must not even read its first day (the extra end day).
    layout = DataLayout(data)
    with pytest.raises(SealedPeriodError, match="refusing to read"):
        load_market(
            layout, "XAUUSD", point=0.01, first=BAR_DAY, last=BAR_DAY, warmup_days=0,
            bar_spread_extra_points=2, sealed=(TICK_DAY, TICK_DAY),
        )  # fmt: skip
    clamped = runner.invoke(app, [*base, "2026-09-14"])
    assert clamped.exit_code == 0, clamped.output
    (run_id,) = run_ids(runs)
    assert read_manifest(RunLayout(runs), run_id)["sealed_opened"] is False

    no_reason = runner.invoke(app, [*base, "2026-09-15", "--open-sealed", "--by", "tester"])
    assert no_reason.exit_code == 1
    assert "needs who (--by) and why (--reason)" in flat(no_reason.output)
    opened = [*base, "2026-09-15", "--open-sealed", "--by", "tester", "--reason", "frozen"]
    first_open = runner.invoke(app, opened)
    assert first_open.exit_code == 0, first_open.output
    again = runner.invoke(app, opened)
    assert again.exit_code == 1
    assert "already opened" in flat(again.output)
    with TrialLedger(ledger_path(data)) as ledger:
        (opening,) = ledger.sealed_openings()
        trials = ledger.list_trials()
    assert (opening.strategy, opening.opened_by, opening.reason) == (
        "reference_fixed_time",
        "tester",
        "frozen",
    )
    assert [t.sealed_test_opened for t in trials] == [False, True]


def test_run_ids_sort_by_creation_within_one_second() -> None:
    # Regression: second-resolution ids sorted by their random suffix within a second.
    t = datetime(2026, 10, 9, 22, 29, 44, 900_000, tzinfo=UTC)
    ids = [new_run_id(t + timedelta(microseconds=n * 30_000), "s", "m5") for n in range(3)]
    assert sorted(ids) == ids
