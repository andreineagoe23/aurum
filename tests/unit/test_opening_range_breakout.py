"""opening_range_breakout 0.1.0 rules (docs/strategies/opening_range_breakout.md) on synthetic
bars, with the real session calendar and news blackout from configs/.

Every bar of a day is flat at ``MID`` (open = high = low = close) unless a test sets it: range
bars by open time, decision-bar closes by close time. ``drive`` calls the strategy at every
decision-timeframe close with entries allowed, as the engine does.
"""

import itertools
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pytest
from pydantic import ValidationError

from aurum.core.enums import Side
from aurum.core.models import Signal
from aurum.core.timeframes import Timeframe
from aurum.data.market_hours import MarketHours
from aurum.features.view import BarSeries, BarStore, to_ms
from aurum.sessions.calendar import TradingCalendar
from aurum.sessions.news import NewsBlackout
from aurum.settings import AppConfig, EarlyClose
from aurum.strategies.opening_range_breakout import (
    RANGES,
    OpeningRangeBreakout,
    OpeningRangeBreakoutParams,
    RangeOutcome,
)
from aurum.strategies.registry import build_strategy, declared_grid

MID = 2001.00
WINTER = date(2024, 1, 17)  # Wednesday; GMT and EST: 09:30 New York = 14:30 London
TF = {"m15": Timeframe.M15, "m5": Timeframe.M5}


def z(day: date, hhmm: str) -> datetime:
    """``hhmm`` UTC on ``day``."""
    return datetime.combine(day, time.fromisoformat(hhmm), UTC)


def series(
    tf: Timeframe,
    day: date,
    *,
    ranges: dict[datetime, tuple[float, float]] | None = None,
    closes: dict[datetime, float] | None = None,
    drop: Iterable[datetime] = (),
    mid: float = MID,
) -> BarSeries:
    """Every ``tf`` bar of ``day`` 00:00-21:00Z. ``ranges``: (high, low) by open time;
    ``closes``: close by close time; ``drop``: close times of missing bars."""
    step = tf.seconds * 1000
    opens = np.arange(to_ms(z(day, "00:00")), to_ms(z(day, "21:00")), step, dtype=np.int64)
    opens = opens[~np.isin(opens + step, [to_ms(t) for t in drop])]
    n = len(opens)
    c, h, lo = np.full(n, mid), np.full(n, mid), np.full(n, mid)
    for t, (hi, low) in (ranges or {}).items():
        i = int(np.searchsorted(opens, to_ms(t)))
        h[i], lo[i] = hi, low
    for t, v in (closes or {}).items():
        i = int(np.searchsorted(opens, to_ms(t) - step))
        c[i] = v
        h[i], lo[i] = max(h[i], v), min(lo[i], v)
    return BarSeries(
        timeframe=tf, open_time=opens, close_time=opens + step, open=c.copy(), high=h, low=lo,
        close=c, spread_min=np.zeros(n, np.int64), tick_count=np.ones(n, np.int64),
    )  # fmt: skip


def market(
    day: date,
    rng: dict[datetime, tuple[float, float]],
    *,
    m15: dict[datetime, float] | None = None,
    m5: dict[datetime, float] | None = None,
    drop: Iterable[datetime] = (),
    mid: float = MID,
) -> BarStore:
    drop = list(drop)
    return BarStore({
        Timeframe.M15: series(Timeframe.M15, day, ranges=rng, closes=m15, drop=drop, mid=mid),
        Timeframe.M5: series(Timeframe.M5, day, closes=m5, drop=drop, mid=mid),
    })  # fmt: skip


def orb(
    cfg: AppConfig, variant: str = "m15", *, stops_level: int = 0, **params: float
) -> OpeningRangeBreakout:
    calendar = TradingCalendar(cfg.sessions, MarketHours(cfg.data.market))
    news = NewsBlackout(cfg.risk.news_blackout)
    strategy_cfg = cfg.strategies["opening_range_breakout"]
    built = build_strategy(
        strategy_cfg.model_copy(update={"params": {**strategy_cfg.params, **params}}),
        variant=variant,
        symbol="XAUUSD",
        point=0.01,
        session_at=lambda t: s.session if (s := calendar.state_at(t)).entries_allowed else None,
        blackout=news.reason_at,
        stops_level_points=stops_level,
    )
    assert isinstance(built, OpeningRangeBreakout)
    return built


def drive(s: OpeningRangeBreakout, store: BarStore, day: date) -> list[Signal]:
    """Call the strategy at every decision close of ``day`` in a session, as the engine does."""
    out, t = [], z(day, "00:00")
    step = timedelta(seconds=s.decision_timeframe.seconds)
    while t < z(day, "21:00"):
        t += step
        if s._session_at(t) is not None and (signal := s.on_bar(store.view_at(t))) is not None:
            out.append(signal)
    return out


def outcome(s: OpeningRangeBreakout, rng: str) -> RangeOutcome:
    (found,) = [o for o in s.outcomes() if o.range == rng]
    return found


def brief(signal: Signal) -> tuple[Side, datetime, float, float | None, timedelta | None]:
    return signal.side, signal.time, signal.stop_loss, signal.take_profit, signal.max_hold


def test_params_grid_and_registry(app_config: AppConfig) -> None:
    for bad in ({"or_bars": 3}, {"target_r": 2.5}, {"stop_buffer_points": 10}):
        with pytest.raises(ValidationError):
            OpeningRangeBreakoutParams.model_validate(bad)
    cfg = app_config.strategies["opening_range_breakout"]
    grid = declared_grid(cfg)
    points = [(p["or_bars"], p["target_r"]) for p in grid]
    assert points == list(itertools.product((2, 4), (1.0, 1.5, 2.0)))
    assert len(grid) * len(cfg.variants) == 12  # the declared trials, no more
    assert all(OpeningRangeBreakoutParams.model_validate(p) for p in grid)
    assert len(declared_grid(app_config.strategies["trend_pullback"])) == 27  # unchanged
    s = orb(app_config, "m5")
    assert (s.decision_timeframe, dict(s.warmup_bars)) == (Timeframe.M5, {})
    assert set(s.diagnostics().values()) == {0}
    with pytest.raises(ValueError, match="session calendar"):
        build_strategy(cfg, variant="m15", symbol="XAUUSD", point=0.01)


def test_the_section_17_trace_is_reproduced(app_config: AppConfig) -> None:
    """2024-03-01 development bars (evidence section 6); flat bars elsewhere sit inside both
    ranges (London [2042.04, 2045.71], New York [2045.62, 2054.85])."""
    day = date(2024, 3, 1)
    rng = {
        z(day, "08:00"): (2045.71, 2043.25), z(day, "08:15"): (2043.89, 2042.04),
        z(day, "14:30"): (2054.85, 2051.29), z(day, "14:45"): (2051.70, 2045.62),
    }  # fmt: skip
    store = market(
        day, rng, mid=2045.65,
        m15={z(day, "08:45"): 2040.58, z(day, "15:15"): 2055.71},
        m5={z(day, "08:35"): 2041.32, z(day, "15:05"): 2050.71, z(day, "15:10"): 2052.63,
            z(day, "15:15"): 2055.71},
    )  # fmt: skip
    long = (Side.BUY, z(day, "15:15"), 2045.42, 2071.14, timedelta(minutes=120))
    m15 = orb(app_config, "m15")
    assert [brief(s) for s in drive(m15, store, day)] == [
        (Side.SELL, z(day, "08:45"), 2045.91, 2032.59, timedelta(minutes=120)), long,
    ]  # fmt: skip
    assert [(o.status, o.width_points, o.stop_points) for o in m15.outcomes()] == [
        ("signal", 367, 533), ("signal", 923, 1029),
    ]  # fmt: skip
    m5 = orb(app_config, "m5")
    assert [brief(s) for s in drive(m5, store, day)] == [long]
    london, newyork = m5.outcomes()
    assert (london.status, london.spend_time, london.stop_points) == (
        "stop_below_minimum", z(day, "08:35"), 459,
    )  # fmt: skip
    assert (newyork.blackout_skipped, newyork.eligible) == (0, 1)  # 10:05, 10:10 were inside


def test_anchors_windows_and_early_closes_follow_each_calendar(app_config: AppConfig) -> None:
    """Eligible decision times on a flat market (no break) match spec section 6, per city's
    daylight time; the New York blackout instants 10:05 and 10:10 are never eligible."""
    weeks = {  # date: (London anchor, New York anchor) in UTC, normal or mismatch week
        WINTER: ("08:00", "14:30", "normal"),
        date(2024, 3, 13): ("08:00", "13:30", "mismatch"),  # US on EDT, UK still on GMT
        date(2024, 7, 17): ("07:00", "13:30", "normal"),
        date(2024, 10, 30): ("08:00", "13:30", "mismatch"),  # UK back on GMT, US still EDT
    }
    eligible = {  # (range, or_bars, variant, week) -> eligible decision times (section 6)
        **{("london", n, v, w): k for (n, v), k in
           {(2, "m15"): 8, (2, "m5"): 24, (4, "m15"): 7, (4, "m5"): 23}.items()
           for w in ("normal", "mismatch")},
        ("newyork", 2, "m15", "normal"): 5, ("newyork", 2, "m5", "normal"): 15,
        ("newyork", 4, "m15", "normal"): 3, ("newyork", 4, "m5", "normal"): 11,
        ("newyork", 2, "m15", "mismatch"): 8, ("newyork", 2, "m5", "mismatch"): 22,
        ("newyork", 4, "m15", "mismatch"): 7, ("newyork", 4, "m5", "mismatch"): 23,
    }  # fmt: skip
    for day, (london, newyork, week) in weeks.items():
        anchors = [r.anchor_utc(day) for r in RANGES]
        assert anchors == [z(day, london), z(day, newyork)], day
        for n, v in itertools.product((2, 4), ("m15", "m5")):
            s = orb(app_config, v, or_bars=n)
            assert drive(s, market(day, {}), day) == []
            for o in s.outcomes():
                case = (o.range, n, v, week)
                assert (o.status, o.eligible) == ("no_break", eligible[case]), case
    # A London instant inside another session is not a decision time of the london range.
    moved = {"london_morning": {"start": "07:00", "end": "10:00"},
             "ldn_ny_overlap": {"start": "10:00", "end": "16:30"}}  # fmt: skip
    sessions = app_config.sessions.model_validate(
        {**app_config.sessions.model_dump(), "sessions": moved}
    )
    s = orb(app_config.model_copy(update={"sessions": sessions}), "m15")
    drive(s, market(WINTER, {}), WINTER)
    assert outcome(s, "london").eligible == 5  # 08:45 .. 09:45; 10:00 .. 10:30 are excluded
    # An early close at 11:00 New York (16:00 London): flat by 15:45, so the newyork windows
    # are clipped. or_bars 2, m15 keeps 15:15 and 15:30; or_bars 4 starts at 15:45: none.
    hours = app_config.data.market
    early = {**hours.early_closes, WINTER: EarlyClose(close=time(11), reason="test")}
    clipped = app_config.model_copy(
        update={"data": app_config.data.model_copy(
            update={"market": hours.model_copy(update={"early_closes": early})})}
    )  # fmt: skip
    for n, status, count in ((2, "no_break", 2), (4, "no_decision_time", 0)):
        s = orb(clipped, "m15", or_bars=n)
        drive(s, market(WINTER, {}), WINTER)
        assert (outcome(s, "newyork").status, outcome(s, "newyork").eligible) == (status, count)
        assert s.diagnostics()["status_no_decision_time"] == (n == 4)


def test_breakout_boundaries_stop_bounds_and_rounding(app_config: AppConfig) -> None:
    """London m15 on WINTER: range bars 08:00 and 08:15Z, range_end 08:30Z."""
    a, b = z(WINTER, "08:00"), z(WINTER, "08:15")
    t1, t2, t3, t4 = (z(WINTER, h) for h in ("08:45", "09:00", "09:15", "09:30"))
    # Equality is inside; the first strictly outside close spends; later ones never signal.
    for side, closes, stop, target in (
        (Side.BUY, {t1: 2010.00, t2: 2000.00, t3: 2010.01, t4: 2030.00}, 1999.80, 2025.32),
        (Side.SELL, {t1: 2000.00, t2: 2010.00, t3: 1999.99, t4: 1980.00}, 2010.20, 1984.68),
    ):
        s = orb(app_config, "m15")
        store = market(WINTER, {a: (2010.00, 2004.00), b: (2006.00, 2000.00)}, m15=closes)
        signals = drive(s, store, WINTER)
        assert [brief(x) for x in signals] == [(side, t3, stop, target, timedelta(minutes=120))]
        assert (outcome(s, "london").stop_points, outcome(s, "london").eligible) == (1021, 3)
        assert signals[0].features["width_points"] == 1000
    # Float noise in a boundary is removed before rounding (round(x / point, 6)).
    noisy = market(WINTER, {a: (2010.0, 2000.0 - 1e-10)}, m15={t1: 2010.01})
    assert 2000.0 - 1e-10 - 0.20 < 1999.80  # floor without round(..., 6) would give 1999.79
    assert drive(orb(app_config), noisy, WINTER)[0].stop_loss == 1999.80
    # Off the grid (never in canonical bars): a long stop rounds down, a short one up.
    for lo_hi, close, stop in (
        ((2010.0, 2000.005), 2010.01, 1999.80),
        ((2010.005, 2000.0), 1999.99, 2010.21),
    ):
        off = drive(orb(app_config), market(WINTER, {a: lo_hi}, m15={t1: close}), WINTER)
        assert off[0].stop_loss == stop, lo_hi
    # Bounds, inclusive: d = W + 21 for a close 1 point above H. A refused stop still spends.
    for high, stops_level, status in (
        (2004.79, 0, "signal"), (2004.78, 0, "stop_below_minimum"),
        (2004.79, 495, "stop_below_minimum"),  # minimum max(500, 495 + 10) = 505
        (2004.84, 495, "signal"),
        (2019.79, 0, "signal"), (2019.80, 0, "stop_above_cap"),
    ):  # fmt: skip
        s = orb(app_config, "m15", stops_level=stops_level)
        closes = {t1: round(high + 0.01, 2), t2: high + 2.00}  # t2 would be a valid stop
        signals = drive(s, market(WINTER, {a: (high, 2000.00)}, m15=closes), WINTER)
        o = outcome(s, "london")
        assert (o.status, o.spend_time, len(signals)) == (
            status, t1, 1 if status == "signal" else 0,
        ), (high, stops_level)  # fmt: skip
        assert s.diagnostics()[status if status != "signal" else "status_signal"] == 1


def test_blackout_instants_and_data_gaps(app_config: AppConfig) -> None:
    """New York m5 on WINTER: range 14:30-15:00Z; 15:05 and 15:10Z (10:05, 10:10 New York)
    are blackout instants; 15:15Z is the first eligible one."""
    rng = {z(WINTER, "14:30"): (2010.00, 2000.00), z(WINTER, "14:45"): (2006.00, 2004.00)}
    t05, t10, t15, t20 = (z(WINTER, h) for h in ("15:05", "15:10", "15:15", "15:20"))
    cases = {
        # A close outside at a blackout instant neither signals nor spends (O3).
        "skipped": ({t05: 2015.0, t10: 2015.0, t15: 2005.0, t20: 2015.0}, (), "signal", 2, 0),
        "back inside": ({t05: 2015.0, t10: 1995.0}, (), "no_break", 2, 0),
        # A missing bar at a blackout instant (15:05) stays in every later read set: the 15:10
        # close outside is not counted as skipped, and nothing can spend the range.
        "gap at blackout": ({t10: 2015.0, t20: 2015.0}, (t05,), "data_gap", 0, 15),
        "range missing": ({t20: 2015.0}, (z(WINTER, "15:00"),), "range_missing", 0, 15),
        "later gap": ({t20: 2005.0}, (z(WINTER, "16:00"),), "data_gap", 0, 6),
    }
    for name, (closes, drop, status, skipped, gaps) in cases.items():
        s = orb(app_config, "m5")
        signals = drive(s, market(WINTER, rng, m5=closes, drop=drop), WINTER)
        o = outcome(s, "newyork")
        assert (o.status, o.blackout_skipped, o.data_gap) == (status, skipped, gaps), name
        assert [x.time for x in signals] == ([t20] if status == "signal" else []), name
        d = s.diagnostics()
        assert (d["blackout_skipped"], d["data_gap"]) == (skipped, gaps), name


def test_one_use_restart_and_deduplicated_diagnostics(app_config: AppConfig) -> None:
    rng = {z(WINTER, "08:00"): (2010.00, 2000.00), z(WINTER, "14:30"): (2010.00, 2000.00)}
    spend = z(WINTER, "09:15")
    store = market(
        WINTER, rng,
        m15={spend: 2010.01, z(WINTER, "09:30"): 2030.0, z(WINTER, "15:15"): 1995.0},
        drop=[z(WINTER, "16:00")],  # a data gap after the New York spend counts nothing
    )  # fmt: skip
    full = orb(app_config)
    signals = drive(full, store, WINTER)
    assert [x.time for x in signals] == [spend, z(WINTER, "15:15")]
    # A restart before the spend decides the same signal (same key); after it, nothing:
    # the spend is re-derived from the bars.
    assert orb(app_config).on_bar(store.view_at(spend)) == signals[0]
    late = orb(app_config)
    assert late.on_bar(store.view_at(z(WINTER, "09:45"))) is None
    assert outcome(late, "london").spend_time == spend
    # One walk at the last decision time counts exactly what calls at every instant counted,
    # and repeated or earlier calls never count an instant twice.
    once = orb(app_config)
    last = z(WINTER, "16:15")
    once.on_bar(store.view_at(last))
    for t in (last, spend, last, z(WINTER, "10:00")):
        once.on_bar(store.view_at(t))
    assert once.diagnostics() == full.diagnostics()
    assert [(o.status, o.eligible, o.final) for o in once.outcomes()] == [
        (o.status, o.eligible, o.final) for o in full.outcomes()
    ]
    assert full.diagnostics()["status_signal"] == 2
    assert full.invalidation(store.view_at(last), None, signals[0]) is None  # type: ignore[arg-type]
