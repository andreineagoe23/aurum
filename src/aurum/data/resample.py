"""Deterministic bar building: normalized ticks -> M1 -> M5/M15/H1 (bid OHLC + spread stats).

Determinism: rows are sorted before grouping, first/last/min/max select existing values, and
every mean is an exact integer sum divided by an integer count. The same input therefore always
gives identical values, and identical Parquet bytes.

Bars exist only for intervals with at least one tick (like MT5), are keyed by UTC open time,
and ``close_time = open_time + duration``. Intervals are aligned to the Unix epoch, which for
M1..H1 equals alignment on the broker's whole-hour server clock (ADR-0003/0004).
"""

import polars as pl

from aurum.core.timeframes import Timeframe
from aurum.data.schemas import CANONICAL_BARS, polars_schema

TICK_FLAG_BID = 2
BROKER_M1_SOURCE = "mt5_bars_m1"
_SCHEMA = polars_schema(CANONICAL_BARS)


def _every(timeframe: Timeframe) -> str:
    return f"{timeframe.seconds}s"


def _finish(df: pl.DataFrame, timeframe: Timeframe) -> pl.DataFrame:
    return (
        df.with_columns(close_time=pl.col("open_time") + pl.duration(seconds=timeframe.seconds))
        .sort("open_time")
        .select(CANONICAL_BARS.names)
        .cast(_SCHEMA)
    )


def ticks_to_m1(ticks: pl.DataFrame) -> pl.DataFrame:
    """Normalized ticks (one source) -> canonical M1 bars."""
    if ticks.is_empty():
        return pl.DataFrame(schema=_SCHEMA)
    grouped = (
        ticks.sort("time", maintain_order=True)
        .with_columns(open_time=pl.col("time").dt.truncate(_every(Timeframe.M1)))
        .group_by("open_time", maintain_order=True)
        .agg(
            open=pl.col("bid").first(),
            high=pl.col("bid").max(),
            low=pl.col("bid").min(),
            close=pl.col("bid").last(),
            tick_count=((pl.col("flags") & TICK_FLAG_BID) > 0).sum().cast(pl.Int64),
            quote_count=pl.len().cast(pl.Int64),
            spread_open=pl.col("spread_points").first(),
            spread_min=pl.col("spread_points").min(),
            spread_max=pl.col("spread_points").max(),
            spread_sum=pl.col("spread_points").cast(pl.Int64).sum(),
            spread_close=pl.col("spread_points").last(),
            source=pl.col("source").first(),
        )
        .with_columns(spread_mean=pl.col("spread_sum") / pl.col("quote_count"))
    )
    return _finish(grouped, Timeframe.M1)


def broker_m1_to_canonical(raw: pl.DataFrame) -> pl.DataFrame:
    """Raw broker M1 bars (RAW_BARS) -> canonical M1, for days without tick history."""
    if raw.is_empty():
        return pl.DataFrame(schema=_SCHEMA)
    df = raw.sort("open_time").select(
        "open_time",
        "open",
        "high",
        "low",
        "close",
        tick_count=pl.col("tick_volume").cast(pl.Int64),
        quote_count=pl.lit(None, pl.Int64),
        spread_open=pl.lit(None, pl.Int32),
        spread_min=pl.col("spread").cast(pl.Int32),
        spread_max=pl.lit(None, pl.Int32),
        spread_mean=pl.lit(None, pl.Float64),
        spread_close=pl.lit(None, pl.Int32),
        source=pl.lit(BROKER_M1_SOURCE),
    )
    return _finish(df, Timeframe.M1)


def aggregate(m1: pl.DataFrame, timeframe: Timeframe) -> pl.DataFrame:
    """Canonical M1 -> canonical ``timeframe`` bars (M1 returns a sorted copy)."""
    if timeframe is Timeframe.M1 or m1.is_empty():
        return _finish(m1, Timeframe.M1) if not m1.is_empty() else pl.DataFrame(schema=_SCHEMA)
    # Exact integer spread sum per M1 bar: mean x count is an integer up to float rounding.
    spread_sum = (pl.col("spread_mean") * pl.col("quote_count")).round().cast(pl.Int64)
    grouped = (
        m1.sort("open_time")
        .with_columns(
            _spread_sum=spread_sum,
            open_time=pl.col("open_time").dt.truncate(_every(timeframe)),
        )
        .group_by("open_time", maintain_order=True)
        .agg(
            open=pl.col("open").first(),
            high=pl.col("high").max(),
            low=pl.col("low").min(),
            close=pl.col("close").last(),
            tick_count=pl.col("tick_count").sum(),
            quote_count=pl.col("quote_count").sum()
            if m1["quote_count"].null_count() == 0
            else pl.lit(None, pl.Int64),
            spread_open=pl.col("spread_open").first(),
            spread_min=pl.col("spread_min").min(),
            spread_max=pl.col("spread_max").max(),
            _spread_sum=pl.col("_spread_sum").sum(),
            spread_close=pl.col("spread_close").last(),
            source=pl.col("source").first(),
        )
        .with_columns(spread_mean=pl.col("_spread_sum") / pl.col("quote_count"))
    )
    return _finish(grouped, timeframe)
