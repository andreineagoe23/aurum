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


def polars_schema(schema: pa.Schema) -> pl.Schema:
    """The polars equivalent of an Arrow schema above."""
    empty = pl.from_arrow(schema.empty_table())
    assert isinstance(empty, pl.DataFrame)
    return empty.schema
