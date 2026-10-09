from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from aurum.adapters.storage.layout import DataLayout, days_in
from aurum.adapters.storage.parquet import (
    empty_frame,
    read_parquet,
    remove_stale_temps,
    sha256_file,
    write_parquet,
)
from aurum.core.timeframes import Timeframe
from aurum.data.schemas import RAW_TICKS


def ticks(n: int = 3) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "time": [datetime(2026, 4, 1, 12, 0, 0, i * 1000, tzinfo=UTC) for i in range(n)],
            "bid": [4000.0 + i for i in range(n)],
            "ask": [4000.1 + i for i in range(n)],
            "last": [0.0] * n,
            "volume": [0] * n,
            "flags": [6] * n,
            "volume_real": [0.0] * n,
        }
    )


def test_identical_rows_give_identical_bytes(tmp_path: Path) -> None:
    a = write_parquet(ticks(), tmp_path / "a.parquet", RAW_TICKS, overwrite=False)
    b = write_parquet(ticks(), tmp_path / "b.parquet", RAW_TICKS, overwrite=False)
    assert sha256_file(a) == sha256_file(b)
    c = write_parquet(ticks(4), tmp_path / "c.parquet", RAW_TICKS, overwrite=False)
    assert sha256_file(a) != sha256_file(c)


def test_round_trip_keeps_schema_and_values(tmp_path: Path) -> None:
    path = write_parquet(ticks(), tmp_path / "t.parquet", RAW_TICKS, overwrite=False)
    back = read_parquet(path, RAW_TICKS)
    assert back.columns == RAW_TICKS.names
    assert back["time"].dtype == pl.Datetime("ms", "UTC")
    assert back["volume"].dtype == pl.UInt64
    assert back["bid"].to_list() == [4000.0, 4001.0, 4002.0]


def test_immutable_write_refuses_to_overwrite(tmp_path: Path) -> None:
    path = write_parquet(ticks(), tmp_path / "t.parquet", RAW_TICKS, overwrite=False)
    with pytest.raises(FileExistsError):
        write_parquet(ticks(5), path, RAW_TICKS, overwrite=False)
    assert read_parquet(path).height == 3
    assert not list(tmp_path.glob("*.tmp"))  # temp file cleaned up


def test_overwrite_replaces_atomically(tmp_path: Path) -> None:
    path = write_parquet(ticks(), tmp_path / "t.parquet", RAW_TICKS, overwrite=True)
    write_parquet(ticks(5), path, RAW_TICKS, overwrite=True)
    assert read_parquet(path).height == 5


def test_missing_column_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(pl.exceptions.ColumnNotFoundError):
        write_parquet(ticks().drop("flags"), tmp_path / "t.parquet", RAW_TICKS, overwrite=False)


def test_read_with_wrong_schema_is_an_error(tmp_path: Path) -> None:
    path = write_parquet(ticks(), tmp_path / "t.parquet", RAW_TICKS, overwrite=False)
    with pytest.raises(ValueError, match="expected"):
        read_parquet(path, RAW_TICKS.remove(0))


def test_empty_frame_has_schema(tmp_path: Path) -> None:
    path = write_parquet(empty_frame(RAW_TICKS), tmp_path / "e.parquet", RAW_TICKS, overwrite=False)
    assert read_parquet(path, RAW_TICKS).height == 0


def test_remove_stale_temps(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.parquet.123.tmp").write_bytes(b"partial")
    (tmp_path / "x" / "keep.parquet").write_bytes(b"ok")
    assert remove_stale_temps(tmp_path) == 1
    assert [p.name for p in (tmp_path / "x").iterdir()] == ["keep.parquet"]


def test_layout_paths(tmp_path: Path) -> None:
    lay = DataLayout(tmp_path)
    d = date(2026, 4, 1)
    assert (
        lay.raw_ticks("XAUUSD", d) == tmp_path / "raw_ticks/mt5_history/XAUUSD/2026-04-01.parquet"
    )
    assert lay.live_ticks_dir("XAUUSD", d) == tmp_path / "raw_ticks/mt5_live/XAUUSD/2026-04-01"
    assert lay.raw_bars("XAUUSD", Timeframe.M5, d) == (
        tmp_path / "raw_broker_bars/mt5/XAUUSD/M5/2026-04-01.parquet"
    )
    assert lay.canonical_bars("XAUUSD", Timeframe.H1, d) == (
        tmp_path / "canonical_bars/H1/XAUUSD/2026-04-01.parquet"
    )
    lay.ensure()
    assert (tmp_path / "features").is_dir()


def test_days_in_ignores_other_files(tmp_path: Path) -> None:
    for name in ("2026-04-02.parquet", "2026-04-01.parquet", "notes.txt", "2026-04-03"):
        (tmp_path / name).touch() if "." in name else (tmp_path / name).mkdir()
    assert days_in(tmp_path) == [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)]
    assert days_in(tmp_path / "missing") == []
