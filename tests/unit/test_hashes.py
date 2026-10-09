from datetime import date
from pathlib import Path

import polars as pl

from aurum.adapters.storage.layout import DataLayout
from aurum.adapters.storage.live_ticks import LiveTickStore
from aurum.adapters.storage.parquet import write_parquet
from aurum.data.hashes import compare, manifest
from aurum.data.schemas import RAW_TICKS
from tests.unit.test_pipeline import DAY, raw_ticks


def seed(root: Path, ticks: pl.DataFrame) -> DataLayout:
    layout = DataLayout(root)
    write_parquet(ticks, layout.raw_ticks("XAUUSD", DAY), RAW_TICKS, overwrite=False)
    return layout


def test_identical_data_gives_identical_manifests(tmp_path: Path) -> None:
    a = manifest(seed(tmp_path / "a", raw_ticks()))
    b = manifest(seed(tmp_path / "b", raw_ticks()))
    assert list(a["files"]) == ["raw_ticks/mt5_history/XAUUSD/2026-09-16.parquet"]
    diff = compare(a, b)
    assert diff.ok
    assert diff.identical == 1
    assert set(a["versions"]) == {"python", "polars", "pyarrow", "duckdb"}


def test_differences_and_missing_files_are_reported(tmp_path: Path) -> None:
    left = seed(tmp_path / "a", raw_ticks())
    right = seed(tmp_path / "b", raw_ticks(10))
    write_parquet(
        raw_ticks(), left.raw_ticks("XAUUSD", date(2026, 9, 17)), RAW_TICKS, overwrite=False
    )
    diff = compare(manifest(left), manifest(right))
    assert not diff.ok
    assert diff.differ == ["raw_ticks/mt5_history/XAUUSD/2026-09-16.parquet"]
    assert diff.only_left == ["raw_ticks/mt5_history/XAUUSD/2026-09-17.parquet"]


def test_live_chunks_are_not_covered(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    LiveTickStore(layout, "XAUUSD").append(raw_ticks(5))
    assert manifest(layout)["files"] == {}
