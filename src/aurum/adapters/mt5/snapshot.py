"""Persist broker symbol specs to ``<data_dir>/specs/`` (one immutable JSON file per capture)."""

import hashlib
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


VOLATILE_KEYS = frozenset(
    {
        "ask", "askhigh", "asklow", "bid", "bidhigh", "bidlow", "last", "lasthigh", "lastlow",
        "price_change", "spread", "time", "volume", "volumehigh", "volumelow", "volume_real",
        "volumehigh_real", "volumelow_real", "select", "visible",
    }
)  # fmt: skip
VOLATILE_PREFIXES = ("session_", "price_greeks_")
"""``symbol_info`` fields that move with the market (quotes, session statistics). Everything
else (contract size, tick size/value, volume limits, swaps, stops level, trade and filling
modes...) describes the contract; a change there is worth a new snapshot."""


def contract_fields(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in raw.items()
        if k not in VOLATILE_KEYS and not k.startswith(VOLATILE_PREFIXES)
    }


def contract_fingerprint(raw: dict[str, Any]) -> str:
    body = json.dumps(contract_fields(raw), sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def latest_snapshot(data_dir: Path, symbol: str) -> dict[str, Any] | None:
    directory = data_dir / "specs" / symbol
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not files:
        return None
    body: dict[str, Any] = json.loads(files[-1].read_text(encoding="utf-8"))
    return body


def snapshot_if_changed(
    data_dir: Path, spec: SymbolSpec, raw: dict[str, Any], *, server: str
) -> Path | None:
    """Write a snapshot if the contract fields changed since the latest one, or if the latest
    is from an earlier UTC day. Returns the new file, or None if nothing was written."""
    assert spec.captured_at is not None
    latest = latest_snapshot(data_dir, spec.name)
    if latest is not None:
        same_contract = contract_fingerprint(latest["raw_symbol_info"]) == contract_fingerprint(raw)
        same_day = latest["captured_at"][:10] == spec.captured_at.date().isoformat()
        if same_contract and same_day and latest.get("server") == server:
            return None
    return write_symbol_snapshot(data_dir, spec, raw, server=server)
