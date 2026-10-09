"""Per-trade P&L with cost attribution, equity curve and summary metrics (account currency).

Attribution per trade (positive numbers are costs):

* ``spread_cost``: half the spread of the entry quote plus half the spread of the exit quote,
  x volume x contract value. Paying the ask and receiving the bid costs exactly this relative
  to trading at mid.
* ``slippage_cost``: adverse distance between each fill and its quote's executable side.
* ``commission``: both sides, as booked.
* ``net_pnl``: booked profit + booked commission (cents, like the broker's deals).
* ``gross_pnl = net_pnl + spread_cost + slippage_cost + commission``, i.e. the mid-to-mid
  result (up to the broker's rounding to cents).
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

import polars as pl

from aurum.backtest.broker import IntrabarRule, SimTrade
from aurum.backtest.runner import BacktestResult, EquityPoint
from aurum.core.enums import DealReason, ExitReason, Side
from aurum.core.models import SymbolSpec
from aurum.data.schemas import (
    BACKTEST_EQUITY,
    BACKTEST_JOURNAL,
    BACKTEST_TRADES,
    polars_schema,
)
from aurum.execution.engine import JournalEntry

_BROKER_EXITS = {
    DealReason.STOP_LOSS: ExitReason.STOP,
    DealReason.TAKE_PROFIT: ExitReason.TARGET,
}


def exit_reason(trade: SimTrade, engine_reasons: Mapping[int, ExitReason]) -> ExitReason:
    """STOP/TARGET from the broker's deal reason, otherwise why the engine closed it."""
    if trade.exit_reason in _BROKER_EXITS:
        return _BROKER_EXITS[trade.exit_reason]
    try:
        return engine_reasons[trade.position_id]
    except KeyError:
        raise ValueError(
            f"position {trade.position_id} closed by {trade.exit_reason} without an engine reason"
        ) from None


def trade_row(
    n: int,
    trade: SimTrade,
    reason: ExitReason,
    spec: SymbolSpec,
    strategy_id: str,
    signal_time: Any,
) -> dict[str, Any]:
    value = spec.money_per_price_unit_per_lot * trade.volume
    buy = trade.side is Side.BUY
    spread = ((trade.entry_ask - trade.entry_bid) + (trade.exit_ask - trade.exit_bid)) / 2
    slip_in = trade.entry_price - trade.entry_ask if buy else trade.entry_bid - trade.entry_price
    slip_out = trade.exit_bid - trade.exit_price if buy else trade.exit_price - trade.exit_ask
    spread_cost = spread * value
    slippage_cost = (slip_in + slip_out) * value
    commission = -trade.commission
    net = trade.profit + trade.commission
    if buy:
        mae, mfe = trade.entry_price - trade.worst, trade.best - trade.entry_price
    else:
        mae, mfe = trade.worst - trade.entry_price, trade.entry_price - trade.best
    return {
        "trade_no": n,
        "position_id": trade.position_id,
        "idempotency_key": trade.idempotency_key,
        "strategy_id": strategy_id,
        "side": str(trade.side),
        "volume": trade.volume,
        "entry_type": str(trade.entry_type),
        "signal_time": signal_time,
        "entry_time": trade.entry_time,
        "entry_price": trade.entry_price,
        "entry_bid": trade.entry_bid,
        "entry_ask": trade.entry_ask,
        "entry_mode": str(trade.entry_mode),
        "exit_time": trade.exit_time,
        "exit_price": trade.exit_price,
        "exit_bid": trade.exit_bid,
        "exit_ask": trade.exit_ask,
        "exit_mode": str(trade.exit_mode),
        "exit_reason": str(reason),
        "stop_loss": trade.stop_loss,
        "take_profit": trade.take_profit,
        "holding_seconds": (trade.exit_time - trade.entry_time).total_seconds(),
        "gross_pnl": round(net + spread_cost + slippage_cost + commission, 6),
        "spread_cost": round(spread_cost, 6),
        "slippage_cost": round(slippage_cost, 6),
        "commission": round(commission, 2),
        "net_pnl": round(net, 2),
        "mae_points": round(spec.price_to_points(mae), 4),
        "mfe_points": round(spec.price_to_points(mfe), 4),
        "intrabar_rule": str(trade.intrabar),
        "worst_case": trade.intrabar is not IntrabarRule.NONE,
    }


def trades_frame(result: BacktestResult, spec: SymbolSpec, strategy_id: str) -> pl.DataFrame:
    rows = []
    for n, trade in enumerate(result.trades, start=1):
        signal = result.signals.get(trade.idempotency_key or "")
        rows.append(
            trade_row(
                n,
                trade,
                exit_reason(trade, result.exit_reasons),
                spec,
                strategy_id,
                signal.time if signal else None,
            )
        )
    return pl.DataFrame(rows, schema=polars_schema(BACKTEST_TRADES))


def equity_frame(points: Sequence[EquityPoint]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "time": p.time,
                "balance": p.balance,
                "equity": round(p.equity, 6),
                "open_positions": p.open_positions,
            }
            for p in points
        ],
        schema=polars_schema(BACKTEST_EQUITY),
    )


def journal_frame(entries: Sequence[JournalEntry]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "time": e.time,
                "event": str(e.event),
                "phase": str(e.phase),
                "detail": e.detail,
                "idempotency_key": e.idempotency_key,
                "position_id": e.position_id,
                "order_id": e.order_id,
                "exit_reason": str(e.exit_reason) if e.exit_reason else None,
            }
            for e in entries
        ],
        schema=polars_schema(BACKTEST_JOURNAL),
    )


def _max_drawdown(equity: pl.Series) -> tuple[float, float]:
    """Largest peak-to-trough fall of ``equity`` in money and as a fraction of the peak."""
    if equity.is_empty():
        return 0.0, 0.0
    peak = equity.cum_max()
    dd = peak - equity
    i = int(dd.arg_max() or 0)
    worst = float(dd[i])
    return worst, (worst / float(peak[i]) if peak[i] else 0.0)


def summarize(
    trades: pl.DataFrame, equity: pl.DataFrame, initial_balance: float
) -> dict[str, float | int | None]:
    """Headline numbers for the run report and the trial ledger (every run, good or bad)."""
    n = trades.height
    net = trades["net_pnl"]
    wins = trades.filter(pl.col("net_pnl") > 0)["net_pnl"]
    losses = trades.filter(pl.col("net_pnl") < 0)["net_pnl"]
    dd_money, dd_frac = _max_drawdown(equity["equity"])
    worst = int(trades["worst_case"].sum()) if n else 0
    reasons = Counter(trades["exit_reason"].to_list())
    rules = Counter(trades["intrabar_rule"].to_list())

    def total(col: str) -> float:
        return round(float(trades[col].sum()), 2) if n else 0.0

    out: dict[str, float | int | None] = {
        "trades": n,
        "long": int((trades["side"] == "BUY").sum()) if n else 0,
        "short": int((trades["side"] == "SELL").sum()) if n else 0,
        "win_rate": round(wins.len() / n, 4) if n else None,
        "net_pnl": total("net_pnl"),
        "gross_pnl": total("gross_pnl"),
        "spread_cost": total("spread_cost"),
        "slippage_cost": total("slippage_cost"),
        "commission": total("commission"),
        "expectancy": round(float(net.mean()), 4) if n else None,  # type: ignore[arg-type]
        "profit_factor": (
            round(float(wins.sum()) / -float(losses.sum()), 4) if losses.len() else None
        ),
        "max_drawdown": round(dd_money, 2),
        "max_drawdown_pct": round(100 * dd_frac, 4),
        "return_pct": round(100 * total("net_pnl") / initial_balance, 4),
        "avg_holding_minutes": (
            round(float(trades["holding_seconds"].mean()) / 60, 2) if n else None  # type: ignore[arg-type]
        ),
        "trades_tick_mode": int((trades["entry_mode"] == "ticks").sum()) if n else 0,
        "trades_bar_mode": int((trades["entry_mode"] == "bars").sum()) if n else 0,
        "worst_case_trades": worst,
        "worst_case_share": round(worst / n, 4) if n else None,
        "intrabar_sl_tp_same_bar": rules.get(str(IntrabarRule.SL_TP_SAME_BAR), 0),
        "intrabar_entry_bar": rules.get(str(IntrabarRule.ENTRY_BAR), 0),
    }
    for reason in ExitReason:
        out[f"exit_{reason.value.lower()}"] = reasons.get(str(reason), 0)
    return out
