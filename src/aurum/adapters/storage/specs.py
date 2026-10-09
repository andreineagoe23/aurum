"""Read the broker symbol-spec snapshots written by ``aurum.adapters.mt5.snapshot``.

Pure file access (no MetaTrader5), so offline code such as the simulator can use the broker's
real contract (point, volume step, stops level...) instead of hard-coded values.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from aurum.core.models import SymbolSpec


@dataclass(frozen=True)
class SpecSnapshot:
    path: Path
    spec: SymbolSpec
    server: str


def latest_spec(data_dir: Path, symbol: str | None = None) -> SpecSnapshot:
    """The newest snapshot (of ``symbol``, or of any symbol). Raises FileNotFoundError."""
    root = data_dir / "specs"
    pattern = f"{symbol}/*.json" if symbol else "*/*.json"
    snapshots = sorted(root.glob(pattern), key=lambda p: p.name) if root.is_dir() else []
    if not snapshots:
        raise FileNotFoundError(
            f"no symbol spec snapshot under {root}: run `aurum mt5 symbols` on the VM first"
        )
    path = snapshots[-1]
    body = json.loads(path.read_text(encoding="utf-8"))
    return SpecSnapshot(
        path=path, spec=SymbolSpec.model_validate(body["spec"]), server=body["server"]
    )
