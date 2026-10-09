from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from aurum.core.timeframes import Timeframe
from aurum.data.resample import BROKER_M1_SOURCE, aggregate, broker_m1_to_canonical, ticks_to_m1
from aurum.data.schemas import CANONICAL_BARS, NORMALIZED_TICKS, RAW_BARS, polars_schema

T = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


def ticks(rows: list[tuple[float, float, int, int]]) -> pl.DataFrame:
    """rows: (seconds after T, bid, spread points, flags)."""
    return pl.DataFrame(
        {
            "time": [T + timedelta(seconds=s) for s, *_ in rows],
            "bid": [b for _, b, _, _ in rows],
            "ask": [b + sp / 100 for _, b, sp, _ in rows],
            "spread_points": [sp for _, _, sp, _ in rows],
            "flags": [f for *_, f in rows],
            "crossed": [False] * len(rows),
            "spread_outlier": [False] * len(rows),
            "source": ["mt5_history"] * len(rows),
        },
        schema=polars_schema(NORMALIZED_TICKS),
    )


def test_m1_from_ticks_hand_computed() -> None:
    m1 = ticks_to_m1(
        ticks(
            [
                (0.1, 4000.00, 8, 134),
                (10.0, 4001.50, 6, 134),
                (20.0, 4001.50, 12, 4),  # ask-only change: a quote, not a bid tick
                (59.9, 3999.25, 10, 130),
                (61.0, 3999.30, 7, 134),
            ]
        )
    )
    assert m1.columns == CANONICAL_BARS.names
    first = m1.row(0, named=True)
    assert first["open_time"] == T
    assert first["close_time"] == T + timedelta(minutes=1)
    assert (first["open"], first["high"], first["low"], first["close"]) == (
        4000.00,
        4001.50,
        3999.25,
        3999.25,
    )
    assert (first["tick_count"], first["quote_count"]) == (3, 4)
    assert (first["spread_open"], first["spread_min"], first["spread_max"]) == (8, 6, 12)
    assert first["spread_close"] == 10
    assert first["spread_mean"] == pytest.approx(9.0)
    assert m1.height == 2


def test_m5_weights_spread_mean_by_quotes() -> None:
    m1 = ticks_to_m1(ticks([(0, 4000.0, 10, 134), (60, 4000.0, 20, 134), (61, 4000.0, 20, 134)]))
    m5 = aggregate(m1, Timeframe.M5)
    assert m5.height == 1
    assert m5["spread_mean"][0] == pytest.approx(50 / 3)
    assert m5["quote_count"][0] == 3


def test_broker_m1_keeps_only_min_spread() -> None:
    raw = pl.DataFrame(
        {
            "open_time": [T, T + timedelta(minutes=1)],
            "open": [4000.0, 4001.0],
            "high": [4001.0, 4002.0],
            "low": [3999.0, 4000.0],
            "close": [4001.0, 4001.5],
            "tick_volume": [120, 80],
            "spread": [5, 6],
            "real_volume": [0, 0],
        },
        schema=polars_schema(RAW_BARS),
    )
    m1 = broker_m1_to_canonical(raw)
    assert m1["spread_min"].to_list() == [5, 6]
    assert m1["spread_mean"].null_count() == 2
    assert m1["source"].unique().to_list() == [BROKER_M1_SOURCE]
    m5 = aggregate(m1, Timeframe.M5)
    row = m5.row(0, named=True)
    assert (row["open"], row["high"], row["low"], row["close"]) == (4000.0, 4002.0, 3999.0, 4001.5)
    assert (row["tick_count"], row["quote_count"], row["spread_min"]) == (200, None, 5)
    assert row["spread_mean"] is None


def test_empty_inputs_give_empty_frames_with_schema() -> None:
    empty = ticks([])
    assert ticks_to_m1(empty).columns == CANONICAL_BARS.names
    assert aggregate(ticks_to_m1(empty), Timeframe.H1).height == 0
    assert broker_m1_to_canonical(pl.DataFrame(schema=polars_schema(RAW_BARS))).height == 0
