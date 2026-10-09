from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from aurum.adapters.storage.layout import DataLayout
from aurum.adapters.storage.live_ticks import LiveTickStore
from aurum.adapters.storage.parquet import read_parquet, sha256_file, write_parquet
from aurum.core.timeframes import Timeframe
from aurum.data.compare import compare_bars
from aurum.data.normalize import normalize_ticks
from aurum.data.pipeline import available_days, build_day, build_range
from aurum.data.resample import BROKER_M1_SOURCE, aggregate, ticks_to_m1
from aurum.data.schemas import RAW_BARS, RAW_TICKS, polars_schema
from aurum.data.summary import summarize_build

DAY = date(2026, 9, 16)
T = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)


def raw_ticks(n: int = 600, step_ms: int = 900, start: datetime = T) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "time": [start + timedelta(milliseconds=i * step_ms) for i in range(n)],
            "bid": [4000.0 + ((i * 7) % 23) * 0.01 for i in range(n)],
            "ask": [4000.08 + ((i * 7) % 23) * 0.01 for i in range(n)],
            "last": [0.0] * n,
            "volume": [0] * n,
            "flags": [134 if i % 9 else 4 for i in range(n)],
            "volume_real": [0.0] * n,
        },
        schema=polars_schema(RAW_TICKS),
    )


def broker_bars_from(ticks: pl.DataFrame, tf: Timeframe) -> pl.DataFrame:
    """What MT5 would report for these ticks: bid OHLC, bid-tick volume, minimum spread."""
    norm, _ = normalize_ticks(ticks, point=0.01, source="x", outlier_factor=5)
    bars = aggregate(ticks_to_m1(norm), tf)
    return bars.select(
        "open_time",
        "open",
        "high",
        "low",
        "close",
        tick_volume=pl.col("tick_count").cast(pl.UInt64),
        spread=pl.col("spread_min"),
        real_volume=pl.lit(0, pl.UInt64),
    )


@pytest.fixture
def layout(tmp_path: Path) -> DataLayout:
    return DataLayout(tmp_path)


def build(layout: DataLayout, day: date = DAY):  # type: ignore[no-untyped-def]
    return build_day(layout, "XAUUSD", day, point=0.01, outlier_factor=5.0)


def test_tick_day_builds_normalized_and_canonical_and_matches_broker(layout: DataLayout) -> None:
    ticks = raw_ticks()
    write_parquet(ticks, layout.raw_ticks("XAUUSD", DAY), RAW_TICKS, overwrite=False)
    for tf in Timeframe:
        write_parquet(
            broker_bars_from(ticks, tf),
            layout.raw_bars("XAUUSD", tf, DAY),
            RAW_BARS,
            overwrite=False,
        )
    result = build(layout)
    assert result.source == "mt5_history"
    assert result.tick_fidelity == "exact"
    assert result.ticks is not None
    assert result.ticks.kept == 600
    assert read_parquet(layout.normalized_ticks("XAUUSD", DAY)).height == 600
    assert result.bars == {"M1": 9, "M5": 2, "M15": 1, "H1": 1}
    for tf, c in result.comparisons.items():
        assert (c.matched, c.ohlc_mismatch, c.only_broker, c.only_canonical) == (
            result.bars[tf],
            0,
            0,
            0,
        ), tf
        assert c.tick_count_mismatch == c.spread_min_mismatch == 0


def test_rebuild_is_byte_identical(layout: DataLayout) -> None:
    write_parquet(raw_ticks(), layout.raw_ticks("XAUUSD", DAY), RAW_TICKS, overwrite=False)
    build(layout)
    files = sorted(p for p in layout.root.rglob("*.parquet") if "raw_" not in str(p))
    before = {p: sha256_file(p) for p in files}
    build(layout)
    assert {p: sha256_file(p) for p in files} == before


def test_day_without_ticks_uses_broker_m1(layout: DataLayout) -> None:
    m1 = broker_bars_from(raw_ticks(), Timeframe.M1)
    write_parquet(m1, layout.raw_bars("XAUUSD", Timeframe.M1, DAY), RAW_BARS, overwrite=False)
    result = build(layout)
    assert result.source == BROKER_M1_SOURCE
    assert result.ticks is None
    canonical = read_parquet(layout.canonical_bars("XAUUSD", Timeframe.M5, DAY))
    assert canonical["source"].unique().to_list() == [BROKER_M1_SOURCE]
    assert canonical["spread_mean"].null_count() == canonical.height


def test_live_ticks_used_only_without_history(layout: DataLayout) -> None:
    LiveTickStore(layout, "XAUUSD").append(raw_ticks(10))
    assert build(layout).source == "mt5_live"
    write_parquet(raw_ticks(20), layout.raw_ticks("XAUUSD", DAY), RAW_TICKS, overwrite=False)
    result = build(layout)
    assert result.source == "mt5_history"
    assert result.ticks is not None
    assert result.ticks.kept == 20


def test_empty_holiday_writes_empty_bars_and_available_days(layout: DataLayout) -> None:
    empty = pl.DataFrame(schema=polars_schema(RAW_BARS))
    write_parquet(
        empty, layout.raw_bars("XAUUSD", Timeframe.M1, date(2026, 4, 3)), RAW_BARS, overwrite=False
    )
    write_parquet(raw_ticks(), layout.raw_ticks("XAUUSD", DAY), RAW_TICKS, overwrite=False)
    assert available_days(layout, "XAUUSD") == [date(2026, 4, 3), DAY]
    results = build_range(
        layout, "XAUUSD", available_days(layout, "XAUUSD"), point=0.01, outlier_factor=5
    )
    assert results[0].source is None
    assert read_parquet(layout.canonical_bars("XAUUSD", Timeframe.H1, date(2026, 4, 3))).height == 0
    summary = summarize_build(results)
    assert summary["days_by_source"] == {"none": 1, "mt5_history": 1}


def test_compare_reports_disagreements() -> None:
    ticks = raw_ticks()
    norm, _ = normalize_ticks(ticks, point=0.01, source="x", outlier_factor=5)
    ours = ticks_to_m1(norm)
    broker = broker_bars_from(ticks, Timeframe.M1)
    broker = broker.with_columns(
        high=pl.when(pl.col("open_time") == T).then(pl.col("high") + 0.25).otherwise(pl.col("high"))
    ).filter(pl.col("open_time") != T + timedelta(minutes=8))
    extra = broker.head(1).with_columns(
        open_time=pl.lit(T - timedelta(minutes=1)).cast(pl.Datetime("ms", "UTC"))
    )
    c = compare_bars(ours, pl.concat([extra, broker]), point=0.01)
    assert (c.matched, c.only_canonical, c.only_broker) == (8, 1, 1)
    assert c.ohlc_mismatch == 1
    assert c.max_diff_points["high"] == 25
    assert c.worst[0]["open_time"] == T.isoformat()


def test_ticks_that_do_not_reproduce_broker_m1_yield_to_it(layout: DataLayout) -> None:
    ticks = raw_ticks()
    full_m1 = broker_bars_from(ticks, Timeframe.M1)
    thinned = ticks.filter(pl.col("time") < T + timedelta(minutes=8))  # last minute missing
    write_parquet(thinned, layout.raw_ticks("XAUUSD", DAY), RAW_TICKS, overwrite=False)
    write_parquet(full_m1, layout.raw_bars("XAUUSD", Timeframe.M1, DAY), RAW_BARS, overwrite=False)
    [result] = build_range(layout, "XAUUSD", [DAY], point=0.01, outlier_factor=5)
    assert result.tick_fidelity == "incomplete"
    assert result.source == BROKER_M1_SOURCE
    assert result.tick_vs_broker is not None
    assert result.tick_vs_broker.only_broker == 1
    assert result.comparisons["M1"].ohlc_mismatch == 0  # canonical now equals the broker
    assert read_parquet(layout.normalized_ticks("XAUUSD", DAY)).height == thinned.height
    manifest = read_parquet(layout.day_manifest("XAUUSD"))
    row = manifest.row(0, named=True)
    assert row["day"] == DAY
    assert (row["m1_source"], row["tick_fidelity"], row["broker_only_minutes"]) == (
        BROKER_M1_SOURCE,
        "incomplete",
        1,
    )
    summary = summarize_build([result])
    assert summary["tick_fidelity"]["incomplete"]["days"] == 1


def test_manifest_merges_partial_rebuilds(layout: DataLayout) -> None:
    other = date(2026, 9, 17)
    write_parquet(raw_ticks(), layout.raw_ticks("XAUUSD", DAY), RAW_TICKS, overwrite=False)
    write_parquet(
        raw_ticks(start=T + timedelta(days=1)),
        layout.raw_ticks("XAUUSD", other),
        RAW_TICKS,
        overwrite=False,
    )
    build_range(layout, "XAUUSD", [DAY, other], point=0.01, outlier_factor=5)
    before = sha256_file(layout.day_manifest("XAUUSD"))
    build_range(layout, "XAUUSD", [other], point=0.01, outlier_factor=5)
    manifest = read_parquet(layout.day_manifest("XAUUSD"))
    assert manifest["day"].to_list() == [DAY, other]
    assert manifest["tick_fidelity"].to_list() == ["unverified", "unverified"]
    assert sha256_file(layout.day_manifest("XAUUSD")) == before
