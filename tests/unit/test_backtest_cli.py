"""`aurum backtest run/grid/recover/report/compare` end to end on a synthetic data store."""

import json
import os
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
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
from aurum.core.clock import SystemClock
from aurum.core.errors import ConfigError, LedgerError, SealedPeriodError
from aurum.core.timeframes import Timeframe
from aurum.ledger.trial_ledger import TrialLedger
from aurum.settings import load_settings
from aurum.strategies import trend_pullback
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


GRID = ["backtest", "grid", "trend_pullback", "--from", "2026-09-14", "--to", "2026-09-15"]
GRID_ID = "trend_pullback/m15/2026-09-14..2026-09-15"
LAST = (80, 1.0, 2.0)  # the last declared point


def point(p: Any) -> tuple[float, float, float]:
    return (p.h1_ema, p.stop_atr_mult, p.target_r)


def fast_grid(
    monkeypatch: pytest.MonkeyPatch, fail: Callable[[tuple[float, float, float], int], bool]
) -> list[tuple[float, float, float]]:
    """Fast stand-ins for warm-up (no 504-bar warm-up on two synthetic days) and simulation;
    validation, the evaluation context, attempts, accounting, trials and run files are the real
    pipeline. ``fail(point, n)``: raise in the n-th simulation of that point. Returns every
    simulated point, in order."""
    real_plan = pipeline.plan_market
    monkeypatch.setattr(
        pipeline, "plan_market", lambda *a, **k: real_plan(*a, **{**k, "warmup_bars": None})
    )
    seen: list[tuple[float, float, float]] = []

    def fake_run(*, engine: Any, clock: Any, **_: object) -> BacktestResult:
        seen.append(point(engine._strategy._p))
        if fail(seen[-1], seen.count(seen[-1])):
            raise RuntimeError("injected")
        start = clock.now()
        return BacktestResult(
            start=start, end=start + timedelta(days=1), trades=[], journal=[], exit_reasons={},
            signals={}, equity=[EquityPoint(start, 10_000.0, 10_000.0, 0)], open_at_end=0,
        )  # fmt: skip

    monkeypatch.setattr(pipeline, "run_backtest", fake_run)
    return seen


def ledger_rows(data: Path) -> dict[str, int]:
    """Row counts of every table the ledger journals into."""
    with TrialLedger(ledger_path(data)) as ledger:
        conn = ledger._conn
        tables = ("trials", "attempts", "attempt_outcomes", "sealed_openings", "parity_accesses",
                  "grid_batches")  # fmt: skip
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def frozen_context(data: Path) -> dict[str, Any]:
    with TrialLedger(ledger_path(data)) as ledger:
        (raw,) = ledger._conn.execute("SELECT context_json FROM grid_batches").fetchone()
    context: dict[str, Any] = json.loads(raw)
    return context


def test_grid_runs_the_declared_points_and_keeps_every_outcome(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    data, _ = env
    fast_grid(monkeypatch, lambda p, n: p == LAST and n == 1)  # only its first attempt fails
    sealed = ["backtest", "grid", "trend_pullback", "--from", "2030-01-07", "--to", "2030-01-08"]
    refused = runner.invoke(app, sealed)  # the whole batch: before any attempt
    assert refused.exit_code == 1
    assert "overlaps the sealed final-test period" in flat(refused.output)
    for extra, code, tally, attempts in (
        ([], 1, "26 resolved, 1 unresolved", 27),  # the failure is reported, the batch goes on
        ([], 1, "26 resolved, 1 unresolved", 27),  # done points are not rerun; no silent retry
        (["--retry-failed"], 0, "27 resolved, 0 unresolved", 28),  # one explicit, linked retry
        (["--retry-failed"], 0, "27 resolved, 0 unresolved", 28),  # nothing left to run
    ):
        result = runner.invoke(app, [*GRID, *extra])
        assert result.exit_code == code, result.output
        assert tally in flat(result.output)
        with TrialLedger(ledger_path(data)) as ledger:
            assert len(ledger.attempts()) == attempts
    context = frozen_context(data)
    with TrialLedger(ledger_path(data)) as ledger:
        history = ledger.attempts(grid_id=GRID_ID)
        assert len(ledger.list_trials()) == 27  # one per declared point; the retry is not a 28th
        (failed,) = [a for a in history if a.status == "failed"]
        (retry,) = [a for a in history if a.retry_of]

        def check(attempt_id: str, params: dict[str, Any], ctx: dict[str, Any] = context) -> None:
            ledger.check_retry(
                attempt_id, strategy="trend_pullback", variant="m15", params=params,
                first_day=date(2026, 9, 14), last_day=date(2026, 9, 15), purpose="evaluation",
                grid_id=GRID_ID, context=ctx,
            )  # fmt: skip

        with pytest.raises(LedgerError, match="nothing to retry"):
            check(retry.attempt_id, retry.params)
        with pytest.raises(LedgerError, match="exact grid point"):
            check(failed.attempt_id, {**failed.params, "target_r": 1.0})
        with pytest.raises(LedgerError, match="another evaluation context"):
            check(failed.attempt_id, failed.params, {**context, "config_hash": "other"})
        with pytest.raises(LedgerError, match="already has trial"):  # never evaluated again
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
            last_day=date(2026, 9, 15), purpose="evaluation", context={},
        )  # fmt: skip
        assert ledger.get_attempt(crashed).status == "unfinished"


def test_a_grid_batch_is_frozen_to_its_evaluation_context(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data, runs = env
    fast_grid(monkeypatch, lambda p, n: p == LAST and n == 1)
    assert runner.invoke(app, GRID).exit_code == 1  # 26 trials and one failed point to continue
    configs = Path(os.environ["AURUM_CONFIG_DIR"])
    warmup_day = date(2026, 9, 11)  # a Friday inside backtest.warmup_days (10) before 09-14
    extra = write_store(tmp_path / "extra", tick_days=[], bar_days=[warmup_day])
    tfs = (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1)

    def edit(name: str, old: str, new: str) -> Callable[[], None]:
        path = configs / name
        text = path.read_text(encoding="utf-8")
        assert old in text, name
        path.write_text(text.replace(old, new), encoding="utf-8")
        return lambda: path.write_text(text, encoding="utf-8")

    def resnapshot() -> Callable[[], None]:  # the same contract, captured again
        snaps = data / "specs" / "XAUUSD"
        again = snaps / "20260102T000000Z.json"
        shutil.copy(snaps / "20260101T000000Z.json", again)
        return again.unlink

    def add_warmup_day() -> Callable[[], None]:
        copied = [DataLayout(data).canonical_bars("XAUUSD", tf, warmup_day) for tf in tfs]
        for tf, dst in zip(tfs, copied, strict=True):
            shutil.copy(extra.canonical_bars("XAUUSD", tf, warmup_day), dst)

        def undo() -> None:
            for p in copied:
                p.unlink()

        return undo

    def patch(target: object, name: str, value: object) -> Callable[[], Callable[[], None]]:
        def apply() -> Callable[[], None]:
            mp = pytest.MonkeyPatch()
            mp.setattr(target, name, value)
            return mp.undo

        return apply

    other_grid = [{**p, "target_r": 2.5} if p["target_r"] == 2.0 else p
                  for p in trend_pullback.declared_grid()]  # fmt: skip
    changes: list[tuple[str, str, Callable[[], Callable[[], None]]]] = [
        ("code", "code", patch(pipeline, "code_identity", lambda: {"source_sha256": "edited"})),
        ("spec", "spec", patch(pipeline, "spec_identity", lambda s, c: {"sha256": "edited"})),
        ("costs", "config_hash, costs",
         lambda: edit("broker.yaml", "points_per_side: 5", "points_per_side: 6")),
        ("risk", "config_hash, risk",
         lambda: edit("risk.yaml", "risk_per_trade_pct: 0.25", "risk_per_trade_pct: 0.2")),
        ("symbol snapshot", "symbol", resnapshot),
        ("input data (warm-up)", "data", add_warmup_day),
        ("declared grid", "grid", patch(trend_pullback, "declared_grid", lambda: other_grid)),
    ]  # fmt: skip
    for label, changed, apply in changes:
        undo = apply()
        try:
            result = runner.invoke(app, [*GRID, "--retry-failed"])
        finally:
            undo()
        assert result.exit_code == 1, label
        assert f"frozen to another evaluation context (changed: {changed})" in flat(
            result.output
        ), (label, result.output)
        assert ledger_rows(data)["attempts"] == 27, label  # refused before any attempt
    settings = load_settings()
    with pytest.raises(ConfigError, match="not this request's"):  # other dates, same batch
        pipeline.execute(
            settings,
            pipeline.RunRequest(
                "trend_pullback", "m15", date(2026, 9, 14), date(2026, 9, 14),
                params=trend_pullback.declared_grid()[0], grid_id=GRID_ID,
            ),
            runs=RunLayout(runs), clock=SystemClock(),
        )  # fmt: skip
    resumed = runner.invoke(app, [*GRID, "--retry-failed"])  # the frozen context again
    assert resumed.exit_code == 0, resumed.output
    assert ledger_rows(data) == {
        "trials": 27, "attempts": 28, "attempt_outcomes": 28, "sealed_openings": 0,
        "parity_accesses": 0, "grid_batches": 1,
    }  # fmt: skip


class Crash(BaseException):
    """The process dying (never caught as an Exception)."""


def test_recorded_trials_are_recovered_never_evaluated_again(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    data, runs = env
    grid = [point(SimpleNamespace(**p)) for p in trend_pullback.declared_grid()]
    write_fails, publish_fails, outcome_fails, crashes = grid[:4]
    seen = fast_grid(monkeypatch, lambda p, n: False)
    current: list[tuple[float, float, float]] = []
    real_stage, real_publish = pipeline.stage_run, pipeline.publish_run
    real_record, real_outcome = TrialLedger.record_evaluation, TrialLedger._insert_outcome

    def stage(*a: Any, manifest: dict[str, Any], **k: Any) -> dict[str, str]:
        current[:] = [point(SimpleNamespace(**manifest["params"]["strategy_params"]))]
        if current == [write_fails] and seen.count(write_fails) == 1:
            raise OSError("disk full")
        return real_stage(*a, manifest=manifest, **k)

    def publish(*a: Any, **k: Any) -> None:
        if current == [publish_fails]:
            raise OSError("rename failed")
        real_publish(*a, **k)

    def record(self: TrialLedger, attempt_id: str, **k: Any) -> Any:
        if current == [crashes]:  # the old, non-atomic order: the trial, then death
            self.record_trial(**k)
            raise Crash
        return real_record(self, attempt_id, **k)

    def outcome(self: TrialLedger, attempt_id: str, status: str, *a: Any) -> None:
        if status == "succeeded" and current == [outcome_fails] and seen.count(outcome_fails) == 1:
            raise sqlite3.OperationalError("disk I/O error")  # inside the trial's transaction
        real_outcome(self, attempt_id, status, *a)

    with monkeypatch.context() as mp:
        mp.setattr(pipeline, "stage_run", stage)
        mp.setattr(pipeline, "publish_run", publish)
        mp.setattr(TrialLedger, "record_evaluation", record)
        mp.setattr(TrialLedger, "_insert_outcome", outcome)
        with pytest.raises(Crash):
            runner.invoke(app, GRID)
        current.clear()
    assert seen == grid[:4]  # the crash stopped the batch
    with TrialLedger(ledger_path(data)) as ledger:
        by_point = {point(SimpleNamespace(**a.params)): a for a in ledger.attempts()}
        trials = ledger.grid_trials(GRID_ID)
    states = {p: (a.status, a.stage, a.trial_id) for p, a in by_point.items()}
    assert states[write_fails][:2] == ("failed", "write")  # before any trial: retryable
    assert states[outcome_fails][:2] == ("failed", "record")  # rolled back with its trial
    assert states[publish_fails][0] == "succeeded"  # trial and outcome, files still staged
    assert states[crashes] == ("unfinished", None, None)  # a trial but no outcome
    assert len(trials) == 2  # the publish failure and the crash; nothing rolled back survives

    resumed = runner.invoke(app, GRID)  # the two points with a trial are reconciled, not rerun
    assert resumed.exit_code == 1
    assert "25 resolved, 2 unresolved, 2 with artifacts pending" in flat(resumed.output)
    assert seen.count(publish_fails) == seen.count(crashes) == 1

    recovered = runner.invoke(app, ["backtest", "recover"])
    assert recovered.exit_code == 0, recovered.output
    assert "2 unpublished runs" in recovered.output
    assert recovered.output.count(": published") == 2

    retried = runner.invoke(app, [*GRID, "--retry-failed"])  # the unfinished one is not retried
    assert retried.exit_code == 0, retried.output
    assert "27 resolved, 0 unresolved, 0 with artifacts pending" in flat(retried.output)
    assert {p: seen.count(p) for p in grid} == {
        p: 2 if p in (write_fails, outcome_fails) else 1 for p in grid
    }  # recovery never simulated a recorded point again
    with TrialLedger(ledger_path(data)) as ledger:
        trials = ledger.list_trials()
        assert len(trials) == len(ledger.grid_trials(GRID_ID)) == 27
    for trial in trials:
        manifest = read_manifest(RunLayout(runs), trial.params["run_id"])
        assert manifest["trial_id"] == trial.trial_id
        assert manifest["outputs"] == trial.params["outputs"]
    leftovers = [
        json.loads((d / "manifest.json").read_text(encoding="utf-8"))["attempt_id"]
        for d in (runs / ".staging").iterdir()
    ]
    assert leftovers == [by_point[outcome_fails].attempt_id]  # rolled back: never published
    assert "0 unpublished runs" in runner.invoke(app, ["backtest", "recover"]).output


def test_an_empty_period_leaves_no_ledger_trace(
    env: tuple[Path, Path], tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, _ = env
    # The 2030 seal with a parity window, so every journalled path is reachable.
    window = {"strategy": "reference_fixed_time", "variant": "m5", "first": "2030-01-07",
              "last": "2030-01-31"}  # fmt: skip
    sealed = {"first": "2030-01-07", "last": "2030-12-31", "parity_exemption": window}
    use_configs(tmp_path / "seal", repo_root, monkeypatch, sealed)
    inverted = ["--from", "2030-01-20", "--to", "2030-01-10"]  # overlaps, inside the window
    context = ["--variant", "m5", "--risk", "fixed", "--latency-ms", "0", "--slippage-points", "0"]
    for label, args in (
        ("run", ["backtest", "run", "reference_fixed_time", *inverted]),
        ("opening", ["backtest", "run", "trend_pullback", *inverted, "--open-sealed", "--by",
                     "tester", "--reason", "x"]),
        ("parity", ["backtest", "run", "reference_fixed_time", *inverted, *context, "--parity"]),
        ("grid", ["backtest", "grid", "trend_pullback", *inverted]),
    ):  # fmt: skip
        result = runner.invoke(app, args)
        assert result.exit_code == 1, label
        assert "empty period 2030-01-20..2030-01-10" in flat(result.output), label
        assert set(ledger_rows(data).values()) == {0}, label
    with TrialLedger(ledger_path(data)) as ledger, pytest.raises(LedgerError, match="empty"):
        ledger.start_attempt(
            strategy="s", variant="m5", params={}, first_day=date(2030, 1, 20),
            last_day=date(2030, 1, 10), purpose="evaluation", context={},
        )  # fmt: skip
