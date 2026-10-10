import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from aurum.adapters.mt5.snapshot import contract_fingerprint, snapshot_if_changed
from aurum.adapters.storage.layout import DataLayout
from aurum.adapters.storage.live_ticks import (
    ChunkName,
    CollectorJournal,
    LiveTickStore,
    TickCursor,
)
from aurum.core.models import SymbolSpec

T = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


def frame(times: list[datetime], bid: float = 4000.0) -> pl.DataFrame:
    n = len(times)
    return pl.DataFrame(
        {
            "time": pl.Series(times, dtype=pl.Datetime("ms", "UTC")),
            "bid": [bid + i * 0.01 for i in range(n)],
            "ask": [bid + 0.09 + i * 0.01 for i in range(n)],
            "last": [0.0] * n,
            "volume": pl.Series([0] * n, dtype=pl.UInt64),
            "flags": pl.Series([6] * n, dtype=pl.UInt32),
            "volume_real": [0.0] * n,
        }
    )


@pytest.fixture
def store(tmp_path: Path) -> LiveTickStore:
    return LiveTickStore(DataLayout(tmp_path), "XAUUSD")


def test_cursor_counts_rows_sharing_the_last_millisecond_across_chunks(
    store: LiveTickStore,
) -> None:
    assert store.tail() is None  # empty store
    store.append(frame([T, T + timedelta(milliseconds=5), T + timedelta(milliseconds=5)]))
    store.append(frame([T + timedelta(milliseconds=5)], bid=4001.0))
    assert store.tail() == TickCursor(time=T + timedelta(milliseconds=5), rows_at_time=3)


def test_chunks_are_immutable_and_named_by_range(store: LiveTickStore) -> None:
    [path] = store.append(frame([T, T + timedelta(seconds=1)]))
    name = ChunkName.parse(path)
    assert name is not None
    assert (name.last_ms - name.first_ms, name.rows) == (1000, 2)
    with pytest.raises(FileExistsError):
        store.append(frame([T, T + timedelta(seconds=1)]))


def test_append_splits_by_utc_day_and_read_day_keeps_order(store: LiveTickStore) -> None:
    late = datetime(2026, 10, 7, 23, 59, 59, tzinfo=UTC)
    store.append(frame([late, late + timedelta(seconds=2)]))
    assert store.days() == [date(2026, 10, 7), date(2026, 10, 8)]
    store.append(frame([late + timedelta(seconds=3)], bid=4100.0))
    assert store.read_day(date(2026, 10, 8))["bid"].to_list() == [4000.01, 4100.0]
    assert store.tail() == TickCursor(time=late + timedelta(seconds=3), rows_at_time=1)
    (store.layout.live_ticks_dir("XAUUSD", late.date()) / "notes.txt").write_text("x")
    assert len(store.chunks(late.date())) == 1  # foreign files are ignored
    assert store.read_day(date(2026, 1, 1)).height == 0


def test_journal_events_and_heartbeat(tmp_path: Path) -> None:
    journal = CollectorJournal(DataLayout(tmp_path), "XAUUSD")
    assert journal.read_heartbeat() is None
    journal.event("gap", T, start=T, end=T + timedelta(minutes=2), seconds=120.0)
    journal.event("stopped", T, cursor=TickCursor(T, 2))
    assert [e["event"] for e in journal.events()] == ["gap", "stopped"]
    assert journal.events("gap")[0]["start"] == "2026-10-07T10:00:00+00:00"
    assert journal.events("stopped")[0]["cursor"] == {
        "time": "2026-10-07T10:00:00+00:00",
        "rows_at_time": 2,
    }
    journal.write_heartbeat(T, status="running")
    journal.write_heartbeat(T + timedelta(seconds=60), status="stopped")
    beat = journal.read_heartbeat()
    assert beat is not None
    assert (beat["time"], beat["status"]) == (T + timedelta(seconds=60), "stopped")


# --------------------------------------------------------------------------- spec snapshots


def spec(at: datetime) -> SymbolSpec:
    return SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        contract_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        captured_at=at,
    )


RAW: dict[str, Any] = {"bid": 4000.0, "ask": 4000.1, "session_open": 3990.0, "swap_long": -65.0}


def test_snapshot_written_on_contract_change_or_new_day_only(tmp_path: Path) -> None:
    assert snapshot_if_changed(tmp_path, spec(T), RAW, server="S") is not None
    quotes_moved = {**RAW, "bid": 4010.0, "ask": 4010.1, "session_open": 4000.0}
    assert (
        snapshot_if_changed(tmp_path, spec(T + timedelta(hours=1)), quotes_moved, server="S")
        is None
    )
    swap_changed = {**RAW, "swap_long": -70.0}
    path = snapshot_if_changed(tmp_path, spec(T + timedelta(hours=2)), swap_changed, server="S")
    assert path is not None
    assert json.loads(path.read_text())["raw_symbol_info"]["swap_long"] == -70.0
    next_day = snapshot_if_changed(tmp_path, spec(T + timedelta(days=1)), swap_changed, server="S")
    assert next_day is not None
    assert len(list((tmp_path / "specs" / "XAUUSD").glob("*.json"))) == 3
    assert contract_fingerprint(RAW) == contract_fingerprint({**RAW, "bid": 1.0, "time": 5})
    assert contract_fingerprint(RAW) != contract_fingerprint({**RAW, "volume_min": 0.1})
