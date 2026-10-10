"""One backtest from configuration to stored run and trial-ledger row (``aurum backtest run``).

``prepare`` resolves everything that defines the evaluation before any attempt exists:
the validated request (period, strategy, params, grid point), the sealed-period access (a
refusal raises here; only the journalled opening and parity paths write to the ledger), every
input file (warm-up included) and the frozen **evaluation context**: code, spec, config,
effective costs and risk, symbol snapshot, input-data hash, declared grid and dates. A request
that is invalid, refused or that the data store cannot serve is not an attempt.

``execute`` then records the attempt with that context (a grid batch or a retry under another
context is refused) and evaluates it:

1. load, simulate, account, and write the run into its staging directory; any failure is
   recorded with its stage and raised as ``EvaluationFailedError`` (no trial exists);
2. record the trial and the attempt's success in **one transaction** (both or neither; a grid
   point can have only one trial);
3. publish the staged run (rename). A failure here leaves the trial standing and the files
   staged (``ArtifactsPendingError``); ``recover_artifacts`` publishes them later without
   running the evaluation again.

The manifest ties the result to the code commit, the full config (hash), the exact input files
(data hash), the evaluation context and every parameter, including command-line overrides.
"""

import hashlib
import importlib.metadata
import json
import platform
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import aurum
from aurum.adapters.storage.layout import DataLayout, RunLayout, ledger_path
from aurum.adapters.storage.parquet import sha256_file
from aurum.adapters.storage.specs import SpecSnapshot, latest_spec
from aurum.backtest.accounting import equity_frame, journal_frame, summarize, trades_frame
from aurum.backtest.broker import SimBroker, SimCosts
from aurum.backtest.loading import MarketPlan, plan_market, read_market
from aurum.backtest.runner import run_backtest
from aurum.backtest.sealed import SealedAccess, resolve_access
from aurum.backtest.storage import new_run_id, publish_run, stage_run
from aurum.core.clock import ManualClock
from aurum.core.errors import ArtifactsPendingError, ConfigError, EvaluationFailedError
from aurum.data.market_hours import MarketHours
from aurum.execution.engine import Engine
from aurum.ledger.trial_ledger import TrialLedger, context_hash, current_code_version
from aurum.ports.clock import ClockPort
from aurum.risk.base import RiskEngine
from aurum.risk.fixed import FixedVolumeRisk
from aurum.risk.policy import PolicyRiskEngine
from aurum.sessions.calendar import TradingCalendar
from aurum.sessions.news import NewsBlackout
from aurum.settings import AppConfig, Settings, StrategyConfig
from aurum.strategies.base import Strategy
from aurum.strategies.registry import build_strategy, declared_grid


@dataclass(frozen=True)
class RunRequest:
    strategy: str
    variant: str
    first: date
    """First local trading date (inclusive)."""
    last: date
    """Last local trading date (inclusive)."""
    latency_ms: int | None = None
    """Override of backtest.latency_ms."""
    slippage_points: float | None = None
    """Override of broker.slippage.points_per_side."""
    risk: Literal["policy", "fixed"] = "policy"
    """``policy``: the Phase 4 risk engine (risk.yaml). ``fixed``: backtest.fixed_volume_lots
    on every approved signal, only for the MT5 Strategy Tester parity check."""
    notes: str = ""
    open_sealed: bool = False
    """The one-time, journalled opening of the sealed final-test period (aurum.backtest.sealed)."""
    opened_by: str = ""
    open_reason: str = ""
    parity: bool = False
    """The MT5 tester parity check of the designated reference strategy inside its approved
    window (backtest.yaml sealed_period.parity_exemption); journalled, never an opening."""
    params: dict[str, Any] | None = None
    """Strategy params replacing the config's (a declared grid point; validated by the strategy)."""
    grid_id: str | None = None
    """The declared-grid batch this attempt belongs to (``batch_id``; aurum.backtest.grid)."""
    retry_of: str | None = None
    """The failed or unfinished attempt this one retries (same grid point and context)."""


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    run_dir: Path
    trial_id: str
    attempt_id: str
    summary: dict[str, Any]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class Prepared:
    """A validated request with everything its evaluation depends on, resolved up front."""

    request: RunRequest
    strategy_cfg: StrategyConfig
    strategy: Strategy
    snapshot: SpecSnapshot
    access: SealedAccess
    plan: MarketPlan
    data_hash: str
    latency_ms: int
    slippage_points: float
    code_version: str | None
    config_hash: str
    purpose: str
    context: dict[str, Any]
    """The frozen evaluation context (everything but the strategy params)."""


@dataclass
class _Progress:
    stage: str = "load"


def config_hash(config: AppConfig) -> str:
    raw = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def batch_id(strategy: str, variant: str, first: date, last: date) -> str:
    """The declared-grid batch of one strategy, variant and period."""
    return f"{strategy}/{variant}/{first}..{last}"


def code_identity() -> dict[str, str]:
    """What runs: the imported ``aurum`` source tree (by content, so uncommitted edits count
    and docs-only commits do not), the installed distributions and the Python version."""
    root = Path(aurum.__file__).parent
    source = hashlib.sha256()
    for f in sorted(root.rglob("*.py")):
        source.update(f.relative_to(root).as_posix().encode())
        source.update(sha256_file(f).encode())
    dists = sorted(
        f"{(d.metadata['Name'] or '?').lower()}=={d.version}"
        for d in importlib.metadata.distributions()
    )
    return {
        "source_sha256": source.hexdigest(),
        "environment_sha256": _sha256_json(dists),
        "python": platform.python_version(),
    }


def spec_identity(settings: Settings, strategy_cfg: StrategyConfig) -> dict[str, str | None]:
    """The strategy's approved spec document and its content hash."""
    path = settings.project_root / strategy_cfg.spec if strategy_cfg.spec else None
    digest = sha256_file(path) if path is not None and path.is_file() else None
    return {"path": strategy_cfg.spec, "sha256": digest}


def prepare(settings: Settings, request: RunRequest, *, clock: ClockPort) -> Prepared:
    """Validate the request and resolve its evaluation context. Raises before any attempt; the
    only ledger writes are the journalled sealed opening and parity access (resolve_access)."""
    cfg = settings.config
    if request.last < request.first:
        raise ConfigError(f"empty period {request.first}..{request.last} (--from after --to)")
    if request.strategy not in cfg.strategies:
        raise ConfigError(f"unknown strategy {request.strategy!r}; see configs/strategies/")
    if cfg.broker.account_currency not in ("auto", cfg.broker.commission.currency):
        raise ConfigError("commission currency differs from the account currency")
    if (request.grid_id or request.retry_of) and (request.open_sealed or request.parity):
        raise ConfigError("grid points and retries are ordinary evaluations, never sealed/parity")
    snapshot = latest_spec(settings.data_dir)
    spec, symbol = snapshot.spec, snapshot.spec.name
    bt = cfg.backtest
    latency = bt.latency_ms if request.latency_ms is None else request.latency_ms
    slippage = (
        cfg.broker.slippage.points_per_side
        if request.slippage_points is None
        else request.slippage_points
    )
    calendar = TradingCalendar(cfg.sessions, MarketHours(cfg.data.market))
    start = calendar.local_to_utc(request.first, datetime.min.time())
    # Built (validated) before the sealed check, so a config error cannot spend the one-time
    # opening; building reads no data.
    strategy_cfg = cfg.strategies[request.strategy]
    if request.params is not None:
        strategy_cfg = strategy_cfg.model_copy(update={"params": dict(request.params)})
    strategy = build_strategy(
        strategy_cfg,
        variant=request.variant,
        symbol=symbol,
        point=spec.point,
        entries_allowed=lambda t: calendar.state_at(t).entries_allowed,
        stops_level_points=spec.stops_level_points,
    )
    grid: list[dict[str, Any]] | None = None
    if request.grid_id is not None:
        expected = batch_id(request.strategy, request.variant, request.first, request.last)
        if request.grid_id != expected:
            raise ConfigError(f"grid id {request.grid_id!r} is not this request's ({expected})")
        grid = declared_grid(cfg.strategies[request.strategy])
        if request.params not in grid:
            raise ConfigError(f"{request.params} is not a declared grid point")
    code_version = current_code_version(settings.project_root)
    cfg_hash = config_hash(cfg)
    access = resolve_access(
        cfg,
        request,
        ledger_file=ledger_path(settings.data_dir),
        config_hash=cfg_hash,
        code_version=code_version,
        clock=clock,
    )
    plan = plan_market(
        DataLayout(settings.data_dir),
        symbol,
        first=request.first,
        last=request.last,
        warmup_days=bt.warmup_days if access.warmup_days is None else access.warmup_days,
        last_data_day=access.last_data_day,
        forbidden=access.forbidden,
        warmup_bars=strategy.warmup_bars,
        warmup_before=start.date(),
    )
    data_hash = plan.data_hash(settings.data_dir)
    purpose = "parity" if request.parity else "evaluation"
    context: dict[str, Any] = {
        "strategy": request.strategy,
        "variant": request.variant,
        "first": request.first.isoformat(),
        "last": request.last.isoformat(),
        "purpose": purpose,
        "sealed_opened": access.opened,
        "code": code_identity(),
        "spec": spec_identity(settings, strategy_cfg),
        "config_hash": cfg_hash,
        "costs": {
            "commission_per_lot_side": cfg.broker.commission.per_lot_per_side,
            "slippage_points": slippage,
            "latency_ms": latency,
            "bar_spread_extra_points": bt.bar_spread_extra_points,
            "max_quote_wait_seconds": bt.max_quote_wait_seconds,
        },
        "risk": {
            "engine": request.risk,
            "policy": cfg.risk.model_dump(mode="json") if request.risk == "policy" else None,
            "fixed_volume_lots": bt.fixed_volume_lots if request.risk == "fixed" else None,
        },
        "account": {
            "initial_balance": bt.initial_balance,
            "leverage": bt.leverage,
            "currency": cfg.broker.commission.currency,
        },
        "symbol": {
            "snapshot": snapshot.path.name,
            "server": snapshot.server,
            "spec_sha256": _sha256_json(spec.model_dump(mode="json")),
        },
        "data": {
            "sha256": data_hash,
            "files": len(plan.files),
            "first_data_day": plan.first_data_day.isoformat(),
            "warmup_bars": {str(tf): n for tf, n in strategy.warmup_bars.items()},
        },
        "grid": None if grid is None else _sha256_json(grid),
    }
    return Prepared(
        request=request,
        strategy_cfg=strategy_cfg,
        strategy=strategy,
        snapshot=snapshot,
        access=access,
        plan=plan,
        data_hash=data_hash,
        latency_ms=latency,
        slippage_points=slippage,
        code_version=code_version,
        config_hash=cfg_hash,
        purpose=purpose,
        context=context,
    )


@dataclass(frozen=True)
class _Staged:
    run_id: str
    summary: dict[str, Any]
    manifest: dict[str, Any]
    params: dict[str, Any]
    outputs: dict[str, str]
    data_start: datetime
    data_end: datetime


def _evaluate(
    settings: Settings,
    prep: Prepared,
    attempt_id: str,
    *,
    runs: RunLayout,
    clock: ClockPort,
    progress: _Progress,
) -> _Staged:
    """Load, simulate, account and stage the run's files (no trial yet)."""
    cfg, request, strategy = settings.config, prep.request, prep.strategy
    spec, symbol, bt = prep.snapshot.spec, prep.snapshot.spec.name, cfg.backtest
    hours = MarketHours(cfg.data.market)
    calendar = TradingCalendar(cfg.sessions, hours)
    start = calendar.local_to_utc(request.first, datetime.min.time())
    progress.stage = "load"
    loaded = read_market(
        DataLayout(settings.data_dir),
        prep.plan,
        point=spec.point,
        bar_spread_extra_points=bt.bar_spread_extra_points,
    )
    sim_clock = ManualClock(start)
    commission = cfg.broker.commission.per_lot_per_side
    broker = SimBroker(
        market=loaded.market,
        spec=spec,
        clock=sim_clock,
        costs=SimCosts(
            commission_per_lot_side=commission,
            slippage_points=prep.slippage_points,
            latency=timedelta(milliseconds=prep.latency_ms),
            max_quote_wait=timedelta(seconds=bt.max_quote_wait_seconds),
        ),
        balance=bt.initial_balance,
        leverage=bt.leverage,
        currency=cfg.broker.commission.currency,
        market_hours=hours,
    )
    broker.connect()
    risk_engine: RiskEngine = (
        PolicyRiskEngine(
            cfg.risk,
            commission_per_lot_side=commission,
            stop_slippage_points=prep.slippage_points,
            news=NewsBlackout(cfg.risk.news_blackout),
        )
        if request.risk == "policy"
        else FixedVolumeRisk(bt.fixed_volume_lots, commission)
    )
    engine = Engine(
        broker=broker,
        strategy=strategy,
        risk=risk_engine,
        calendar=calendar,
        symbol=symbol,
        magic=cfg.broker.magic_number,
        max_intent_age=timedelta(seconds=cfg.broker.max_intent_age_seconds),
        atr_period=cfg.risk.atr_period,
    )
    progress.stage = "simulate"
    result = run_backtest(
        engine=engine,
        broker=broker,
        market=loaded.market,
        calendar=calendar,
        clock=sim_clock,
        first=request.first,
        last=request.last,
    )
    progress.stage = "account"
    trades = trades_frame(result, spec, strategy.strategy_id)
    equity = equity_frame(result.equity)
    journal = journal_frame(result.journal)
    summary: dict[str, Any] = {
        **summarize(trades, equity, bt.initial_balance),
        "open_at_end": result.open_at_end,
        "tick_days": len(loaded.tick_days),
        "bar_days": len(loaded.bar_days),
        **strategy.diagnostics(),
    }
    ctx_hash = context_hash(prep.context)
    params: dict[str, Any] = {
        "purpose": "parity" if prep.access.parity else "evaluation",
        "strategy_params": prep.strategy_cfg.params,
        "attempt_id": attempt_id,
        "grid_id": request.grid_id,
        "retry_of": request.retry_of,
        "context_hash": ctx_hash,
        "variant": request.variant,
        "symbol": symbol,
        "latency_ms": prep.latency_ms,
        "slippage_points": prep.slippage_points,
        "commission_per_lot_side": commission,
        "bar_spread_extra_points": bt.bar_spread_extra_points,
        "max_quote_wait_seconds": bt.max_quote_wait_seconds,
        "risk_engine": (
            "PolicyRiskEngine" if request.risk == "policy" else "FixedVolumeRisk (parity only)"
        ),
        "volume_lots": bt.fixed_volume_lots if request.risk == "fixed" else None,
        "risk_per_trade_pct": cfg.risk.risk_per_trade_pct if request.risk == "policy" else None,
        "initial_balance": bt.initial_balance,
        "leverage": bt.leverage,
        "market_close_buffer_minutes": cfg.sessions.market_close_buffer_minutes,
    }
    created = clock.now()
    run_id = new_run_id(created, request.strategy, request.variant)
    manifest: dict[str, Any] = {
        "created_at": created.isoformat(),
        "strategy": request.strategy,
        "variant": request.variant,
        "first": request.first.isoformat(),
        "last": request.last.isoformat(),
        "code_version": prep.code_version,
        "config_hash": prep.config_hash,
        "data_hash": prep.data_hash,
        "data_files": len(loaded.files),
        "tick_days": [d.isoformat() for d in loaded.tick_days],
        "spec_snapshot": prep.snapshot.path.name,
        "spec": spec.model_dump(mode="json"),
        "params": params,
        "notes": request.notes,
        "sealed_opened": prep.access.opened,
        "warmup_bars": {str(tf): n for tf, n in strategy.warmup_bars.items()},
        "first_data_day": loaded.first_data_day.isoformat(),
        "attempt_id": attempt_id,
        "context_hash": ctx_hash,
        "context": prep.context,
    }
    progress.stage = "write"
    outputs = stage_run(
        runs,
        run_id,
        trades=trades,
        equity=equity,
        journal=journal,
        manifest=manifest,
        summary=summary,
    )
    return _Staged(
        run_id=run_id,
        summary=summary,
        manifest={**manifest, "run_id": run_id, "outputs": outputs},
        params={**params, "run_id": run_id, "data_hash": prep.data_hash, "outputs": outputs},
        outputs=outputs,
        data_start=result.start,
        data_end=result.end,
    )


def execute(
    settings: Settings, request: RunRequest, *, runs: RunLayout, clock: ClockPort
) -> RunOutcome:
    prep = prepare(settings, request, clock=clock)
    ledger_file = ledger_path(settings.data_dir)
    with TrialLedger(ledger_file, clock=clock) as ledger:
        attempt_id = ledger.start_attempt(
            strategy=request.strategy,
            variant=request.variant,
            params=prep.strategy_cfg.params,
            first_day=request.first,
            last_day=request.last,
            purpose=prep.purpose,
            context=prep.context,
            grid_id=request.grid_id,
            retry_of=request.retry_of,
            code_version=prep.code_version,
            config_hash=prep.config_hash,
        )
    progress = _Progress()

    def failed(exc: BaseException) -> None:
        with TrialLedger(ledger_file, clock=clock) as ledger:
            ledger.fail_attempt(
                attempt_id, stage=progress.stage, error=f"{type(exc).__name__}: {exc}"
            )
        if isinstance(exc, Exception):
            raise EvaluationFailedError(attempt_id, progress.stage, exc) from exc

    try:
        staged = _evaluate(settings, prep, attempt_id, runs=runs, clock=clock, progress=progress)
    except BaseException as exc:
        failed(exc)
        raise
    progress.stage = "record"
    try:
        with TrialLedger(ledger_file, clock=clock) as ledger:
            trial = ledger.record_evaluation(
                attempt_id,
                strategy=request.strategy,
                variant=request.variant,
                params=staged.params,
                data_start=staged.data_start,
                data_end=staged.data_end,
                metrics={
                    k: (float(v) if v is not None else None) for k, v in staged.summary.items()
                },
                notes=request.notes or f"backtest run {staged.run_id}",
                code_version=prep.code_version,
                config_hash=prep.config_hash,
                sealed_test_opened=prep.access.opened,
            )
    except BaseException as exc:
        with TrialLedger(ledger_file, clock=clock) as ledger:
            recorded = ledger.trial_of(attempt_id)
        if recorded is None:  # rolled back: neither trial nor success exists
            failed(exc)
            raise
        if isinstance(exc, Exception):  # it committed; only the files are left to publish
            raise ArtifactsPendingError(attempt_id, recorded, staged.run_id, exc) from exc
        raise
    try:
        publish_run(runs, staged.run_id, trial_id=trial.trial_id, outputs=staged.outputs)
    except Exception as exc:
        raise ArtifactsPendingError(attempt_id, trial.trial_id, staged.run_id, exc) from exc
    return RunOutcome(
        run_id=staged.run_id,
        run_dir=runs.run_dir(staged.run_id),
        trial_id=trial.trial_id,
        attempt_id=attempt_id,
        summary=staged.summary,
        manifest={**staged.manifest, "trial_id": trial.trial_id},
    )


@dataclass(frozen=True)
class Recovered:
    trial_id: str
    run_id: str
    status: str
    """``published`` (from its staged files) or ``missing: <why>`` (the trial stands)."""


def recover_artifacts(settings: Settings, runs: RunLayout) -> list[Recovered]:
    """Publish the staged files of every recorded trial whose run is not published. Writes no
    ledger row and runs nothing: a trial is never evaluated again to rebuild its files."""
    with TrialLedger(ledger_path(settings.data_dir)) as ledger:
        trials = ledger.list_trials()
    found: list[Recovered] = []
    for trial in trials:
        run_id, outputs = trial.params.get("run_id"), trial.params.get("outputs")
        if not isinstance(run_id, str) or not isinstance(outputs, dict):
            continue  # recorded before runs were staged; published with its trial
        if runs.manifest(run_id).is_file():
            continue
        try:
            publish_run(runs, run_id, trial_id=trial.trial_id, outputs=outputs)
        except (OSError, ValueError) as exc:
            found.append(Recovered(trial.trial_id, run_id, f"missing: {exc}"))
            continue
        found.append(Recovered(trial.trial_id, run_id, "published"))
    return found
