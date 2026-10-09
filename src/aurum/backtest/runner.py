"""Chronological replay: drive the shared ``Engine`` over history with a ``SimBroker``.

Wake-ups (all UTC, strictly increasing):

* every close of ``step`` (M5) from each trading day's pre-session start to its cutoff;
* every phase boundary of the day (session starts/ends, no-new-entries, force-flat), so
  early-close cutoffs are met to the minute;
* every pending ``max_hold`` deadline of an open position;
* every ``step`` close while exposure remains outside the schedule (e.g. a close that failed
  for lack of quotes is retried).

At each wake ``t`` the broker first processes every quote before ``t`` (fills, stops, targets),
then the engine decides with a view of bars that closed by ``t``. Nothing after ``t`` is
reachable from the strategy.
"""

import heapq
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from aurum.backtest.broker import SimBroker, SimTrade
from aurum.backtest.market import HistoricalMarket
from aurum.core.clock import ManualClock
from aurum.core.enums import ExitReason
from aurum.core.models import Signal
from aurum.core.timeframes import Timeframe
from aurum.execution.engine import Engine, JournalEntry
from aurum.sessions.clock import SessionClock


@dataclass(frozen=True, slots=True)
class EquityPoint:
    time: datetime
    balance: float
    equity: float
    open_positions: int


@dataclass(frozen=True, slots=True)
class BacktestResult:
    start: datetime
    end: datetime
    trades: list[SimTrade]
    journal: list[JournalEntry]
    exit_reasons: dict[int, ExitReason]
    signals: dict[str, Signal]
    """Idempotency key -> the signal behind each traded position."""
    equity: list[EquityPoint]
    open_at_end: int
    """Positions still open after the final flatten attempt (should be 0)."""


def schedule(
    calendar: SessionClock, first: date, last: date, step: Timeframe = Timeframe.M5
) -> Iterator[datetime]:
    """Static wake-ups for local trading dates ``first..last`` (inclusive), increasing."""
    d = first
    while d <= last:
        sched = calendar.schedule_for(d)
        if sched is not None:
            times = {
                sched.pre_session_start,
                sched.no_new_entries_after,
                sched.force_flat_at,
                *(t for w in sched.sessions for t in (w.start, w.end)),
            }
            t = step.bar_open_time(sched.pre_session_start)
            if t < sched.pre_session_start:
                t += step.duration
            while t <= sched.force_flat_at:
                times.add(t)
                t += step.duration
            yield from sorted(times)
        d += timedelta(days=1)


def run_backtest(
    *,
    engine: Engine,
    broker: SimBroker,
    market: HistoricalMarket,
    calendar: SessionClock,
    clock: ManualClock,
    first: date,
    last: date,
    step: Timeframe = Timeframe.M5,
) -> BacktestResult:
    """Replay local trading dates ``first..last``; returns trades, journal and equity."""
    start = calendar.local_to_utc(first, datetime.min.time())
    end = calendar.local_to_utc(last + timedelta(days=1), datetime.min.time())
    static = schedule(calendar, first, last, step)
    queue: list[datetime] = []
    nxt = next(static, None)
    if nxt is not None:
        heapq.heappush(queue, nxt)
    equity: list[EquityPoint] = []
    now: datetime | None = None
    while queue:
        t = heapq.heappop(queue)
        if now is not None and t <= now:
            continue
        if t >= end:
            break
        if t == nxt:
            nxt = next(static, None)
            if nxt is not None:
                heapq.heappush(queue, nxt)
        clock.set(t)
        broker.advance_to(t)
        engine.step(market.store.view_at(t))
        now = t
        account = broker.account_info()
        exposed = len(broker.positions()) + len(broker.orders())
        equity.append(EquityPoint(t, account.balance, account.equity, len(broker.positions())))
        deadline = engine.next_deadline_after(t)
        if deadline is not None:
            heapq.heappush(queue, deadline)
        if exposed:
            heapq.heappush(queue, step.bar_open_time(t) + step.duration)

    clock.set(max(end, clock.now()))
    broker.advance_to(clock.now())
    if broker.positions() or broker.orders():
        engine.flatten(clock.now(), ExitReason.END_OF_DATA, "end of backtest")
    signals = {
        t.idempotency_key: s
        for t in broker.trades
        if t.idempotency_key and (s := engine.signal_for(t.idempotency_key)) is not None
    }
    return BacktestResult(
        start=start,
        end=end,
        trades=broker.trades,
        journal=list(engine.journal),
        exit_reasons=dict(engine.exit_reasons),
        signals=signals,
        equity=equity,
        open_at_end=len(broker.positions()),
    )
