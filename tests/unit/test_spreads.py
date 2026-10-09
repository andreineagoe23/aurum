from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from aurum.adapters.storage.parquet import write_parquet
from aurum.data.schemas import NORMALIZED_TICKS, polars_schema
from aurum.data.spreads import propose_max_spread, spread_stats
from aurum.sessions.clock import SessionClock
from aurum.settings import AppConfig

DAY = date(2026, 10, 7)  # BST: london_morning 06:00-10:00 UTC, overlap 12:00-15:30 UTC


def ticks_file(tmp_path: Path, rows: list[tuple[datetime, int]]) -> Path:
    df = pl.DataFrame(
        {
            "time": [t for t, _ in rows],
            "bid": [4000.0] * len(rows),
            "ask": [4000.0 + s / 100 for _, s in rows],
            "spread_points": [s for _, s in rows],
            "flags": [134] * len(rows),
            "crossed": [False] * len(rows),
            "spread_outlier": [False] * len(rows),
            "source": ["mt5_history"] * len(rows),
        },
        schema=polars_schema(NORMALIZED_TICKS),
    )
    return write_parquet(df, tmp_path / "t.parquet", NORMALIZED_TICKS, overwrite=False)


@pytest.fixture
def clock(app_config: AppConfig) -> SessionClock:
    return SessionClock(app_config.sessions)


def test_session_and_decision_spreads(tmp_path: Path, clock: SessionClock) -> None:
    morning = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
    rows = [(morning + timedelta(seconds=10 * i), 8) for i in range(4 * 360)]  # 06:00-10:00
    rows[30] = (rows[30][0], 200)  # one wide quote at 06:05:00, i.e. at an M5 close
    night = datetime(2026, 10, 7, 2, 0, tzinfo=UTC)
    rows += [(night + timedelta(seconds=i), 30) for i in range(60)]  # outside sessions
    stats = spread_stats([ticks_file(tmp_path, sorted(rows))], clock, [DAY], spike_factor=3)
    [morning_row] = [
        r for r in stats["quote_weighted"]["by_session"] if r["session"] == "london_morning"
    ]
    assert morning_row["n"] == 4 * 360
    assert morning_row["p50"] == 8
    assert morning_row["max"] == 200
    [all_row] = [
        r for r in stats["quote_weighted"]["by_session"] if r["session"] == "all_market_hours"
    ]
    assert all_row["n"] == 4 * 360 + 60
    decisions = {r["session"]: r for r in stats["at_decision_m5_close"]}
    assert decisions["london_morning"]["n"] == 48  # 06:05 .. 10:00
    assert decisions["london_morning"]["max"] == 200  # the quote in force at 06:05
    assert "ldn_ny_overlap" not in decisions  # no quotes within a minute of those closes
    assert stats["decisions_blocked_by_cap"]["cap_20"] == pytest.approx(1 / 48, abs=1e-5)
    assert stats["spikes"]["spike_minutes"] == 1
    proposal = propose_max_spread(stats)
    assert proposal is not None
    assert proposal.max_spread_points % 5 == 0


def test_no_files_and_no_proposal(clock: SessionClock) -> None:
    assert spread_stats([], clock, [], spike_factor=3) == {"days": 0}
    assert propose_max_spread({"days": 0}) is None
