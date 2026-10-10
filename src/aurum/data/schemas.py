"""Arrow schemas of every stored dataset: the single source of truth for column names, types
and order (docs/DATA_DICTIONARY.md mirrors this file).

Conventions: times are ``timestamp[ms, UTC]``; prices are USD/oz (``float64``); spreads are
integer *points* (multiples of ``SymbolSpec.point``); bars are keyed by open time and cover
``[open_time, close_time)``; bar OHLC is bid.
"""

import polars as pl
import pyarrow as pa

UTC_MS = pa.timestamp("ms", tz="UTC")

RAW_TICKS = pa.schema(
    [
        ("time", UTC_MS),
        ("bid", pa.float64()),
        ("ask", pa.float64()),
        ("last", pa.float64()),
        ("volume", pa.uint64()),
        ("flags", pa.uint32()),
        ("volume_real", pa.float64()),
    ]
)
"""MT5 ``copy_ticks_range(COPY_TICKS_ALL)`` rows exactly as received, in received order, with
``time`` = ``time_msc`` converted from server clock to UTC (ADR-0004). Nothing is dropped."""

RAW_BARS = pa.schema(
    [
        ("open_time", UTC_MS),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("tick_volume", pa.uint64()),
        ("spread", pa.int32()),
        ("real_volume", pa.uint64()),
    ]
)
"""MT5 ``copy_rates_range`` rows (completed bars only) with ``open_time`` converted to UTC."""

NORMALIZED_TICKS = pa.schema(
    [
        ("time", UTC_MS),
        ("bid", pa.float64()),
        ("ask", pa.float64()),
        ("spread_points", pa.int32()),
        ("flags", pa.uint32()),
        ("crossed", pa.bool_()),
        ("spread_outlier", pa.bool_()),
        ("source", pa.string()),
    ]
)
"""Ticks after normalisation (aurum.data.normalize): sorted by time (stable), exact duplicates
and rows without a positive bid and ask removed, quality flags added."""

CANONICAL_BARS = pa.schema(
    [
        ("open_time", UTC_MS),
        ("close_time", UTC_MS),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("tick_count", pa.int64()),
        ("quote_count", pa.int64()),
        ("spread_open", pa.int32()),
        ("spread_min", pa.int32()),
        ("spread_max", pa.int32()),
        ("spread_mean", pa.float64()),
        ("spread_close", pa.int32()),
        ("source", pa.string()),
    ]
)
"""Bid OHLC bars built deterministically by aurum.data.resample: ticks -> M1 -> M5/M15/H1, or,
for days without tick history, broker M1 -> M5/M15/H1.

* ``tick_count``: ticks that changed the bid (MT5 ``tick_volume`` convention, flag bit 2).
* ``quote_count``: all ticks (bid or ask changed); weight of ``spread_mean``. Null for bars
  built from broker M1.
* Spreads are points. Bars built from broker M1 only have ``spread_min`` (the broker's bar
  ``spread`` field is the minimum spread in the bar, measured in Phase 2); the other spread
  columns are null there.
"""

DAY_SOURCES = pa.schema(
    [
        ("day", pa.date32()),
        ("m1_source", pa.string()),
        ("tick_source", pa.string()),
        ("tick_rows", pa.int64()),
        ("tick_fidelity", pa.string()),
        ("tick_m1_mismatch", pa.int64()),
        ("broker_only_minutes", pa.int64()),
        ("tick_only_minutes", pa.int64()),
    ]
)
"""One row per UTC day built by aurum.data.pipeline: where its canonical bars came from and
whether its stored ticks reproduce the broker's M1 (``tick_fidelity``, ADR-0007)."""


def polars_schema(schema: pa.Schema) -> pl.Schema:
    """The polars equivalent of an Arrow schema above."""
    empty = pl.from_arrow(schema.empty_table())
    assert isinstance(empty, pl.DataFrame)
    return empty.schema


# ------------------------------------------------------------------------------ backtest runs
# Written under reports/runs/<run_id>/ by aurum.backtest.storage. Money is account currency
# (USD); costs are positive numbers; ``net_pnl`` is what the broker booked (profit +
# commission, cents) and ``gross_pnl = net_pnl + spread_cost + slippage_cost + commission``.

BACKTEST_TRADES = pa.schema(
    [
        ("trade_no", pa.int32()),
        ("position_id", pa.int64()),
        ("idempotency_key", pa.string()),
        ("strategy_id", pa.string()),
        ("side", pa.string()),
        ("volume", pa.float64()),
        ("entry_type", pa.string()),
        ("signal_time", UTC_MS),
        ("entry_time", UTC_MS),
        ("entry_price", pa.float64()),
        ("entry_bid", pa.float64()),
        ("entry_ask", pa.float64()),
        ("entry_mode", pa.string()),
        ("exit_time", UTC_MS),
        ("exit_price", pa.float64()),
        ("exit_bid", pa.float64()),
        ("exit_ask", pa.float64()),
        ("exit_mode", pa.string()),
        ("exit_reason", pa.string()),
        ("stop_loss", pa.float64()),
        ("take_profit", pa.float64()),
        ("holding_seconds", pa.float64()),
        ("gross_pnl", pa.float64()),
        ("spread_cost", pa.float64()),
        ("slippage_cost", pa.float64()),
        ("commission", pa.float64()),
        ("net_pnl", pa.float64()),
        ("mae_points", pa.float64()),
        ("mfe_points", pa.float64()),
        ("intrabar_rule", pa.string()),
        ("worst_case", pa.bool_()),
    ]
)
"""One row per closed position, in closing order.

* ``*_bid``/``*_ask``: reference quote of each fill (see ``aurum.backtest.broker.SimTrade``).
* ``*_mode``: ``ticks`` (real quotes) or ``bars`` (M1 path, ADR-0007).
* ``exit_reason``: STOP, TARGET, INVALIDATION, TIMEOUT, FORCE_FLAT, END_OF_DATA.
* ``mae_points``/``mfe_points``: largest adverse/favourable move of the exit-side price (bid for
  BUY, ask for SELL) from the entry price while open, in points.
* ``intrabar_rule``/``worst_case``: whether a BARS-mode assumption decided the outcome
  (``sl_tp_same_bar``: stop assumed before target; ``entry_bar``: entry/exit order assumed;
  ``close_bar``: a requested close and a level in one bar; ``cancel_bar``: a pending entry
  assumed filled before a cancel arriving in the same bar). ADR-0009 §5.
"""

BACKTEST_EQUITY = pa.schema(
    [
        ("time", UTC_MS),
        ("balance", pa.float64()),
        ("equity", pa.float64()),
        ("open_positions", pa.int32()),
    ]
)
"""Account balance (closed trades) and equity (balance + open P&L) at every engine wake-up."""

BACKTEST_JOURNAL = pa.schema(
    [
        ("time", UTC_MS),
        ("event", pa.string()),
        ("phase", pa.string()),
        ("detail", pa.string()),
        ("idempotency_key", pa.string()),
        ("position_id", pa.int64()),
        ("order_id", pa.int64()),
        ("exit_reason", pa.string()),
    ]
)
"""Every engine event: session transitions, signals, risk rejections, orders, closes, cancels."""
