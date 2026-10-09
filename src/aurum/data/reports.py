"""JSON run reports under ``quality_reports/<name>/<UTC stamp>.json`` (backfill runs, QA,
spread statistics, depth measurements, hash manifests). One immutable file per run."""

import dataclasses
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from aurum.adapters.storage.layout import DataLayout
from aurum.core.clock import SystemClock
from aurum.ports.clock import ClockPort


def _default(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def to_json(body: Any) -> str:
    return json.dumps(body, default=_default, indent=2, sort_keys=True)


def write_report(
    layout: DataLayout, name: str, body: dict[str, Any], *, clock: ClockPort | None = None
) -> Path:
    now = (clock or SystemClock()).now()
    directory = layout.quality_reports(name)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"{stamp}.json"
    with path.open("x", encoding="utf-8") as f:
        f.write(to_json({"generated_at": now, **body}))
    return path


def latest_report(layout: DataLayout, name: str) -> dict[str, Any] | None:
    directory = layout.quality_reports(name)
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not files:
        return None
    body: dict[str, Any] = json.loads(files[-1].read_text(encoding="utf-8"))
    return body
