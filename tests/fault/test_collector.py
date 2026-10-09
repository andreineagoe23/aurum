"""Live tick collector against the fake terminal: no duplicates, no silent drops, recovery from
disconnects, terminal restarts, VM sleep and collector restarts."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from aurum.adapters.mt5 import MT5Broker
from aurum.adapters.storage.layout import DataLayout
from aurum.adapters.storage.live_ticks import CollectorJournal, LiveTickStore
from aurum.apps.collector import TickCollector
from aurum.core.clock import ManualClock
from aurum.core.errors import BrokerError
from aurum.data.market_hours import MarketHours
from aurum.settings import AppConfig, CollectorConfig
from tests.fake_mt5 import FakeMT5
from tests.unit.test_mt5_adapter import make_settings

T0 = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)  # Wednesday, market open
CFG = CollectorConfig(
    poll_seconds=1,
    flush_seconds=60,
    heartbeat_seconds=30,
    gap_record_seconds=60,
    settle_seconds=2,
    pause_detect_seconds=30,
    reconnect_initial_seconds=1,
    reconnect_max_seconds=8,
)


@dataclass
class World:
    """Fake terminal + clock + a schedule of things that happen while the collector sleeps."""

    clock: ManualClock
    mt5: FakeMT5
    broker: MT5Broker
    layout: DataLayout
    market: MarketHours
    schedule: list[tuple[datetime, Callable[[], None]]] = field(default_factory=list)

    def sleep(self, seconds: float) -> None:
        self.clock.advance(timedelta(seconds=seconds))
        due = [item for item in self.schedule if item[0] <= self.clock.now()]
        for item in due:
            self.schedule.remove(item)
            item[1]()

    def at(self, t: datetime, action: Callable[[], None]) -> None:
        self.schedule.append((t, action))

    def add_ticks(
        self, start: datetime, end: datetime, every: timedelta = timedelta(milliseconds=700)
    ) -> None:
        t, i = start, 0
        while t < end:
            ms = int(self.mt5.server_tz.to_server_seconds(t) * 1000)
            bid = 4000.0 + (i % 50) * 0.01
            self.mt5.tick_rows.append({"time_msc": ms, "bid": bid, "ask": bid + 0.09})
            t += every
            i += 1

    def expected(self, until: datetime) -> list[datetime]:
        return sorted(
            self.mt5.server_tz.to_utc(r["time_msc"] / 1000)
            for r in self.mt5.tick_rows
            if self.mt5.server_tz.to_utc(r["time_msc"] / 1000) <= until
        )

    def collector(self, cfg: CollectorConfig = CFG, **kwargs: object) -> TickCollector:
        return TickCollector(
            self.broker,
            self.layout,
            cfg,
            self.market,
            clock=self.clock,
            sleep=self.sleep,
            **kwargs,  # type: ignore[arg-type]
        )

    def run_until(self, end: datetime, cfg: CollectorConfig = CFG, **kwargs: object) -> None:
        self.collector(cfg, **kwargs).run(lambda: self.clock.now() >= end)

    def stored(self) -> pl.DataFrame:
        store = LiveTickStore(self.layout, "XAUUSD")
        frames = [store.read_day(d) for d in store.days()]
        return pl.concat(frames) if frames else pl.DataFrame()

    def events(self, kind: str | None = None) -> list[dict[str, object]]:
        return CollectorJournal(self.layout, "XAUUSD").events(kind)


@pytest.fixture
def world(app_config: AppConfig, tmp_path: Path) -> World:
    clock = ManualClock(T0)
    mt5 = FakeMT5(now=clock.now)
    w = World(
        clock=clock,
        mt5=mt5,
        broker=None,  # type: ignore[arg-type]
        layout=DataLayout(tmp_path),
        market=MarketHours(app_config.data.market),
    )
    w.broker = MT5Broker(make_settings(app_config), mt5_module=mt5, clock=clock, sleep=w.sleep)
    return w


def stored_times(w: World) -> list[datetime]:
    return w.stored()["time"].to_list()


def test_collects_every_tick_exactly_once(world: World) -> None:
    world.add_ticks(T0 - timedelta(minutes=10), T0 + timedelta(minutes=10))
    end = T0 + timedelta(minutes=5)
    world.run_until(end)
    assert stored_times(world) == world.expected(end)
    kinds = [e["event"] for e in world.events()]
    assert kinds[:2] == ["started", "connected"]
    assert kinds[-1] == "stopped"
    assert "gap" not in kinds


def test_restart_resumes_without_duplicates_and_records_outage(world: World) -> None:
    world.add_ticks(T0, T0 + timedelta(minutes=40))
    world.run_until(T0 + timedelta(minutes=5))
    world.clock.advance(timedelta(minutes=15))  # collector down, market keeps ticking
    end = T0 + timedelta(minutes=25)
    world.run_until(end)
    times = stored_times(world)
    assert times == world.expected(end)
    assert len(times) == len(set(times))
    [outage] = world.events("outage")
    assert outage["previous_status"] == "stopped"
    assert 899 <= outage["seconds"] <= 905  # type: ignore[operator]
    assert world.events("gap") == []  # the terminal served the missed span from history


def test_same_millisecond_rows_are_neither_lost_nor_duplicated(world: World) -> None:
    ms = int(world.mt5.server_tz.to_server_seconds(T0 + timedelta(seconds=1)) * 1000)
    row = {"time_msc": ms, "bid": 4000.0, "ask": 4000.09}
    world.mt5.tick_rows += [dict(row), {**row, "bid": 4000.01}]

    def late_row_same_ms() -> None:  # the terminal receives a third tick stamped with that ms
        world.mt5.tick_rows.append({**row, "bid": 4000.02})
        world.mt5.tick_rows.append({**row, "time_msc": ms + 1000, "bid": 4000.03})

    world.at(T0 + timedelta(seconds=10), late_row_same_ms)
    world.run_until(T0 + timedelta(seconds=30))
    assert world.stored()["bid"].to_list() == [4000.0, 4000.01, 4000.02, 4000.03]


def test_ipc_failure_reconnects_and_loses_nothing(world: World) -> None:
    world.add_ticks(T0, T0 + timedelta(minutes=10))
    world.at(T0 + timedelta(minutes=2), lambda: setattr(world.mt5, "fail_next_copies", 3))
    end = T0 + timedelta(minutes=6)
    world.run_until(end)
    assert stored_times(world) == world.expected(end)
    assert world.events("disconnected")
    assert len(world.events("connected")) >= 2


def test_terminal_restart_with_backoff(world: World) -> None:
    world.add_ticks(T0, T0 + timedelta(minutes=20))

    def terminal_dies() -> None:
        world.mt5._initialized = False
        world.mt5.initialize_results = [False] * 7 + [True]  # 2+ failed broker connects

    world.at(T0 + timedelta(minutes=2), terminal_dies)
    end = T0 + timedelta(minutes=10)
    world.run_until(end)
    assert stored_times(world) == world.expected(end)
    failed = world.events("connect_failed")
    assert len(failed) >= 2
    assert [e["retry_in_s"] for e in failed[:2]] == [1, 2]  # exponential backoff


def test_gap_while_market_open_is_recorded(world: World) -> None:
    world.add_ticks(T0, T0 + timedelta(minutes=2))
    world.add_ticks(T0 + timedelta(minutes=9), T0 + timedelta(minutes=10))
    world.clock.set(T0 + timedelta(minutes=10))
    world.run_until(T0 + timedelta(minutes=11))
    [gap] = world.events("gap")
    assert gap["seconds"] == pytest.approx(7 * 60 + 0.7, abs=1)
    assert gap["market_seconds"] == pytest.approx(gap["seconds"], abs=0.01)


def test_daily_break_and_weekend_are_not_gaps(world: World) -> None:
    # Measured: last tick 20:58:59, reopen 22:00:00 (US summer time).
    world.add_ticks(
        datetime(2026, 10, 7, 20, 55, tzinfo=UTC), datetime(2026, 10, 7, 20, 59, tzinfo=UTC)
    )
    world.add_ticks(
        datetime(2026, 10, 7, 22, 0, 1, tzinfo=UTC), datetime(2026, 10, 7, 22, 3, tzinfo=UTC)
    )
    world.clock.set(datetime(2026, 10, 7, 22, 3, tzinfo=UTC))
    world.run_until(datetime(2026, 10, 7, 22, 4, tzinfo=UTC))
    assert world.events("gap") == []
    assert len(stored_times(world)) == len(world.expected(world.clock.now()))


def test_vm_sleep_is_recorded_and_recovered(world: World) -> None:
    world.add_ticks(T0, T0 + timedelta(minutes=30))
    world.at(T0 + timedelta(minutes=2), lambda: world.clock.advance(timedelta(minutes=12)))
    end = T0 + timedelta(minutes=20)
    world.run_until(end)
    [pause] = world.events("pause")
    assert pause["seconds"] >= 12 * 60  # type: ignore[operator]
    assert stored_times(world) == world.expected(end)


def test_crash_leftovers_are_cleaned_and_cursor_comes_from_disk(world: World) -> None:
    world.add_ticks(T0, T0 + timedelta(minutes=10))
    world.run_until(T0 + timedelta(minutes=2))
    day_dir = world.layout.live_ticks_dir("XAUUSD", T0.date())
    (day_dir / "123_456_7.parquet.999.tmp").write_bytes(b"half a file")
    end = T0 + timedelta(minutes=4)
    world.run_until(end)
    [_, second] = world.events("started")
    assert second["removed_temp_files"] == 1
    assert stored_times(world) == world.expected(end)


def test_utc_midnight_splits_chunks_by_day(world: World) -> None:
    start = datetime(2026, 10, 7, 23, 58, tzinfo=UTC)
    world.clock.set(start)
    world.add_ticks(start, start + timedelta(minutes=4))
    world.run_until(start + timedelta(minutes=4))
    store = LiveTickStore(world.layout, "XAUUSD")
    assert store.days() == [date(2026, 10, 7), date(2026, 10, 8)]
    for d in store.days():
        assert {t.date() for t in store.read_day(d)["time"].to_list()} == {d}


def test_stop_flushes_buffer_and_marks_heartbeat(world: World) -> None:
    world.add_ticks(T0, T0 + timedelta(minutes=5))
    cfg = CFG.model_copy(update={"flush_seconds": 3600})
    world.run_until(T0 + timedelta(minutes=3), cfg)
    assert len(stored_times(world)) == len(world.expected(T0 + timedelta(minutes=3)))
    heartbeat = CollectorJournal(world.layout, "XAUUSD").read_heartbeat()
    assert heartbeat is not None
    assert heartbeat["status"] == "stopped"


def test_on_connect_hook_runs_and_its_failure_is_tolerated(world: World) -> None:
    calls: list[str] = []

    def hook(symbol: str) -> None:
        calls.append(symbol)
        raise BrokerError("snapshot failed")

    world.add_ticks(T0, T0 + timedelta(minutes=2))
    world.run_until(T0 + timedelta(minutes=1), on_connect=hook)
    assert calls == ["XAUUSD"]
    assert world.events("on_connect_failed")
    assert stored_times(world)


def test_stop_file_stops_gracefully_and_a_stale_one_is_ignored(
    world: World, app_config: AppConfig
) -> None:
    from aurum.apps.collector import run_collector, stop_file_path

    settings = make_settings(app_config)
    settings = type(settings)(**{**vars(settings), "data_dir": world.layout.root})
    stop = stop_file_path(world.layout)
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.touch()  # left over from an earlier run: must not stop this one at once
    world.add_ticks(T0, T0 + timedelta(minutes=10))
    world.at(T0 + timedelta(minutes=3), stop.touch)
    stats = run_collector(settings, world.broker, clock=world.clock, sleep=world.sleep)
    assert stats.rows_written == len(world.expected(world.clock.now()))
    assert T0 + timedelta(minutes=3) <= world.clock.now() <= T0 + timedelta(minutes=3, seconds=2)
    assert not stop.exists()
    assert world.events()[-1]["event"] == "stopped"
