from datetime import UTC, datetime, timedelta

import polars as pl
from hypothesis import given
from hypothesis import strategies as st

from aurum.data.normalize import normalize_ticks
from aurum.data.schemas import NORMALIZED_TICKS, RAW_TICKS, polars_schema

T = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


def raw(rows: list[tuple[int, float, float]], flags: int = 6) -> pl.DataFrame:
    """rows: (ms offset from T, bid, ask)."""
    return pl.DataFrame(
        {
            "time": [T + timedelta(milliseconds=ms) for ms, _, _ in rows],
            "bid": [b for _, b, _ in rows],
            "ask": [a for _, _, a in rows],
            "last": [0.0] * len(rows),
            "volume": [0] * len(rows),
            "flags": [flags] * len(rows),
            "volume_real": [0.0] * len(rows),
        },
        schema=polars_schema(RAW_TICKS),
    )


def norm(df: pl.DataFrame, factor: float = 5.0) -> tuple[pl.DataFrame, object]:
    return normalize_ticks(df, point=0.01, source="mt5_history", outlier_factor=factor)


def test_schema_and_spread_points() -> None:
    out, stats = norm(raw([(0, 4000.00, 4000.08), (5, 4000.01, 4000.10)]))
    assert out.columns == NORMALIZED_TICKS.names
    assert out["spread_points"].to_list() == [8, 9]
    assert out["source"].unique().to_list() == ["mt5_history"]
    assert stats.kept == 2  # type: ignore[attr-defined]
    empty, empty_stats = norm(raw([]))
    assert empty.height == 0
    assert empty.columns == NORMALIZED_TICKS.names
    assert empty_stats.median_spread_points is None  # type: ignore[attr-defined]


def test_invalid_rows_dropped_and_counted() -> None:
    out, stats = norm(
        raw([(0, 4000.0, 4000.1), (1, 0.0, 4000.1), (2, 4000.0, -1.0), (3, float("nan"), 4000.1)])
    )
    assert out.height == 1
    assert stats.invalid == 3  # type: ignore[attr-defined]


def test_out_of_order_sorted_stably_and_counted() -> None:
    out, stats = norm(raw([(10, 4000.3, 4000.4), (5, 4000.1, 4000.2), (5, 4000.2, 4000.3)]))
    assert out["bid"].to_list() == [4000.1, 4000.2, 4000.3]  # same-ms rows keep received order
    assert stats.out_of_order == 2  # type: ignore[attr-defined]


def test_only_consecutive_exact_duplicates_dropped() -> None:
    a, b = (5, 4000.1, 4000.2), (5, 4000.2, 4000.3)
    out, stats = norm(raw([a, a, b, a]))
    assert out["bid"].to_list() == [4000.1, 4000.2, 4000.1]  # A, B, A flicker kept
    assert stats.duplicates == 1  # type: ignore[attr-defined]


def test_crossed_and_outlier_flags_keep_rows() -> None:
    rows = [(i, 4000.0, 4000.08) for i in range(10)] + [(20, 4000.0, 4000.60), (21, 4000.1, 4000.0)]
    out, stats = norm(raw(rows))
    assert out.height == 12
    assert out["spread_outlier"].to_list()[-2:] == [True, False]  # 60 > 5 x 8
    assert out["crossed"].to_list()[-2:] == [False, True]
    assert (stats.spread_outliers, stats.crossed) == (1, 1)  # type: ignore[attr-defined]
    # The outlier factor is configurable: a 50-point spread against a 10-point median.
    rows = [(i, 4000.0, 4000.10) for i in range(5)] + [(9, 4000.0, 4000.50)]
    for factor in (2.0, 10.0):
        out, _ = norm(raw(rows), factor)
        assert out["spread_outlier"].to_list()[-1] is (factor * 10 < 50), factor


tick_rows = st.lists(
    st.tuples(
        st.integers(0, 2000),
        st.sampled_from([4000.0, 4000.01, 4000.02, 0.0]),
        st.sampled_from([4000.05, 4000.1, 4003.0]),
    ),
    max_size=60,
)


@given(tick_rows)
def test_deterministic_sorted_and_idempotent(rows: list[tuple[int, float, float]]) -> None:
    first, _ = norm(raw(rows))
    again, _ = norm(raw(rows))
    assert first.equals(again)
    assert first["time"].is_sorted()
    assert (first["bid"] > 0).all()
    # Normalising already-normalised ticks changes nothing.
    back = first.select("time", "bid", "ask", "flags").with_columns(
        last=pl.lit(0.0), volume=pl.lit(0, pl.UInt64), volume_real=pl.lit(0.0)
    )
    twice, stats = norm(back.select(RAW_TICKS.names))
    assert twice.equals(first)
    assert stats.duplicates == stats.invalid == stats.out_of_order == 0  # type: ignore[attr-defined]
