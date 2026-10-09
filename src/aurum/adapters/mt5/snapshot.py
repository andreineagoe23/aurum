"""Persist broker symbol specs to ``<data_dir>/specs/`` (one immutable JSON file per capture)."""

import json
from pathlib import Path
from typing import Any

from aurum.core.models import SymbolSpec


def write_symbol_snapshot(
    data_dir: Path, spec: SymbolSpec, raw: dict[str, Any], *, server: str
) -> Path:
    """Write ``specs/<symbol>/<captured_at UTC>.json`` and return its path. Never overwrites.

    ``raw`` is every MT5 ``symbol_info`` field as reported; integer times in it are MT5 server
    clock, not UTC (ADR-0004). ``spec`` is the validated subset Aurum uses.
    """
    assert spec.captured_at is not None
    stamp = spec.captured_at.strftime("%Y%m%dT%H%M%SZ")
    path = data_dir / "specs" / spec.name / f"{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "captured_at": spec.captured_at.isoformat(),
        "server": server,
        "spec": spec.model_dump(mode="json"),
        "raw_symbol_info": raw,
        "raw_note": "integer times in raw_symbol_info are MT5 server clock (ADR-0004)",
    }
    with path.open("x", encoding="utf-8") as f:  # "x": refuse to overwrite a capture
        json.dump(body, f, indent=2, sort_keys=True, default=str)
    return path
