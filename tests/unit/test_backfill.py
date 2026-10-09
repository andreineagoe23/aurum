from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from aurum.adapters.mt5 import MT5Broker
from aurum.adapters.storage.layout import DataLayout
from aurum.adapters.storage.parquet import read_parquet, sha256_file
from aurum.core.clock import ManualClock
from aurum.core.timeframes import Timeframe
from aurum.data.backfill import Backfiller
from aurum.data.market_hours import MarketHours
from aurum.settings import AppConfig
from tests import fake_mt5 as f
from tests.fake_mt5 import FakeMT5
from tests.unit.test_mt5_adapter import make_settings

NOW = datetime(2026, 10, 8, 9, 30, tzinfo=UTC)  # Thursday
MON, TUE, WED = date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)
SAT = date(2026, 10, 3)


class CountingSource:
    """Wraps the broker; counts calls and can return nothing for the first N calls (the
    terminal still downloading history)."""

    def __init__(self, broker: MT5Broker, empty_first: int = 0) -> None:
        self.broker = broker
        self.calls = 0
        self.empty_first = empty_first

    def __getattr__(self, name: str) -> Any:
        return getattr(self.broker, name)

    def _maybe_empty(self, frame: pl.DataFrame) -> pl.DataFrame:
        self.calls += 1
        if self.empty_first > 0:
            self.empty_first -= 1
            return frame.clear()
        return frame

    def tick_frame(self, *args: Any) -> pl.DataFrame:
        return self._maybe_empty(self.broker.tick_frame(*args))

    def rate_frame(self, *args: Any) -> pl.DataFrame:
        return self._maybe_empty(self.broker.rate_frame(*args))


def add_day_of_ticks(mt5: FakeMT5, d: date, every: timedelta = timedelta(minutes=7)) -> None:
    t = datetime(d.year, d.month, d.day, tzinfo=UTC)
    while t.date() == d:
        ms = int(mt5.server_tz.to_server_seconds(t) * 1000)
        mt5.tick_rows.append({"time_msc": ms, "bid": 4000.0, "ask": 4000.09})
        t += every


def add_m5_bars(mt5: FakeMT5, start: datetime, end: datetime) -> None:
    t = start
    rows = mt5.rates.setdefault(f.TIMEFRAME_M5, [])
    while t < end:
        rows.append(
            {
                "time": mt5.server_s(t),
                "open": 4000.0,
                "high": 4001.0,
                "low": 3999.0,
                "close": 4000.5,
                "tick_volume": 50,
                "spread": 3,
                "real_volume": 0,
            }
        )
        t += timedelta(minutes=5)


@pytest.fixture
def mt5() -> FakeMT5:
    clock = ManualClock(NOW)
    return FakeMT5(now=clock.now)


def make(
    app_config: AppConfig, mt5: FakeMT5, root: Path, empty_first: int = 0
) -> tuple[Backfiller, CountingSource, list[float]]:
    clock = ManualClock(NOW)
    broker = MT5Broker(make_settings(app_config), mt5_module=mt5, clock=clock)
    broker.connect()
    source = CountingSource(broker, empty_first)
    sleeps: list[float] = []
    bf = Backfiller(
        source,  # type: ignore[arg-type]
        DataLayout(root),
        app_config.data.backfill,
        MarketHours(app_config.data.market),
        clock=clock,
        sleep=sleeps.append,
    )
    return bf, source, sleeps


def test_ticks_one_file_per_day_including_empty_and_never_today(
    app_config: AppConfig, mt5: FakeMT5, tmp_path: Path
) -> None:
    for d in (MON, TUE, WED, NOW.date()):
        add_day_of_ticks(mt5, d)
    bf, _, _ = make(app_config, mt5, tmp_path)
    result = bf.ticks("XAUUSD", SAT, NOW.date())
    lay = DataLayout(tmp_path)
    stored = sorted(p.name for p in lay.raw_ticks_dir("XAUUSD").iterdir())
    assert stored == [f"2026-10-0{i}.parquet" for i in range(3, 8)]  # Sat..Wed, not Thursday
    assert read_parquet(lay.raw_ticks("XAUUSD", SAT)).height == 0
    assert read_parquet(lay.raw_ticks("XAUUSD", TUE)).height == 24 * 60 // 7 + 1
    assert result.empty_days == ["2026-10-04"]  # Sunday evening should trade; Saturday not
    assert result.written == 5


def test_rerun_skips_existing_days_and_is_byte_identical(
    app_config: AppConfig, mt5: FakeMT5, tmp_path: Path
) -> None:
    add_day_of_ticks(mt5, TUE)
    bf, source, _ = make(app_config, mt5, tmp_path / "a")
    bf.ticks("XAUUSD", MON, WED)
    calls = source.calls
    again = bf.ticks("XAUUSD", MON, WED)
    assert source.calls == calls  # nothing refetched
    assert again.skipped_existing == 3
    other, _, _ = make(app_config, mt5, tmp_path / "b")
    other.ticks("XAUUSD", MON, WED)
    a = DataLayout(tmp_path / "a").raw_ticks("XAUUSD", TUE)
    b = DataLayout(tmp_path / "b").raw_ticks("XAUUSD", TUE)
    assert sha256_file(a) == sha256_file(b)


def test_empty_weekday_is_retried_before_storing(
    app_config: AppConfig, mt5: FakeMT5, tmp_path: Path
) -> None:
    add_day_of_ticks(mt5, TUE)
    bf, source, sleeps = make(app_config, mt5, tmp_path, empty_first=2)
    result = bf.ticks("XAUUSD", TUE, TUE)
    assert source.calls == 3
    assert sleeps == [2.0, 4.0]
    assert result.rows > 0
    assert result.empty_days == []


def test_refetch_empty_replaces_only_empty_days(
    app_config: AppConfig, mt5: FakeMT5, tmp_path: Path
) -> None:
    add_day_of_ticks(mt5, MON)
    bf, _, _ = make(app_config, mt5, tmp_path)
    first = bf.ticks("XAUUSD", MON, TUE)
    assert first.empty_days == ["2026-10-06"]
    add_day_of_ticks(mt5, TUE)  # history became available later
    lay = DataLayout(tmp_path)
    mon_hash = sha256_file(lay.raw_ticks("XAUUSD", MON))
    second = bf.ticks("XAUUSD", MON, TUE, refetch_empty=True)
    assert second.refilled == 1
    assert read_parquet(lay.raw_ticks("XAUUSD", TUE)).height > 0
    assert sha256_file(lay.raw_ticks("XAUUSD", MON)) == mon_hash  # data days untouched


def test_bars_chunked_and_split_by_day_completed_only(
    app_config: AppConfig, mt5: FakeMT5, tmp_path: Path
) -> None:
    add_m5_bars(mt5, datetime(2026, 10, 5, tzinfo=UTC), NOW + timedelta(hours=1))
    cfg = app_config.model_copy(
        update={
            "data": app_config.data.model_copy(
                update={
                    "backfill": app_config.data.backfill.model_copy(update={"bar_chunk_days": 2})
                }
            )
        }
    )
    bf, source, _ = make(cfg, mt5, tmp_path)
    result = bf.bars("XAUUSD", Timeframe.M5, MON, NOW.date())
    assert source.calls == 2  # Mon-Tue, Wed (Thursday is not complete yet)
    lay = DataLayout(tmp_path)
    tue = read_parquet(lay.raw_bars("XAUUSD", Timeframe.M5, TUE))
    assert tue.height == 288
    assert tue["open_time"].min() == datetime(2026, 10, 6, tzinfo=UTC)
    assert not lay.raw_bars("XAUUSD", Timeframe.M5, NOW.date()).exists()
    assert result.written == 3
    assert bf.bars("XAUUSD", Timeframe.M5, MON, NOW.date()).skipped_existing == 3
