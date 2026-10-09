from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from aurum.adapters.storage.layout import DataLayout
from aurum.adapters.storage.live_ticks import LiveTickStore
from aurum.adapters.storage.parquet import write_parquet
from aurum.data.market_hours import MarketHours
from aurum.data.qa import Gap, coverage, explain, live_vs_history, stale_quotes
from aurum.data.schemas import RAW_TICKS, polars_schema
from aurum.sessions.clock import SessionClock
from aurum.settings import AppConfig, KnownGap

WED = date(2026, 10, 7)  # trading date: Tue 18:00 -> Wed 16:59 New York (22:00 -> 20:59 UTC)


@pytest.fixture
def market(app_config: AppConfig) -> MarketHours:
    return MarketHours(app_config.data.market)


@pytest.fixture
def clock(app_config: AppConfig) -> SessionClock:
    return SessionClock(app_config.sessions)


def full_day(market: MarketHours, d: date) -> list[datetime]:
    s = market.session(d)
    assert s is not None
    t, out = s.open, []
    while t < s.close:
        out.append(t)
        t += timedelta(minutes=1)
    return out


def series(times: list[datetime]) -> pl.Series:
    return pl.Series("t", times, dtype=pl.Datetime("ms", "UTC"))


def test_complete_day_has_no_findings(market: MarketHours, clock: SessionClock) -> None:
    missing, session, other, outside, n = coverage(
        series(full_day(market, WED)), market, clock, WED, WED
    )
    assert (missing, session, other, outside, n) == ([], [], [], [], 1)


def test_late_first_tick_within_grace_is_fine(market: MarketHours, clock: SessionClock) -> None:
    times = full_day(market, WED)[2:]  # 18:00 and 18:01 New York absent
    assert coverage(series(times), market, clock, WED, WED)[1:4] == ([], [], [])


def test_gap_inside_london_session_is_a_session_gap(
    market: MarketHours, clock: SessionClock
) -> None:
    hole = {datetime(2026, 10, 7, 8, 10, tzinfo=UTC) + timedelta(minutes=i) for i in range(3)}
    times = [t for t in full_day(market, WED) if t not in hole]
    _, session, other, _, _ = coverage(series(times), market, clock, WED, WED)
    assert session == [
        Gap(
            datetime(2026, 10, 7, 8, 10, tzinfo=UTC),
            datetime(2026, 10, 7, 8, 13, tzinfo=UTC),
            3,
            WED,
            ("london_morning",),
        )
    ]
    assert other == []


def test_gap_outside_sessions_is_only_a_market_gap(
    market: MarketHours, clock: SessionClock
) -> None:
    gone = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)  # Asian hours
    times = [t for t in full_day(market, WED) if t != gone]
    _, session, other, _, _ = coverage(series(times), market, clock, WED, WED)
    assert session == []
    assert [(g.start, g.minutes, g.sessions) for g in other] == [(gone, 1, ())]


def test_missing_weekday_and_closed_date(market: MarketHours, clock: SessionClock) -> None:
    good_friday = date(2026, 4, 3)
    thu = date(2026, 4, 2)
    missing, *_ = coverage(series(full_day(market, thu)), market, clock, thu, date(2026, 4, 6))
    assert missing == [date(2026, 4, 6)]  # Monday missing; Good Friday is configured closed
    assert market.session(good_friday) is None


def test_bars_in_daily_break_or_weekend_are_violations(
    market: MarketHours, clock: SessionClock
) -> None:
    in_break = datetime(2026, 10, 7, 21, 15, tzinfo=UTC)  # 17:15 New York
    saturday = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    times = [*full_day(market, WED), in_break, saturday]
    *_, outside, _ = coverage(series(times), market, clock, WED, date(2026, 10, 9))
    assert [g.start for g in outside] == [in_break, saturday]


def test_early_close_is_respected(app_config: AppConfig, clock: SessionClock) -> None:
    cfg = app_config.data.market.model_copy(
        update={"early_closes": {WED: app_config.data.market.early_closes[date(2026, 9, 7)]}}
    )
    market = MarketHours(cfg)
    s = market.session(WED)
    assert s is not None
    times = full_day(market, WED)
    assert coverage(series(times), market, clock, WED, WED)[1:4] == ([], [], [])
    late = times[-1] + timedelta(minutes=5)
    assert coverage(series([*times, late]), market, clock, WED, WED)[3][0].start == late


def test_known_gaps_explain_only_what_they_cover() -> None:
    t = datetime(2026, 10, 7, 8, 10, tzinfo=UTC)
    inside = Gap(t, t + timedelta(minutes=2), 2, WED, ("london_morning",))
    beyond = Gap(t, t + timedelta(minutes=9), 9, WED, ("london_morning",))
    known = [KnownGap(start=t, end=t + timedelta(minutes=5), reason="broker feed gap")]
    unexplained, explained = explain([inside, beyond], known)
    assert unexplained == [beyond]
    assert explained[0]["reason"] == "broker feed gap"


def test_stale_quotes_per_session(clock: SessionClock) -> None:
    from aurum.data.qa import _session_windows

    base = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # London morning (BST: 07:00-11:00 = 06-10 UTC)
    times = [
        base,
        base + timedelta(seconds=3),
        base + timedelta(seconds=40),
        base + timedelta(seconds=41),
    ]
    night = datetime(2026, 10, 7, 2, 0, tzinfo=UTC)
    times += [night, night + timedelta(seconds=100)]  # outside sessions: ignored
    ticks = pl.DataFrame({"time": series(sorted(times))})
    out = stale_quotes(ticks, _session_windows(clock, WED, WED), 10)
    assert set(out) == {"london_morning"}
    assert out["london_morning"].intervals == 1
    assert out["london_morning"].max_seconds == pytest.approx(37.0)


def test_live_vs_history_compares_time_bid_ask_multisets(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    t = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)

    def frame(rows: list[tuple[int, float]], flags: int) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "time": [t + timedelta(milliseconds=ms) for ms, _ in rows],
                "bid": [b for _, b in rows],
                "ask": [b + 0.1 for _, b in rows],
                "last": [0.0] * len(rows),
                "volume": [0] * len(rows),
                "flags": [flags] * len(rows),
                "volume_real": [0.0] * len(rows),
            },
            schema=polars_schema(RAW_TICKS),
        )

    live = [(0, 4000.0), (0, 4000.0), (5, 4000.1), (9, 4000.3)]
    hist = [(0, 4000.0), (5, 4000.1), (7, 4000.2)]
    LiveTickStore(layout, "XAUUSD").append(frame(live, 6))
    write_parquet(frame(hist, 134), layout.raw_ticks("XAUUSD", WED), RAW_TICKS, overwrite=False)
    [row] = live_vs_history(layout, "XAUUSD")
    assert (row["only_live"], row["only_history"]) == (2, 1)  # flags differ by design: ignored
