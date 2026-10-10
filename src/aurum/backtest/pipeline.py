"""One backtest from configuration to stored run and trial-ledger row (``aurum backtest run``).

Every run is recorded in the trial ledger, whatever its result (MASTER_PROMPT §4 rule 9). The
manifest ties the result to the code commit, the full config (hash), the exact input files
(data hash) and every parameter, including command-line overrides.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from aurum.adapters.storage.layout import DataLayout, RunLayout, ledger_path
from aurum.adapters.storage.specs import latest_spec
from aurum.backtest.accounting import equity_frame, journal_frame, summarize, trades_frame
from aurum.backtest.broker import SimBroker, SimCosts
from aurum.backtest.loading import load_market
from aurum.backtest.runner import run_backtest
from aurum.backtest.sealed import resolve_access
from aurum.backtest.storage import new_run_id, write_run
from aurum.core.clock import ManualClock
from aurum.core.errors import ConfigError
from aurum.data.market_hours import MarketHours
from aurum.execution.engine import Engine
from aurum.ledger.trial_ledger import TrialLedger, current_code_version
from aurum.ports.clock import ClockPort
from aurum.risk.base import RiskEngine
from aurum.risk.fixed import FixedVolumeRisk
from aurum.risk.policy import PolicyRiskEngine
from aurum.sessions.calendar import TradingCalendar
from aurum.sessions.news import NewsBlackout
from aurum.settings import AppConfig, Settings
from aurum.strategies.registry import build_strategy


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


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    run_dir: Path
    trial_id: str
    summary: dict[str, Any]
    manifest: dict[str, Any]


def config_hash(config: AppConfig) -> str:
    raw = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def execute(
    settings: Settings, request: RunRequest, *, runs: RunLayout, clock: ClockPort
) -> RunOutcome:
    cfg = settings.config
    if request.strategy not in cfg.strategies:
        raise ConfigError(f"unknown strategy {request.strategy!r}; see configs/strategies/")
    if cfg.broker.account_currency not in ("auto", cfg.broker.commission.currency):
        raise ConfigError("commission currency differs from the account currency")
    snapshot = latest_spec(settings.data_dir)
    spec, symbol = snapshot.spec, snapshot.spec.name
    bt = cfg.backtest
    latency = bt.latency_ms if request.latency_ms is None else request.latency_ms
    slippage = (
        cfg.broker.slippage.points_per_side
        if request.slippage_points is None
        else request.slippage_points
    )
    hours = MarketHours(cfg.data.market)
    calendar = TradingCalendar(cfg.sessions, hours)
    start = calendar.local_to_utc(request.first, datetime.min.time())
    # Built (validated) before the sealed check, so a config error cannot spend the one-time
    # opening; building reads no data.
    strategy = build_strategy(
        cfg.strategies[request.strategy],
        variant=request.variant,
        symbol=symbol,
        point=spec.point,
        entries_allowed=lambda t: calendar.state_at(t).entries_allowed,
        stops_level_points=spec.stops_level_points,
    )
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
    layout = DataLayout(settings.data_dir)
    loaded = load_market(
        layout,
        symbol,
        point=spec.point,
        first=request.first,
        last=request.last,
        warmup_days=bt.warmup_days if access.warmup_days is None else access.warmup_days,
        bar_spread_extra_points=bt.bar_spread_extra_points,
        last_data_day=access.last_data_day,
        forbidden=access.forbidden,
        warmup_bars=strategy.warmup_bars,
        warmup_before=start.date(),
    )
    sim_clock = ManualClock(start)
    commission = cfg.broker.commission.per_lot_per_side
    broker = SimBroker(
        market=loaded.market,
        spec=spec,
        clock=sim_clock,
        costs=SimCosts(
            commission_per_lot_side=commission,
            slippage_points=slippage,
            latency=timedelta(milliseconds=latency),
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
            stop_slippage_points=slippage,
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
    result = run_backtest(
        engine=engine,
        broker=broker,
        market=loaded.market,
        calendar=calendar,
        clock=sim_clock,
        first=request.first,
        last=request.last,
    )
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
    params: dict[str, Any] = {
        "purpose": "parity" if access.parity else "evaluation",
        "strategy_params": cfg.strategies[request.strategy].params,
        "variant": request.variant,
        "symbol": symbol,
        "latency_ms": latency,
        "slippage_points": slippage,
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
    data_hash = loaded.data_hash(settings.data_dir)
    manifest: dict[str, Any] = {
        "created_at": created.isoformat(),
        "strategy": request.strategy,
        "variant": request.variant,
        "first": request.first.isoformat(),
        "last": request.last.isoformat(),
        "code_version": code_version,
        "config_hash": cfg_hash,
        "data_hash": data_hash,
        "data_files": len(loaded.files),
        "tick_days": [d.isoformat() for d in loaded.tick_days],
        "spec_snapshot": snapshot.path.name,
        "spec": spec.model_dump(mode="json"),
        "params": params,
        "notes": request.notes,
        "sealed_opened": access.opened,
        "warmup_bars": {str(tf): n for tf, n in strategy.warmup_bars.items()},
        "first_data_day": loaded.first_data_day.isoformat(),
    }
    with TrialLedger(ledger_path(settings.data_dir)) as ledger:
        trial = ledger.record_trial(
            strategy=request.strategy,
            variant=request.variant,
            params={**params, "run_id": run_id, "data_hash": data_hash},
            data_start=result.start,
            data_end=result.end,
            metrics={k: (float(v) if v is not None else None) for k, v in summary.items()},
            notes=request.notes or f"backtest run {run_id}",
            code_version=code_version,
            config_hash=cfg_hash,
            sealed_test_opened=access.opened,
        )
    manifest["trial_id"] = trial.trial_id
    manifest["outputs"] = write_run(
        runs,
        run_id,
        trades=trades,
        equity=equity,
        journal=journal,
        manifest=manifest,
        summary=summary,
    )
    return RunOutcome(
        run_id=run_id,
        run_dir=runs.run_dir(run_id),
        trial_id=trial.trial_id,
        summary=summary,
        manifest=manifest,
    )
