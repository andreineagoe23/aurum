"""Append-only storage for live-collected ticks (``raw_ticks/mt5_live``) plus the collector's
heartbeat and event journal.

Every flush writes one new immutable chunk per UTC date it touches, named
``<first_ms>_<last_ms>_<rows>.parquet`` (epoch milliseconds, UTC). Nothing is ever rewritten.

The resume point (cursor) is derived **from the chunk files themselves**, never from a separate
state file. A crash between writing data and writing state can therefore never cause
duplicates or a skipped range: what is on disk is the only truth.
"""

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from aurum.adapters.storage.layout import DataLayout, days_in
from aurum.adapters.storage.parquet import (
    empty_frame,
    read_parquet,
    remove_stale_temps,
    write_parquet,
)
from aurum.core.timeutil import EPOCH, to_utc
from aurum.data.schemas import RAW_TICKS


def epoch_ms(t: datetime) -> int:
    return (to_utc(t) - EPOCH) // timedelta(milliseconds=1)


def from_epoch_ms(ms: int) -> datetime:
    return EPOCH + timedelta(milliseconds=ms)


@dataclass(frozen=True)
class TickCursor:
    """Resume point: the newest stored tick time and how many stored rows share it (several
    ticks can carry the same millisecond)."""

    time: datetime
    rows_at_time: int


@dataclass(frozen=True)
class ChunkName:
    first_ms: int
    last_ms: int
    rows: int

    @classmethod
    def parse(cls, path: Path) -> "ChunkName | None":
        parts = path.name.removesuffix(".parquet").split("_")
        if len(parts) != 3 or not path.name.endswith(".parquet"):
            return None
        try:
            return cls(*(int(p) for p in parts))
        except ValueError:
            return None

    @property
    def filename(self) -> str:
        return f"{self.first_ms}_{self.last_ms}_{self.rows}.parquet"


class LiveTickStore:
    def __init__(self, layout: DataLayout, symbol: str) -> None:
        self.layout = layout
        self.symbol = symbol

    @property
    def base(self) -> Path:
        return self.layout.live_ticks_dir(self.symbol)

    def remove_stale_temps(self) -> int:
        return remove_stale_temps(self.base)

    def chunks(self, day: date) -> list[tuple[ChunkName, Path]]:
        """The day's chunks in write order (by first, then last tick time)."""
        directory = self.layout.live_ticks_dir(self.symbol, day)
        if not directory.is_dir():
            return []
        found = [(ChunkName.parse(p), p) for p in directory.iterdir()]
        named = [(n, p) for n, p in found if n is not None]
        return sorted(named, key=lambda np_: (np_[0].first_ms, np_[0].last_ms, np_[1].name))

    def days(self) -> list[date]:
        return days_in(self.base)

    def append(self, frame: pl.DataFrame) -> list[Path]:
        """Write ``frame`` (RAW_TICKS rows, received order) as one chunk per UTC date."""
        if frame.is_empty():
            return []
        written = []
        frame = frame.with_columns(pl.col("time").dt.date().alias("_day"))
        for day in frame["_day"].unique(maintain_order=True).to_list():
            part = frame.filter(pl.col("_day") == day).drop("_day")
            times = part["time"]
            name = ChunkName(
                epoch_ms(times.min()),  # type: ignore[arg-type]
                epoch_ms(times.max()),  # type: ignore[arg-type]
                part.height,
            )
            path = self.layout.live_ticks_dir(self.symbol, day) / name.filename
            written.append(write_parquet(part, path, RAW_TICKS, overwrite=False))
        return written

    def read_day(self, day: date) -> pl.DataFrame:
        frames = [read_parquet(p, RAW_TICKS) for _, p in self.chunks(day)]
        return pl.concat(frames) if frames else empty_frame(RAW_TICKS)

    def tail(self) -> TickCursor | None:
        """Cursor after the newest stored tick, or None if nothing is stored yet."""
        for day in reversed(self.days()):
            chunks = self.chunks(day)
            if not chunks:
                continue
            last_ms = max(n.last_ms for n, _ in chunks)
            last = from_epoch_ms(last_ms)
            rows = 0
            for name, path in chunks:
                if name.first_ms <= last_ms <= name.last_ms:
                    times = read_parquet(path, RAW_TICKS)["time"]
                    rows += int((times == last).sum())
            return TickCursor(time=last, rows_at_time=rows)
        return None


# ------------------------------------------------------------------------------ journal


def _iso(t: datetime) -> str:
    return to_utc(t).isoformat()


class CollectorJournal:
    """``collector/<symbol>/events.jsonl`` (append-only) and ``heartbeat.json`` (replaced)."""

    def __init__(self, layout: DataLayout, symbol: str) -> None:
        self.dir = layout.collector_dir(symbol)
        self.events_path = self.dir / "events.jsonl"
        self.heartbeat_path = self.dir / "heartbeat.json"

    def event(self, kind: str, at: datetime, **fields: Any) -> dict[str, Any]:
        record = {"time": _iso(at), "event": kind, **_jsonable(fields)}
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return record

    def events(self, kind: str | None = None) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        lines = self.events_path.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
        return [r for r in records if kind is None or r["event"] == kind]

    def write_heartbeat(self, at: datetime, **fields: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"time": _iso(at), **_jsonable(fields)}, sort_keys=True, indent=2)
        tmp = self.heartbeat_path.with_name(f"heartbeat.json.{os.getpid()}.tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, self.heartbeat_path)

    def read_heartbeat(self) -> dict[str, Any] | None:
        if not self.heartbeat_path.exists():
            return None
        data: dict[str, Any] = json.loads(self.heartbeat_path.read_text(encoding="utf-8"))
        data["time"] = datetime.fromisoformat(data["time"]).astimezone(UTC)
        return data


def _jsonable(fields: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, datetime):
            out[key] = _iso(value)
        elif isinstance(value, TickCursor):
            out[key] = {"time": _iso(value.time), "rows_at_time": value.rows_at_time}
        elif hasattr(value, "__dataclass_fields__"):
            out[key] = _jsonable(asdict(value))
        else:
            out[key] = value
    return out
