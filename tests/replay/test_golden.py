"""Golden replay: every scenario in ``scenarios.py`` through the full backtest stack.

Runner -> engine -> FixedVolumeRisk -> SimBroker -> accounting, with the repo's real
sessions and market calendar (configs/), so a config change that moves a cutoff shows up here.
"""

from datetime import timedelta
from typing import Any

import polars as pl
import pytest
import yaml

from aurum.backtest.accounting import trades_frame
from aurum.backtest.broker import SimBroker, SimCosts
from aurum.backtest.runner import BacktestResult, run_backtest
from aurum.core.clock import ManualClock
from aurum.data.market_hours import MarketHours
from aurum.execution.engine import Engine
from aurum.risk.fixed import FixedVolumeRisk
from aurum.sessions import TradingCalendar
from aurum.settings import AppConfig
from tests.conftest import REPO_ROOT
from tests.replay.scenarios import SCENARIOS, Scenario
from tests.simdata import ScriptedStrategy, at

VOLUME = 0.20
COMMISSION = 2.25
SLIPPAGE_POINTS = 5
LATENCY = timedelta(milliseconds=200)


def replay(cfg: AppConfig, scenario: Scenario) -> tuple[BacktestResult, pl.DataFrame]:
    raw = yaml.safe_load((REPO_ROOT / "configs" / "sessions.yaml").read_text(encoding="utf-8"))
    sessions = type(cfg.sessions).model_validate({**raw, **scenario.sessions})
    hours = MarketHours(cfg.data.market)
    calendar = TradingCalendar(sessions, hours)
    mkt = scenario.build()
    clock = ManualClock(at("00:00", scenario.day) - timedelta(days=1))
    broker = SimBroker(
        market=mkt,
        spec=scenario.symbol_spec,
        clock=clock,
        costs=SimCosts(
            commission_per_lot_side=COMMISSION, slippage_points=SLIPPAGE_POINTS, latency=LATENCY
        ),
        balance=10_000.0,
        leverage=500,
        market_hours=hours,
        rejection_hooks=scenario.hooks,
    )
    broker.connect()
    engine = Engine(
        broker=broker,
        strategy=ScriptedStrategy(plans=scenario.plans),
        risk=FixedVolumeRisk(VOLUME, COMMISSION),
        calendar=calendar,
        symbol="XAUUSD",
        magic=7020261,
    )
    result = run_backtest(
        engine=engine,
        broker=broker,
        market=mkt,
        calendar=calendar,
        clock=clock,
        first=scenario.day,
        last=scenario.day,
    )
    return result, trades_frame(result, scenario.symbol_spec, "scripted")


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, float):
        return actual == pytest.approx(expected, abs=1e-6)
    return bool(actual == expected)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_golden_scenario(app_config: AppConfig, scenario: Scenario) -> None:
    result, trades = replay(app_config, scenario)

    assert result.open_at_end == 0
    rows = trades.to_dicts()
    assert len(rows) == len(scenario.expected), rows
    for row, expected in zip(rows, scenario.expected, strict=True):
        wrong = {
            k: (row[k], v) for k, v in expected.items() if not _matches(row[k], v)
        }  # column: (actual, expected)
        assert not wrong, f"{scenario.name}: {wrong}"
        assert row["volume"] == VOLUME
        # The attribution identity holds on every trade (up to cent rounding of booked P&L).
        rebuilt = row["net_pnl"] + row["spread_cost"] + row["slippage_cost"] + row["commission"]
        assert row["gross_pnl"] == pytest.approx(rebuilt, abs=1e-6)

    for event, fragment in scenario.journal:
        details = [e.detail for e in result.journal if str(e.event) == event]
        assert any(fragment in d for d in details), (event, fragment, details)

    # Deterministic: a second replay gives the same trades.
    assert replay(app_config, scenario)[1].equals(trades)
