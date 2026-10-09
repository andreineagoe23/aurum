"""SHA-256 manifests of the reproducible datasets (`aurum data hashes`): the Phase 2 exit gate
("re-running ingestion and aggregation yields identical file hashes").

Covered: raw history ticks, raw broker bars, normalized ticks, canonical bars and the day
manifest. Not covered, by design: live collector chunks (their boundaries depend on when the
collector flushed), spec snapshots and run reports (timestamped).
"""

import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pyarrow as pa

from aurum.adapters.storage.layout import MT5_HISTORY, DataLayout
from aurum.adapters.storage.parquet import sha256_file

COVERED = (
    f"raw_ticks/{MT5_HISTORY}",
    "raw_broker_bars",
    "normalized_ticks",
    "canonical_bars",
)


def manifest(layout: DataLayout) -> dict[str, Any]:
    files: dict[str, str] = {}
    for prefix in COVERED:
        base = layout.root / prefix
        if base.is_dir():
            for path in sorted(base.rglob("*.parquet")):
                files[path.relative_to(layout.root).as_posix()] = sha256_file(path)
    return {
        "data_dir": str(layout.root),
        "versions": {
            "python": platform.python_version(),
            "polars": pl.__version__,
            "pyarrow": pa.__version__,
            "duckdb": duckdb.__version__,
        },
        "files": files,
    }


@dataclass
class ManifestDiff:
    identical: int = 0
    differ: list[str] = field(default_factory=list)
    only_left: list[str] = field(default_factory=list)
    only_right: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.differ or self.only_left or self.only_right)


def compare(left: dict[str, Any], right: dict[str, Any], prefix: str = "") -> ManifestDiff:
    """Compare two manifests' file hashes (optionally only paths starting with ``prefix``)."""
    a = {k: v for k, v in left["files"].items() if k.startswith(prefix)}
    b = {k: v for k, v in right["files"].items() if k.startswith(prefix)}
    out = ManifestDiff()
    for name in sorted(a.keys() | b.keys()):
        if name not in b:
            out.only_left.append(name)
        elif name not in a:
            out.only_right.append(name)
        elif a[name] == b[name]:
            out.identical += 1
        else:
            out.differ.append(name)
    return out


def load_manifest(path: Path) -> dict[str, Any]:
    import json

    body: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return body
