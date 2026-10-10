"""Trade accounting: cost attribution identity, excursions, exit reasons, summary metrics."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from aurum.backtest.accounting import exit_reason, summarize, trade_row
from aurum.backtest.broker import IntrabarRule, SimTrade
from aurum.backtest.market import DataMode
from aurum.core.enums import DealReason, ExitReason, OrderType, Side
from aurum.data.schemas import BACKTEST_EQUITY, BACKTEST_TRADES, polars_schema
from tests.simdata import spec

T = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def sim_trade(side: Side = Side.BUY, **kw: object) -> SimTrade:
    base: dict[str, object] = {
        "position_id": 7,
        "idempotency_key": "AUabc12345",
        "magic": 1,
        "side": side,
        "volume": 0.20,
        "entry_type": OrderType.MARKET,
        "entry_time": T,
        "entry_price": 2000.15,
        "entry_bid": 2000.00,
        "entry_ask": 2000.10,
        "entry_mode": DataMode.TICKS,
        "exit_time": T + timedelta(minutes=30),
        "exit_price": 2000.95,
        "exit_bid": 2001.00,
        "exit_ask": 2001.10,
        "exit_mode": DataMode.TICKS,
        "exit_reason": DealReason.EXPERT,
        "stop_loss": 1995.0,
        "take_profit": None,
        "best": 2001.50,
        "worst": 1999.40,
        "commission": -0.90,
        "profit": 16.00,
        "intrabar": IntrabarRule.NONE,
    }
    base.update(kw)
    return SimTrade(**base)  # type: ignore[arg-type]


def test_buy_attribution_adds_up_to_mid_move() -> None:
    row = trade_row(1, sim_trade(), ExitReason.TIMEOUT, spec(), "s", T)
    assert row["net_pnl"] == pytest.approx(15.10)  # 16.00 profit - 0.90 commission
    assert row["spread_cost"] == pytest.approx(2.00)  # (0.05 + 0.05) x 20
    assert row["slippage_cost"] == pytest.approx(2.00)  # (0.05 + 0.05) x 20
    assert row["commission"] == pytest.approx(0.90)
    # Mid 2000.05 -> 2001.05 = 1.00 x 100 oz x 0.20 lots.
    assert row["gross_pnl"] == pytest.approx(20.00)
    assert row["mae_points"] == pytest.approx(75.0)  # 2000.15 - 1999.40
    assert row["mfe_points"] == pytest.approx(135.0)  # 2001.50 - 2000.15
    assert row["holding_seconds"] == 1800
    assert row["worst_case"] is False


def test_sell_attribution_adds_up_to_mid_move() -> None:
    t = sim_trade(
        Side.SELL,
        entry_price=1999.95,  # bid 2000.00 - 5 points
        exit_price=1999.15,  # ask 1999.10 + 5 points
        exit_bid=1999.00,
        exit_ask=1999.10,
        best=1998.50,
        worst=2000.60,
        profit=16.00,
    )
    row = trade_row(1, t, ExitReason.TARGET, spec(), "s", T)
    assert row["gross_pnl"] == pytest.approx(20.00)  # mid 2000.05 -> 1999.05
    assert row["slippage_cost"] == pytest.approx(2.00)
    assert row["mae_points"] == pytest.approx(65.0)  # 2000.60 - 1999.95
    assert row["mfe_points"] == pytest.approx(145.0)


def test_exit_reason_mapping() -> None:
    assert exit_reason(sim_trade(exit_reason=DealReason.STOP_LOSS), {}) is ExitReason.STOP
    assert exit_reason(sim_trade(exit_reason=DealReason.TAKE_PROFIT), {}) is ExitReason.TARGET
    assert exit_reason(sim_trade(), {7: ExitReason.FORCE_FLAT}) is ExitReason.FORCE_FLAT
    with pytest.raises(ValueError, match="without an engine reason"):
        exit_reason(sim_trade(), {})


def test_summary_metrics() -> None:
    rows = [
        trade_row(1, sim_trade(profit=30.0), ExitReason.TARGET, spec(), "s", T),
        trade_row(
            2,
            sim_trade(profit=-10.0, intrabar=IntrabarRule.CANCEL_BAR, entry_mode=DataMode.BARS),
            ExitReason.STOP,
            spec(),
            "s",
            T,
        ),
    ]
    trades = pl.DataFrame(rows, schema=polars_schema(BACKTEST_TRADES))
    equity = pl.DataFrame(
        {
            "time": [T, T + timedelta(minutes=5), T + timedelta(minutes=10)],
            "balance": [10_000.0, 10_029.1, 10_018.2],
            "equity": [10_000.0, 10_040.0, 10_010.0],
            "open_positions": [0, 1, 0],
        },
        schema=polars_schema(BACKTEST_EQUITY),
    )
    s = summarize(trades, equity, 10_000.0)
    assert s["trades"] == 2
    assert s["net_pnl"] == pytest.approx(29.1 - 10.9)
    assert s["profit_factor"] == pytest.approx(29.1 / 10.9, abs=1e-4)
    assert s["win_rate"] == 0.5
    assert s["max_drawdown"] == pytest.approx(30.0)
    assert s["worst_case_trades"] == 1
    assert s["worst_case_share"] == 0.5
    assert (s["intrabar_cancel_bar"], s["intrabar_sl_tp_same_bar"]) == (1, 0)
    assert s["trades_bar_mode"] == 1
    assert s["exit_target"] == 1
    assert s["exit_stop"] == 1


def test_summary_of_no_trades() -> None:
    empty = pl.DataFrame(schema=polars_schema(BACKTEST_TRADES))
    eq = pl.DataFrame(schema=polars_schema(BACKTEST_EQUITY))
    s = summarize(empty, eq, 10_000.0)
    assert s["trades"] == 0
    assert s["expectancy"] is None
    assert s["worst_case_share"] is None
    assert s["max_drawdown"] == 0.0
