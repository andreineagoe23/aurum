"""Resampling properties: ticks -> M1 -> higher timeframe equals ticks -> higher timeframe
directly, results are deterministic, and bars are aligned and keyed by open time."""

from datetime import UTC, datetime, timedelta

import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from aurum.core.timeframes import Timeframe
from aurum.data.resample import aggregate, ticks_to_m1
from aurum.data.schemas import NORMALIZED_TICKS, polars_schema

T0 = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)

ticks_strategy = st.lists(
    st.tuples(
        st.integers(0, 3 * 3600 * 1000 - 1),  # ms within three hours
        st.integers(400000, 400100),  # bid in cents
        st.integers(1, 60),  # spread points
        st.sampled_from([2, 4, 6, 130, 134]),  # flags
    ),
    min_size=1,
    max_size=300,
)


def frame(rows: list[tuple[int, int, int, int]]) -> pl.DataFrame:
    rows = sorted(rows, key=lambda r: r[0])
    return pl.DataFrame(
        {
            "time": [T0 + timedelta(milliseconds=ms) for ms, *_ in rows],
            "bid": [b / 100 for _, b, _, _ in rows],
            "ask": [(b + s) / 100 for _, b, s, _ in rows],
            "spread_points": [s for _, _, s, _ in rows],
            "flags": [f for *_, f in rows],
            "crossed": [False] * len(rows),
            "spread_outlier": [False] * len(rows),
            "source": ["mt5_history"] * len(rows),
        },
        schema=polars_schema(NORMALIZED_TICKS),
    )


def direct(ticks: pl.DataFrame, tf: Timeframe) -> pl.DataFrame:
    """Reference: build ``tf`` bars straight from ticks with the M1 code path's own rules."""
    widened = ticks.with_columns(pl.col("time").dt.truncate(f"{tf.seconds}s"))
    bars = ticks_to_m1(widened)  # every tick now falls in its tf bucket's first minute
    return bars.with_columns(close_time=pl.col("open_time") + pl.duration(seconds=tf.seconds))


@settings(max_examples=150)
@given(ticks_strategy, st.sampled_from([Timeframe.M5, Timeframe.M15, Timeframe.H1]))
def test_two_step_equals_direct(rows: list[tuple[int, int, int, int]], tf: Timeframe) -> None:
    ticks = frame(rows)
    two_step = aggregate(ticks_to_m1(ticks), tf)
    assert two_step.equals(direct(ticks, tf))


@given(ticks_strategy)
def test_deterministic_and_aligned(rows: list[tuple[int, int, int, int]]) -> None:
    ticks = frame(rows)
    for tf in Timeframe:
        a = aggregate(ticks_to_m1(ticks), tf)
        b = aggregate(ticks_to_m1(ticks), tf)
        assert a.equals(b)
        assert a["open_time"].is_sorted()
        assert all(tf.is_aligned(t) for t in a["open_time"].to_list())
        assert ((a["close_time"] - a["open_time"]) == timedelta(seconds=tf.seconds)).all()
        assert (a["high"] >= a["low"]).all()
        assert a["quote_count"].sum() == ticks.height


@given(ticks_strategy)
def test_every_tick_is_inside_its_bar(rows: list[tuple[int, int, int, int]]) -> None:
    ticks = frame(rows)
    m5 = aggregate(ticks_to_m1(ticks), Timeframe.M5)
    joined = ticks.join_asof(m5.sort("open_time"), left_on="time", right_on="open_time")
    assert (joined["time"] < joined["close_time"]).all()
    assert ((joined["bid"] >= joined["low"]) & (joined["bid"] <= joined["high"])).all()
