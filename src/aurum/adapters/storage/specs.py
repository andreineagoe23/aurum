"""Read the broker symbol-spec snapshots written by ``aurum.adapters.mt5.snapshot``.

Pure file access (no MetaTrader5), so offline code such as the simulator can use the broker's
real contract (point, volume step, stops level...) instead of hard-coded values. Backtests use
one pinned snapshot (``pinned_spec``: file name and content hash, ``backtest.symbol_snapshot``),
never "the latest", so a snapshot the collector writes later cannot change an evaluation.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from aurum.core.errors import ConfigError
from aurum.core.models import SymbolSpec


@dataclass(frozen=True)
class SpecSnapshot:
    path: Path
    spec: SymbolSpec
    server: str
    sha256: str
    """Of the file's bytes."""


def _read(path: Path) -> SpecSnapshot:
    raw = path.read_bytes()
    body = json.loads(raw)
    return SpecSnapshot(
        path=path,
        spec=SymbolSpec.model_validate(body["spec"]),
        server=body["server"],
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def latest_spec(data_dir: Path, symbol: str | None = None) -> SpecSnapshot:
    """The newest snapshot (of ``symbol``, or of any symbol). Raises FileNotFoundError."""
    root = data_dir / "specs"
    pattern = f"{symbol}/*.json" if symbol else "*/*.json"
    snapshots = sorted(root.glob(pattern), key=lambda p: p.name) if root.is_dir() else []
    if not snapshots:
        raise FileNotFoundError(
            f"no symbol spec snapshot under {root}: run `aurum mt5 symbols` on the VM first"
        )
    return _read(snapshots[-1])


def pinned_spec(data_dir: Path, file: str, sha256: str) -> SpecSnapshot:
    """Exactly the snapshot named ``file`` (under any symbol), refused unless its bytes hash to
    ``sha256``. Raises FileNotFoundError if absent, ConfigError if ambiguous or changed."""
    root = data_dir / "specs"
    found = sorted(root.glob(f"*/{file}")) if root.is_dir() else []
    if not found:
        raise FileNotFoundError(f"pinned symbol snapshot {file} is not under {root}")
    if len(found) > 1:
        raise ConfigError(f"pinned symbol snapshot {file} exists for several symbols: {found}")
    snapshot = _read(found[0])
    if snapshot.sha256 != sha256:
        raise ConfigError(
            f"pinned symbol snapshot {file} has sha256 {snapshot.sha256}, not the pinned "
            f"{sha256}; refusing to use it"
        )
    return snapshot
