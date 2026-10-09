"""Live tick collector (`aurum data collect`, Windows VM).

Repeatedly asks the terminal for every tick since the newest stored one
(``copy_ticks_range(cursor, now)``) and appends them to ``raw_ticks/mt5_live``.

Because it asks for a *range* from the cursor instead of sampling the latest quote, a
disconnect, an MT5 restart or a VM sleep loses nothing the terminal can still serve. On resume
the missed span is fetched from the terminal's tick history. What history cannot fill is
recorded as a gap. Guarantees:

* **No duplicates.** The cursor (newest stored time + number of stored rows at that ms) is
  derived from the files on disk. Rows already stored at the cursor ms are skipped on refetch.
* **No silent drops.** Every span longer than ``gap_record_seconds`` with no ticks while the
  market should be trading (``MarketHours``) is written to ``events.jsonl`` as a ``gap``.
  Collector downtime is recorded as an ``outage``, VM sleep or freeze as a ``pause``.
* **Graceful shutdown.** On stop, buffered ticks are flushed, the heartbeat is marked stopped
  and the terminal is released.

Limitation (measured in QA, not hidden): a tick the terminal inserts *behind* the cursor later
is not seen. The daily history backfill (``raw_ticks/mt5_history``) is the reference;
``aurum data qa`` compares live and history ticks for each day both exist.
"""

import contextlib
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import structlog

from aurum.adapters.mt5.snapshot import snapshot_if_changed
from aurum.adapters.storage.layout import DataLayout
from aurum.adapters.storage.live_ticks import CollectorJournal, LiveTickStore, TickCursor
from aurum.core.clock import SystemClock
from aurum.core.errors import AurumError, BrokerError
from aurum.data.market_hours import MarketHours
from aurum.ports.clock import ClockPort
from aurum.ports.market_data import MarketDataSource
from aurum.settings import CollectorConfig, Settings

log = structlog.get_logger(__name__)

MAX_BUFFER_ROWS = 500_000
FETCH_AHEAD = timedelta(minutes=1)
"""The fetch window ends a little after "now" so that nothing the terminal has is cut off."""


@dataclass
class CollectorStats:
    rows_written: int = 0
    chunks_written: int = 0
    connects: int = 0
    disconnects: int = 0
    gaps: int = 0
    pauses: int = 0


class TickCollector:
    def __init__(
        self,
        source: MarketDataSource,
        layout: DataLayout,
        cfg: CollectorConfig,
        market: MarketHours,
        *,
        clock: ClockPort,
        sleep: Callable[[float], None] = time.sleep,
        on_connect: Callable[[str], None] | None = None,
    ) -> None:
        """``on_connect(symbol)`` runs after every connect and once per UTC day (spec snapshots)."""
        self.source = source
        self.layout = layout
        self.cfg = cfg
        self.market = market
        self.clock = clock
        self._sleep_fn = sleep
        self.on_connect = on_connect
        self.stats = CollectorStats()
        self.symbol: str | None = None
        self.store: LiveTickStore | None = None
        self.journal: CollectorJournal | None = None
        self.cursor: TickCursor | None = None
        self._buffer: list[pl.DataFrame] = []
        self._buffer_rows = 0
        self._connected = False
        self._last_flush = clock.now()
        self._last_heartbeat: datetime | None = None
        self._last_loop: datetime | None = None
        self._last_tick_seen: datetime | None = None
        self._hook_day: date | None = None

    # ------------------------------------------------------------------ main loop
    def run(self, should_stop: Callable[[], bool]) -> CollectorStats:
        """Collect until ``should_stop()`` is true. Returns counters for this run."""
        started = self.clock.now()
        try:
            while not should_stop():
                if not self._connected:
                    self._connect_with_backoff(should_stop)
                    continue
                self._detect_pause()
                try:
                    self._poll()
                except BrokerError as exc:
                    self._on_disconnect(str(exc))
                    continue
                now = self.clock.now()
                if self._due_flush(now):
                    self._flush()
                if self._last_heartbeat is None or (now - self._last_heartbeat) >= timedelta(
                    seconds=self.cfg.heartbeat_seconds
                ):
                    self._heartbeat("running")
                    if self._hook_day != now.date():
                        self._call_on_connect()
                self._sleep(self.cfg.poll_seconds, should_stop)
        finally:
            self._shutdown(started)
        return self.stats

    # ------------------------------------------------------------------ connection
    def _connect_with_backoff(self, should_stop: Callable[[], bool]) -> None:
        delay = self.cfg.reconnect_initial_seconds
        while not should_stop():
            try:
                self.source.connect()
            except AurumError as exc:
                self._event("connect_failed", error=str(exc), retry_in_s=delay)
                self._sleep(delay, should_stop)
                delay = min(delay * 2, self.cfg.reconnect_max_seconds)
                continue
            self._connected = True
            self.stats.connects += 1
            if self.symbol is None:
                self._start(self.source.resolve_symbol())
            self._event("connected")
            self._call_on_connect()
            # Let the terminal sync history for the time it was away before asking for it.
            self._sleep(self.cfg.settle_seconds, should_stop)
            return

    def _start(self, symbol: str) -> None:
        """First connect of this run: open storage, find the resume point, record downtime."""
        self.symbol = symbol
        self.store = LiveTickStore(self.layout, symbol)
        self.journal = CollectorJournal(self.layout, symbol)
        removed = self.store.remove_stale_temps()
        self.cursor = self.store.tail()
        now = self.clock.now()
        heartbeat = self.journal.read_heartbeat()
        self._event(
            "started",
            cursor=self.cursor,
            removed_temp_files=removed,
            previous_status=heartbeat.get("status") if heartbeat else None,
        )
        if heartbeat is not None:
            since = heartbeat["time"]
            self._event(
                "outage",
                start=since,
                end=now,
                seconds=round((now - since).total_seconds(), 3),
                market_seconds=round(self.market.expected_data_seconds(since, now), 3),
                previous_status=heartbeat.get("status"),
            )

    def _on_disconnect(self, error: str) -> None:
        self.stats.disconnects += 1
        self._event("disconnected", error=error, cursor=self.cursor)
        self._flush()
        with contextlib.suppress(AurumError):
            self.source.disconnect()
        self._connected = False

    def _detect_pause(self) -> None:
        now = self.clock.now()
        last, self._last_loop = self._last_loop, now
        if last is None:
            return
        late = (now - last).total_seconds()
        if late <= self.cfg.pause_detect_seconds:
            return
        self.stats.pauses += 1
        self._event("pause", start=last, end=now, seconds=round(late, 3))
        if not self.source.is_connected():
            self._on_disconnect("terminal not connected after pause")

    # ------------------------------------------------------------------ polling
    def _poll(self) -> None:
        if not self._connected:
            return
        assert self.symbol is not None
        now = self.clock.now()
        start = self.cursor.time if self.cursor else self._day_start(now)
        frame = self.source.tick_frame(self.symbol, start, now + FETCH_AHEAD)
        new = self._skip_stored(frame)
        if new.is_empty():
            return
        self._record_gaps(new)
        self._advance_cursor(new)
        self._buffer.append(new)
        self._buffer_rows += new.height
        self._last_tick_seen = self.cursor.time if self.cursor else None
        if self._buffer_rows >= MAX_BUFFER_ROWS:
            self._flush()

    @staticmethod
    def _day_start(now: datetime) -> datetime:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _skip_stored(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Drop the leading rows at the cursor millisecond that are already stored."""
        if self.cursor is None or frame.is_empty():
            return frame
        at_cursor = frame["time"] == self.cursor.time
        leading = 0
        for flag in at_cursor:
            if not flag:
                break
            leading += 1
        if leading < self.cursor.rows_at_time:
            self._event(
                "history_changed",
                cursor=self.cursor,
                rows_now_at_cursor=leading,
                note="terminal returns fewer rows at the cursor ms than were stored",
            )
        return frame.slice(min(leading, self.cursor.rows_at_time))

    def _advance_cursor(self, new: pl.DataFrame) -> None:
        newest = new["time"].max()
        assert isinstance(newest, datetime)
        rows = int((new["time"] == newest).sum())
        if self.cursor is not None and self.cursor.time == newest:
            rows += self.cursor.rows_at_time
        self.cursor = TickCursor(time=newest, rows_at_time=rows)

    def _record_gaps(self, new: pl.DataFrame) -> None:
        """Record every tick-less span longer than ``gap_record_seconds`` of expected trading."""
        times = new["time"]
        previous = self.cursor.time if self.cursor else None
        threshold = self.cfg.gap_record_seconds
        candidates = pl.DataFrame({"t": times}).with_columns(prev=pl.col("t").cum_max().shift(1))
        if previous is not None:
            candidates = candidates.with_columns(pl.col("prev").fill_null(previous))
        candidates = candidates.drop_nulls().filter(
            (pl.col("t") - pl.col("prev")).dt.total_milliseconds() > threshold * 1000
        )
        for row in candidates.iter_rows(named=True):
            start, end = row["prev"], row["t"]
            expected = self.market.expected_data_seconds(start, end)
            if expected > threshold:
                self.stats.gaps += 1
                self._event(
                    "gap",
                    start=start,
                    end=end,
                    seconds=round((end - start).total_seconds(), 3),
                    market_seconds=round(expected, 3),
                )

    # ------------------------------------------------------------------ flushing
    def _due_flush(self, now: datetime) -> bool:
        return bool(self._buffer) and (now - self._last_flush) >= timedelta(
            seconds=self.cfg.flush_seconds
        )

    def _flush(self) -> None:
        self._last_flush = self.clock.now()
        if not self._buffer:
            return
        assert self.store is not None
        frame = pl.concat(self._buffer)
        paths = self.store.append(frame)
        self.stats.rows_written += frame.height
        self.stats.chunks_written += len(paths)
        log.info("collector.flush", rows=frame.height, files=[p.name for p in paths])
        self._buffer, self._buffer_rows = [], 0

    def _heartbeat(self, status: str) -> None:
        if self.journal is None:
            return
        now = self.clock.now()
        self._last_heartbeat = now
        age = (now - self._last_tick_seen).total_seconds() if self._last_tick_seen else None
        self.journal.write_heartbeat(
            now,
            status=status,
            connected=self._connected,
            cursor=self.cursor,
            last_tick_age_s=round(age, 3) if age is not None else None,
            market_open=self.market.is_open(now),
            buffered_rows=self._buffer_rows,
            rows_written=self.stats.rows_written,
        )
        log.info(
            "collector.heartbeat",
            status=status,
            connected=self._connected,
            last_tick_age_s=age,
            buffered=self._buffer_rows,
            written=self.stats.rows_written,
        )

    def _shutdown(self, started: datetime) -> None:
        try:
            if self._connected:  # last poll so a clean stop leaves nothing behind
                try:
                    self._poll()
                except BrokerError as exc:
                    self._event("final_poll_failed", error=str(exc))
            self._flush()
        finally:
            self._heartbeat("stopped")
            if self.journal is not None:
                self._event(
                    "stopped",
                    cursor=self.cursor,
                    run_seconds=round((self.clock.now() - started).total_seconds(), 3),
                    rows_written=self.stats.rows_written,
                )
            if self._connected:
                with contextlib.suppress(AurumError):
                    self.source.disconnect()
                self._connected = False

    # ------------------------------------------------------------------ helpers
    def _call_on_connect(self) -> None:
        self._hook_day = self.clock.now().date()
        if self.on_connect is None or self.symbol is None:
            return
        try:
            self.on_connect(self.symbol)
        except (AurumError, OSError) as exc:  # a failed spec snapshot must not stop collection
            self._event("on_connect_failed", error=str(exc))

    def _event(self, kind: str, **fields: object) -> None:
        log.info(f"collector.{kind}", symbol=self.symbol, **fields)
        if self.journal is not None:
            self.journal.event(kind, self.clock.now(), **fields)

    def _sleep(self, seconds: float, should_stop: Callable[[], bool]) -> None:
        """Sleep in steps of at most one second so a stop request is honoured quickly."""
        remaining = seconds
        while remaining > 0 and not should_stop():
            step = min(1.0, remaining)
            self._sleep_fn(step)
            remaining -= step


# ------------------------------------------------------------------------------ entry point


def stop_file_path(layout: DataLayout) -> Path:
    """Create this file to stop a running collector gracefully (works for detached runs)."""
    return layout.root / "collector" / "STOP"


def run_collector(
    settings: Settings,
    source: MarketDataSource,
    *,
    clock: ClockPort | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_seconds: float | None = None,
) -> CollectorStats:
    """Run until Ctrl+C / SIGTERM / SIGBREAK, a STOP file, or ``max_seconds``.

    Snapshots the symbol spec on every connect and once per UTC day if it changed.
    """
    clock = clock or SystemClock()
    layout = DataLayout(settings.data_dir)
    layout.ensure()
    stop_file = stop_file_path(layout)
    if stop_file.exists():
        stop_file.unlink()
        log.info("collector.stale_stop_file_removed", path=str(stop_file))
    deadline = clock.now() + timedelta(seconds=max_seconds) if max_seconds is not None else None
    requested: list[str] = []

    def on_signal(signum: int, _frame: object) -> None:
        requested.append(signal.Signals(signum).name)

    def should_stop() -> bool:
        return (
            bool(requested)
            or stop_file.exists()
            or (deadline is not None and clock.now() >= deadline)
        )

    def snapshot(symbol: str) -> None:
        path = snapshot_if_changed(
            settings.data_dir,
            source.symbol_info(symbol),
            source.symbol_snapshot(symbol),
            server=source.account_info().server,
        )
        if path is not None:
            log.info("collector.spec_snapshot", path=str(path))

    names = ["SIGINT", "SIGTERM", "SIGBREAK"]  # SIGBREAK: Ctrl+Break on Windows
    previous = {}
    for name in names:
        sig = getattr(signal, name, None)
        if sig is not None:
            previous[sig] = signal.signal(sig, on_signal)
    try:
        collector = TickCollector(
            source,
            layout,
            settings.config.data.collector,
            MarketHours(settings.config.data.market),
            clock=clock,
            sleep=sleep,
            on_connect=snapshot,
        )
        stats = collector.run(should_stop)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if stop_file.exists():
            stop_file.unlink()
    log.info("collector.exit", signals=requested, **vars(stats))
    return stats
