"""No look-ahead (MASTER_PROMPT §4 rule 7): strategies only ever see bars with close_time <= now,
and multi-timeframe joins happen on close time."""

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest
from hypothesis import given
from hypothesis import strategies as st

from aurum.core.timeframes import Timeframe
from aurum.features.asof import asof_join
from aurum.features.view import BarSeries, BarStore, from_ms, to_ms
from tests.factories import canonical_bars

BASE = datetime(2026, 6, 29, 0, 0, tzinfo=UTC)


@st.composite
def bar_histories(draw: st.DrawFn, timeframe: Timeframe) -> pl.DataFrame:
    """Aligned bars with random gaps (missing bars), like real data around breaks."""
    n = draw(st.integers(min_value=0, max_value=60))
    steps = draw(st.lists(st.integers(min_value=1, max_value=4), min_size=n, max_size=n))
    times, k = [], 0
    for s in steps:
        k += s
        times.append(BASE + timeframe.duration * k)
    closes = draw(st.lists(st.floats(2000, 5000), min_size=n, max_size=n))
    return canonical_bars(timeframe, times, closes)


instants = st.integers(min_value=0, max_value=6 * 24 * 3600 * 1000).map(
    lambda ms: BASE + timedelta(milliseconds=ms)
)


@given(
    m5=bar_histories(Timeframe.M5),
    m15=bar_histories(Timeframe.M15),
    h1=bar_histories(Timeframe.H1),
    now=instants,
    count=st.one_of(st.none(), st.integers(min_value=0, max_value=10)),
)
def test_view_only_returns_bars_closed_by_now(
    m5: pl.DataFrame, m15: pl.DataFrame, h1: pl.DataFrame, now: datetime, count: int | None
) -> None:
    frames = {Timeframe.M5: m5, Timeframe.M15: m15, Timeframe.H1: h1}
    store = BarStore({tf: BarSeries.from_frame(tf, df) for tf, df in frames.items()})
    view = store.view_at(now)
    for tf, df in frames.items():
        got = view.bars(tf, count)
        closed = df.filter(pl.col("close_time") <= now)
        expected = closed if count is None else closed.tail(count)
        assert all(from_ms(int(c)) <= now for c in got.close_time)
        assert len(got) == expected.height
        if expected.height:
            # The newest completed bar is never withheld either.
            assert got.last_close_time == expected["close_time"][-1]
            assert np.array_equal(got.close, expected["close"].to_numpy())


@given(m5=bar_histories(Timeframe.M5), now=instants)
def test_view_arrays_are_read_only(m5: pl.DataFrame, now: datetime) -> None:
    series = BarStore({Timeframe.M5: BarSeries.from_frame(Timeframe.M5, m5)}).view_at(now)
    got = series.bars(Timeframe.M5)
    for arr in (got.close, got.high, got.close_time):
        assert not arr.flags.writeable


@given(m5=bar_histories(Timeframe.M5), h1=bar_histories(Timeframe.H1))
def test_asof_join_uses_only_closed_higher_bars(m5: pl.DataFrame, h1: pl.DataFrame) -> None:
    joined = asof_join(m5, h1, suffix="_h1")
    assert joined.height == m5.height
    h1_close = h1["close_time"].to_list()
    for row in joined.iter_rows(named=True):
        t = row["close_time"]
        eligible = [c for c in h1_close if c <= t]
        if eligible:
            # The newest H1 bar that had closed by the M5 close, never a forming one.
            assert row["close_time_h1"] == max(eligible)
        else:
            assert row["close_time_h1"] is None


def test_decision_at_10_35_sees_h1_closed_at_10_00() -> None:
    opens = [BASE.replace(hour=h) for h in range(8, 12)]
    store = BarStore(
        {
            Timeframe.H1: BarSeries.from_frame(
                Timeframe.H1, canonical_bars(Timeframe.H1, opens, [1.0, 2.0, 3.0, 4.0])
            )
        }
    )
    h1 = store.view_at(BASE.replace(hour=10, minute=35)).bars(Timeframe.H1)
    assert h1.last_close_time == BASE.replace(hour=10)
    assert h1.close[-1] == 2.0
    # Exactly at the close the bar becomes visible.
    assert store.view_at(BASE.replace(hour=11)).bars(Timeframe.H1).close[-1] == 3.0


def test_ms_round_trip() -> None:
    t = datetime(2026, 7, 1, 8, 0, 0, 123000, tzinfo=UTC)
    assert from_ms(to_ms(t)) == t


def test_from_frame_rejects_unsorted_or_misaligned() -> None:
    t = [BASE, BASE + timedelta(minutes=5)]
    df = canonical_bars(Timeframe.M5, t, [1.0, 2.0])
    with pytest.raises(ValueError, match="increasing"):
        BarSeries.from_frame(Timeframe.M5, df.reverse())
    with pytest.raises(ValueError, match="aligned"):
        BarSeries.from_frame(Timeframe.M15, df)


def test_asof_join_requires_sorted_close_time() -> None:
    df = canonical_bars(Timeframe.M5, [BASE, BASE + timedelta(minutes=5)], [1.0, 2.0])
    with pytest.raises(ValueError, match="sorted"):
        asof_join(df.reverse(), df, suffix="_x")
    with pytest.raises(ValueError, match="close_time"):
        asof_join(df.drop("close_time"), df, suffix="_x")
