"""A small synthetic Phase 2 data store (canonical bars, normalized ticks, day manifest, spec
snapshot) for end-to-end backtest tests, and repo configs pinned to its snapshot.
Deterministic for a given seed."""

import hashlib
import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from aurum.adapters.storage.layout import DataLayout
from aurum.adapters.storage.parquet import write_parquet
from aurum.core.timeframes import Timeframe
from aurum.data.resample import aggregate, ticks_to_m1
from aurum.data.schemas import CANONICAL_BARS, DAY_SOURCES, NORMALIZED_TICKS, polars_schema
from tests.simdata import spec

SYMBOL = "XAUUSD"
SNAPSHOT = "20260101T000000Z.json"
TICK_SECONDS = 2
MARKET_HOURS_UTC = (0, 21)  # every test day is a summer weekday: open 00:00-20:59 UTC


def synthetic_ticks(day: date, seed: int) -> pl.DataFrame:
    """A random walk of bid/ask quotes every 2 s over the day's market hours (UTC)."""
    rng = np.random.default_rng(seed * 100_000 + day.toordinal())
    start = datetime(day.year, day.month, day.day, MARKET_HOURS_UTC[0], tzinfo=UTC)
    n = (MARKET_HOURS_UTC[1] - MARKET_HOURS_UTC[0]) * 3600 // TICK_SECONDS
    steps = rng.choice([-0.30, -0.10, 0.0, 0.10, 0.30], size=n)
    bid = np.round(4000.0 + np.cumsum(steps), 2)
    spread = rng.integers(5, 12, size=n)
    times = [start + timedelta(seconds=TICK_SECONDS * i) for i in range(n)]
    return pl.DataFrame(
        {
            "time": times,
            "bid": bid,
            "ask": np.round(bid + spread * 0.01, 2),
            "spread_points": spread.astype(np.int32),
            "flags": np.full(n, 6, dtype=np.uint32),
            "crossed": np.zeros(n, dtype=bool),
            "spread_outlier": np.zeros(n, dtype=bool),
            "source": ["mt5_history"] * n,
        },
        schema=polars_schema(NORMALIZED_TICKS),
    )


def write_store(
    root: Path, *, tick_days: list[date], bar_days: list[date], seed: int = 1
) -> DataLayout:
    """Days in ``tick_days`` get exact normalized ticks; ``bar_days`` only broker-style bars
    (minimum spread only). Also writes the day manifest and a spec snapshot."""
    layout = DataLayout(root)
    layout.ensure()
    manifest = []
    for day in sorted({*tick_days, *bar_days}):
        ticks = synthetic_ticks(day, seed)
        m1 = ticks_to_m1(ticks)
        exact = day in tick_days
        if exact:
            write_parquet(
                ticks, layout.normalized_ticks(SYMBOL, day), NORMALIZED_TICKS, overwrite=False
            )
        else:
            m1 = m1.with_columns(
                quote_count=pl.lit(None, pl.Int64),
                spread_open=pl.lit(None, pl.Int32),
                spread_max=pl.lit(None, pl.Int32),
                spread_mean=pl.lit(None, pl.Float64),
                spread_close=pl.lit(None, pl.Int32),
                source=pl.lit("mt5_bars_m1"),
            )
        for tf in (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1):
            write_parquet(
                aggregate(m1, tf),
                layout.canonical_bars(SYMBOL, tf, day),
                CANONICAL_BARS,
                overwrite=False,
            )
        manifest.append(
            {
                "day": day,
                "m1_source": "mt5_history" if exact else "mt5_bars_m1",
                "tick_source": "mt5_history" if exact else None,
                "tick_rows": ticks.height if exact else None,
                "tick_fidelity": "exact" if exact else None,
                "tick_m1_mismatch": 0 if exact else None,
                "broker_only_minutes": 0 if exact else None,
                "tick_only_minutes": 0 if exact else None,
            }
        )
    write_parquet(
        pl.DataFrame(manifest, schema=polars_schema(DAY_SOURCES)),
        layout.day_manifest(SYMBOL),
        DAY_SOURCES,
        overwrite=True,
    )
    snap = root / "specs" / SYMBOL / SNAPSHOT
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_bytes(snapshot_bytes())
    return layout


def snapshot_bytes() -> bytes:
    body = {
        "captured_at": "2026-01-01T00:00:00+00:00",
        "server": "Test-Demo",
        "spec": spec().model_dump(mode="json"),
    }
    return json.dumps(body).encode()


def use_configs(
    dest: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sealed: dict[str, object] | None = None,
) -> Path:
    """The repo configs under ``dest``, pinned to the synthetic store's snapshot, optionally
    with another sealed period (tests of pipeline mechanics keep the seal out of their
    synthetic days). Returns the config dir."""
    configs = dest / "configs"
    shutil.copytree(repo_root / "configs", configs)
    backtest = configs / "backtest.yaml"
    cfg = yaml.safe_load(backtest.read_text(encoding="utf-8"))
    cfg["symbol_snapshot"] = {
        "file": SNAPSHOT,
        "sha256": hashlib.sha256(snapshot_bytes()).hexdigest(),
    }
    if sealed is not None:
        cfg["sealed_period"] = sealed
    backtest.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("AURUM_CONFIG_DIR", str(configs))
    return configs
