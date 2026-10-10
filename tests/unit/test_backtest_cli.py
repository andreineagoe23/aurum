"""`aurum backtest run/report/compare` end to end on a synthetic data store."""

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml
from typer.testing import CliRunner

from aurum.adapters.storage.layout import DataLayout, RunLayout, ledger_path
from aurum.backtest import pipeline
from aurum.backtest.loading import load_market
from aurum.backtest.runner import BacktestResult, EquityPoint
from aurum.backtest.storage import new_run_id, read_manifest, read_trades
from aurum.cli import app
from aurum.core.errors import LedgerError, SealedPeriodError
from aurum.core.timeframes import Timeframe
from aurum.ledger.trial_ledger import TrialLedger
from tests.sim_store import write_store

runner = CliRunner()
TICK_DAY, BAR_DAY = date(2026, 9, 15), date(2026, 9, 14)  # Tuesday (exact ticks), Monday (bars)


def use_configs(
    tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch, sealed: dict[str, object]
) -> None:
    """The repo configs with another sealed period (tests of pipeline mechanics keep the seal
    out of their synthetic days; the real seal is tested in test_parity.py)."""
    configs = tmp_path / "configs"
    shutil.copytree(repo_root / "configs", configs)
    backtest = configs / "backtest.yaml"
    cfg = yaml.safe_load(backtest.read_text(encoding="utf-8"))
    cfg["sealed_period"] = sealed
    backtest.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("AURUM_CONFIG_DIR", str(configs))


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_root: Path) -> tuple[Path, Path]:
    data, runs = tmp_path / "data", tmp_path / "runs"
    use_configs(tmp_path, repo_root, monkeypatch, {"first": "2030-01-07", "last": "2030-12-31"})
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
    assert (manifest["warmup_bars"], manifest["first_data_day"]) == ({}, "2026-09-04")
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
    short = runner.invoke(
        app, ["backtest", "run", "trend_pullback", "--from", "2026-09-14", "--to", "2026-09-15"]
    )
    assert short.exit_code == 1  # two synthetic days: never a short warm-up
    assert "warm-up needs 504 completed H1 bars" in flat(short.output)
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


@pytest.fixture(scope="module")
def research_store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """27 synthetic bar days, Mon 2025-08-04 .. Tue 2025-09-09: enough warm-up (504 H1 bars)
    for trend_pullback before 2025-09-08."""
    root = tmp_path_factory.mktemp("research") / "data"
    days = [date(2025, 8, 4) + timedelta(days=i) for i in range(37)]
    write_store(root, tick_days=[], bar_days=[d for d in days if d.weekday() < 5])
    return root


def test_research_seal_is_refused_then_opened_once_and_journalled(
    research_store: Path, tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, runs = tmp_path / "data", tmp_path / "runs"
    shutil.copytree(research_store, data)
    monkeypatch.setenv("AURUM_DATA_DIR", str(data))
    monkeypatch.setenv("AURUM_RUNS_DIR", str(runs))
    use_configs(tmp_path, repo_root, monkeypatch, {"first": "2025-09-09", "last": "2025-09-30"})
    base = ["backtest", "run", "trend_pullback", "--from", "2025-09-08", "--to"]

    refused = runner.invoke(app, [*base, "2025-09-09"])
    assert refused.exit_code == 1
    assert "overlaps the sealed final-test period" in flat(refused.output)
    # Ending the day before the seal: the extra end day (09-09) is not even read.
    with pytest.raises(SealedPeriodError, match="refusing to read"):
        load_market(
            DataLayout(data), "XAUUSD", point=0.01, first=date(2025, 9, 8),
            last=date(2025, 9, 8), warmup_days=0, bar_spread_extra_points=2,
            forbidden=[(date(2025, 9, 9), date(2025, 9, 30))],
        )  # fmt: skip
    clamped = runner.invoke(app, [*base, "2025-09-08"])
    assert clamped.exit_code == 0, clamped.output
    (run_id,) = run_ids(runs)
    manifest = read_manifest(RunLayout(runs), run_id)
    assert (manifest["sealed_opened"], manifest["params"]["purpose"]) == (False, "evaluation")

    no_reason = runner.invoke(app, [*base, "2025-09-09", "--open-sealed", "--by", "tester"])
    assert no_reason.exit_code == 1
    assert "needs who (--by) and why (--reason)" in flat(no_reason.output)
    reference = ["backtest", "run", "reference_fixed_time", "--from", "2025-09-09", "--to"]
    not_research = runner.invoke(
        app, [*reference, "2025-09-09", "--open-sealed", "--by", "tester", "--reason", "x"]
    )
    assert not_research.exit_code == 1
    assert "opened only for research strategies" in flat(not_research.output)
    opened = [*base, "2025-09-09", "--open-sealed", "--by", "tester", "--reason", "frozen"]
    first_open = runner.invoke(app, opened)
    assert first_open.exit_code == 0, first_open.output
    again = runner.invoke(app, opened)
    assert again.exit_code == 1
    assert "already opened" in flat(again.output)
    with TrialLedger(ledger_path(data)) as ledger:
        (opening,) = ledger.sealed_openings()
        trials = ledger.list_trials()
        assert ledger.parity_accesses() == []
    assert (opening.strategy, opening.opened_by, opening.reason) == (
        "trend_pullback",
        "tester",
        "frozen",
    )
    assert [t.sealed_test_opened for t in trials] == [False, True]


def test_warmup_is_loaded_by_bar_count(tmp_path: Path) -> None:
    # Thu 09-10 and Fri 09-11 have 21 H1 bars each; trading starts Mon 09-14 (London midnight
    # = 09-13 23:00 UTC, so whole UTC days before 09-13 count).
    thu, fri, mon = date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 14)
    layout = write_store(tmp_path, tick_days=[], bar_days=[thu, fri, mon])
    common = {"point": 0.01, "first": mon, "last": mon, "warmup_days": 0}
    loaded = load_market(
        layout, "XAUUSD", **common, bar_spread_extra_points=2,
        warmup_bars={Timeframe.H1: 30}, warmup_before=date(2026, 9, 13),
    )  # fmt: skip
    assert loaded.first_data_day == thu  # 21 on Friday are not enough; Thursday adds 21
    first_decision = datetime(2026, 9, 13, 23, tzinfo=UTC)
    assert len(loaded.market.store.view_at(first_decision).bars(Timeframe.H1)) == 2 * 21
    no_warmup = load_market(layout, "XAUUSD", **common, bar_spread_extra_points=2)
    assert no_warmup.first_data_day == mon
    with pytest.raises(ValueError, match="warm-up needs 100 completed H1 bars"):
        load_market(
            layout, "XAUUSD", **common, bar_spread_extra_points=2,
            warmup_bars={Timeframe.H1: 100}, warmup_before=date(2026, 9, 13),
        )  # fmt: skip
    with pytest.raises(SealedPeriodError):  # warm-up history may not come from it either
        load_market(
            layout, "XAUUSD", **common, bar_spread_extra_points=2,
            warmup_bars={Timeframe.H1: 30}, warmup_before=date(2026, 9, 13), forbidden=[(thu, thu)],
        )  # fmt: skip


def test_run_ids_sort_by_creation_within_one_second() -> None:
    # Regression: second-resolution ids sorted by their random suffix within a second.
    t = datetime(2026, 10, 9, 22, 29, 44, 900_000, tzinfo=UTC)
    ids = [new_run_id(t + timedelta(microseconds=n * 30_000), "s", "m5") for n in range(3)]
    assert sorted(ids) == ids


def test_a_failed_evaluation_stays_auditable(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    data, _ = env
    dates = ["--from", "2026-09-14", "--to", "2026-09-15"]
    refused = runner.invoke(app, ["backtest", "run", "nope", *dates])
    assert refused.exit_code == 1  # an invalid request is not an attempt

    def boom(**_: object) -> None:
        raise RuntimeError("injected")

    monkeypatch.setattr(pipeline, "run_backtest", boom)
    args = ["backtest", "run", "reference_fixed_time", "--from", "2026-09-14", "--to", "2026-09-15"]
    failed = runner.invoke(app, args)
    assert failed.exit_code == 1
    assert "failed at simulate: RuntimeError: injected" in flat(failed.output)
    with TrialLedger(ledger_path(data)) as ledger:
        (attempt,) = ledger.attempts()
        assert ledger.list_trials() == []
    assert (attempt.status, attempt.stage, attempt.error, attempt.trial_id) == (
        "failed",
        "simulate",
        "RuntimeError: injected",
        None,
    )


def test_grid_runs_the_declared_points_and_keeps_every_outcome(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    data, _ = env
    # Fast stand-ins for loading (no 504-bar warm-up on two synthetic days) and simulation;
    # validation, attempts, accounting, trials and run files are the real pipeline.
    real_load = pipeline.load_market
    monkeypatch.setattr(
        pipeline, "load_market", lambda *a, **k: real_load(*a, **{**k, "warmup_bars": None})
    )
    seen: list[object] = []

    def fake_run(*, engine: Any, clock: Any, **_: object) -> BacktestResult:
        p = engine._strategy._p
        seen.append(p)
        if (p.h1_ema, p.stop_atr_mult, p.target_r) == (80, 1.0, 2.0) and seen.count(p) == 1:
            raise RuntimeError("injected")  # only the first attempt of the last point fails
        start = clock.now()
        return BacktestResult(
            start=start, end=start + timedelta(days=1), trades=[], journal=[], exit_reasons={},
            signals={}, equity=[EquityPoint(start, 10_000.0, 10_000.0, 0)], open_at_end=0,
        )  # fmt: skip

    monkeypatch.setattr(pipeline, "run_backtest", fake_run)
    sealed = ["backtest", "grid", "trend_pullback", "--from", "2030-01-07", "--to", "2030-01-08"]
    refused = runner.invoke(app, sealed)  # the whole batch: before any attempt
    assert refused.exit_code == 1
    assert "overlaps the sealed final-test period" in flat(refused.output)
    grid = ["backtest", "grid", "trend_pullback", "--from", "2026-09-14", "--to", "2026-09-15"]
    for extra, code, tally, attempts in (
        ([], 1, "26 resolved, 1 unresolved", 27),  # the failure is reported, the batch goes on
        ([], 1, "26 resolved, 1 unresolved", 27),  # done points are not rerun; no silent retry
        (["--retry-failed"], 0, "27 resolved, 0 unresolved", 28),  # one explicit, linked retry
        (["--retry-failed"], 0, "27 resolved, 0 unresolved", 28),  # nothing left to run
    ):
        result = runner.invoke(app, [*grid, *extra])
        assert result.exit_code == code, result.output
        assert tally in flat(result.output)
        with TrialLedger(ledger_path(data)) as ledger:
            assert len(ledger.attempts()) == attempts
    with TrialLedger(ledger_path(data)) as ledger:
        history = ledger.attempts(grid_id="trend_pullback/m15/2026-09-14..2026-09-15")
        assert len(ledger.list_trials()) == 27  # one per declared point; the retry is not a 28th
        (failed,) = [a for a in history if a.status == "failed"]
        (retry,) = [a for a in history if a.retry_of]

        def check(attempt_id: str, params: dict[str, Any]) -> None:
            ledger.check_retry(
                attempt_id, strategy="trend_pullback", variant="m15", params=params,
                first_day=date(2026, 9, 14), last_day=date(2026, 9, 15), purpose="evaluation",
                grid_id=retry.grid_id,
            )  # fmt: skip

        with pytest.raises(LedgerError, match="nothing to retry"):
            check(retry.attempt_id, retry.params)
        with pytest.raises(LedgerError, match="exact grid point"):
            check(failed.attempt_id, {**failed.params, "target_r": 1.0})
        with pytest.raises(LedgerError, match="already retried"):
            check(failed.attempt_id, failed.params)
    assert (failed.stage, retry.retry_of, retry.status) == (
        "simulate",
        failed.attempt_id,
        "succeeded",
    )
    assert len({json.dumps(a.params, sort_keys=True) for a in history}) == 27
    with TrialLedger(ledger_path(data)) as ledger:  # a crash leaves an attempt without outcome
        crashed = ledger.start_attempt(
            strategy="trend_pullback", variant="m5", params={}, first_day=date(2026, 9, 14),
            last_day=date(2026, 9, 15), purpose="evaluation",
        )  # fmt: skip
        assert ledger.get_attempt(crashed).status == "unfinished"
