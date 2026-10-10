"""trend_pullback 0.2.0 rules (docs/strategies/trend_pullback.md) on synthetic bars.

The base market is built so the numbers are exact:
- flat history at 2000.00, then a rising M15 tail b7..b0 = 2001..2007, 2009, closing 14:15Z;
- every bar's true range is 6.00 (high = close + 1.25, low = close - 4.75), so ATR(14) = 6.00;
- H1 flat, then rising, to give a long regime.

Boundary values come from ``ema_last``, the spec's own EMA definition, over the same 500-bar
windows.
"""

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from pydantic import ValidationError

from aurum.core.enums import Side
from aurum.core.models import PositionState, Signal
from aurum.core.timeframes import Timeframe
from aurum.features.indicators import ema_last
from aurum.features.view import BarSeries, BarStore, to_ms
from aurum.settings import AppConfig
from aurum.strategies.registry import build_strategy
from aurum.strategies.trend_pullback import TrendPullback, TrendPullbackParams

T0 = datetime(2024, 3, 1, 14, 15, tzinfo=UTC)  # the setup bar b0 closes here
FLAT = 520
TAIL = [2001.0, 2002.0, 2003.0, 2004.0, 2005.0, 2006.0, 2007.0, 2009.0]  # b7 .. b0
UP, DOWN = 1.25, 4.75


def series(
    tf: Timeframe,
    closes: list[float],
    last_close: datetime,
    *,
    lows: dict[int, float] | None = None,
    highs: dict[int, float] | None = None,
) -> BarSeries:
    """Consecutive bars ending at ``last_close``; ``lows``/``highs`` override by index."""
    n, step = len(closes), tf.seconds * 1000
    close_time = to_ms(last_close) - step * (n - 1 - np.arange(n, dtype=np.int64))
    c = np.array(closes, dtype=np.float64)
    high, low = c + UP, c - DOWN
    for i, v in (lows or {}).items():
        low[i] = v
    for i, v in (highs or {}).items():
        high[i] = v
    return BarSeries(
        timeframe=tf,
        open_time=close_time - step,
        close_time=close_time,
        open=c.copy(),
        high=high,
        low=low,
        close=c,
        spread_min=np.zeros(n, dtype=np.int64),
        tick_count=np.ones(n, dtype=np.int64),
    )


def h1(extra: list[float] | None = None) -> BarSeries:
    closes = [2000.0] * FLAT + [2001.0, 2002.0, 2003.0, 2004.0, 2005.0] + (extra or [])
    return series(Timeframe.H1, closes, T0.replace(minute=0) + timedelta(hours=len(extra or [])))


def m15_closes(after: list[float] | None = None) -> list[float]:
    return [2000.0] * FLAT + TAIL + (after or [])


def strategy(
    variant: str = "m15", allowed: Callable[[datetime], bool] = lambda t: True
) -> TrendPullback:
    tf = Timeframe.M15 if variant == "m15" else Timeframe.M5
    return TrendPullback(
        TrendPullbackParams(),
        symbol="XAUUSD",
        point=0.01,
        decision_timeframe=tf,
        entries_allowed=allowed,
    )


def at(store: BarStore, s: TrendPullback, now: datetime) -> Signal | None:
    return s.on_bar(store.view_at(now))


def ema(closes: list[float], end: int, period: int) -> float:
    """The spec's EMA at bar ``end`` (negative index allowed) over its 500-bar window."""
    end = end % len(closes)
    value = ema_last(np.array(closes[end - 499 : end + 1]), period)
    assert value is not None
    return value


def mirror(bars: BarSeries) -> BarSeries:
    """The same market reflected around 2000: every long rule becomes the short one."""
    return BarSeries(
        timeframe=bars.timeframe,
        open_time=bars.open_time,
        close_time=bars.close_time,
        open=4000.0 - bars.open,
        high=4000.0 - bars.low,
        low=4000.0 - bars.high,
        close=4000.0 - bars.close,
        spread_min=bars.spread_min,
        tick_count=bars.tick_count,
    )


def test_ema_definition_params_and_warmup(app_config: AppConfig) -> None:
    assert ema_last(np.array([1.0, 2.0, 3.0, 4.0]), 2) == pytest.approx(3.5)  # seed 1.5, alpha 2/3
    assert ema_last(np.array([1.0]), 2) is None
    with pytest.raises(ValidationError, match="stop_atr_mult must be one of"):
        TrendPullbackParams(stop_atr_mult=0.3)
    with pytest.raises(ValidationError, match="target_r must be one of"):
        TrendPullbackParams(target_r=3.0)
    with pytest.raises(ValidationError):
        TrendPullbackParams(h1_ema=40)  # type: ignore[arg-type]
    built = build_strategy(
        app_config.strategies["trend_pullback"],
        variant="m5",
        symbol="XAUUSD",
        point=0.01,
        entries_allowed=lambda t: True,
    )
    assert built.decision_timeframe is Timeframe.M5
    assert dict(built.warmup_bars) == {Timeframe.H1: 504, Timeframe.M15: 509, Timeframe.M5: 3}
    with pytest.raises(ValueError, match="session calendar"):
        build_strategy(
            app_config.strategies["trend_pullback"], variant="m15", symbol="X", point=0.01
        )


def test_m15_signal_and_rule_boundaries() -> None:
    closes = m15_closes()
    e20_b3, e20_b1, e50_b5 = ema(closes, -4, 20), ema(closes, -2, 20), ema(closes, -6, 50)
    no_wick = dict(zip(range(-4, 0), closes[-4:], strict=True))  # lows b3..b0 = close

    def store(m15: BarSeries, hourly: BarSeries | None = None) -> BarStore:
        return BarStore({Timeframe.H1: hourly or h1(), Timeframe.M15: m15})

    base = series(Timeframe.M15, closes, T0)
    sig = at(store(base), strategy(), T0)
    assert sig is not None
    assert (sig.side, sig.stop_loss, sig.take_profit, sig.max_hold) == (
        Side.BUY,
        1993.25,  # min low 1996.25 (b7) - 0.5 x ATR 6.00, rounded down
        2032.62,  # 2009 + 1.5 x 15.75 = 2032.625, rounded down (toward the entry)
        timedelta(minutes=180),
    )
    assert sig.features["stop_points"] == 1575.0
    short = at(BarStore({Timeframe.H1: mirror(h1()), Timeframe.M15: mirror(base)}), strategy(), T0)
    assert short is not None
    assert (short.side, short.stop_loss, short.take_profit) == (Side.SELL, 2006.75, 1967.38)

    no_signal: list[tuple[str, BarStore, Callable[[datetime], bool]]] = [
        ("touch just above EMA20 (b3)", store(series(Timeframe.M15, closes, T0,
            lows={**no_wick, -4: math.nextafter(e20_b3, math.inf)})), lambda t: True),
        ("no touch at all in b0..b3", store(series(Timeframe.M15, closes, T0, lows=no_wick)),
            lambda t: True),
        ("close(b4) == EMA50(b4): strict", store(series(Timeframe.M15,
            [*closes[:-5], e50_b5, *closes[-4:]], T0)), lambda t: True),
        ("close(b0) == EMA20(b0): strict", store(series(Timeframe.M15,
            [*closes[:-1], e20_b1], T0)), lambda t: True),
        ("close(b0) == high(b1): strict", store(series(Timeframe.M15, closes, T0,
            highs={-2: closes[-1]})), lambda t: True),
        ("H1 slope 0: no regime", store(base, series(Timeframe.H1, [2000.0] * 530,
            T0.replace(minute=0))), lambda t: True),
        ("setup bar is not a decision time", store(base), lambda t: False),
    ]  # fmt: skip
    for name, market, allowed in no_signal:
        assert at(market, strategy(allowed=allowed), T0) is None, name
    touch_equal = store(series(Timeframe.M15, closes, T0, lows={**no_wick, -4: e20_b3}))
    assert at(touch_equal, strategy(), T0) is not None, "low(b3) == EMA20(b3) counts"

    # A missing bar inside b0..b7 is a data gap: no setup, counted.
    gap = series(Timeframe.M15, closes, T0)
    keep = np.ones(len(gap), dtype=bool)
    keep[-6] = False  # b5 missing
    gapped = BarSeries(
        timeframe=Timeframe.M15,
        **{f: getattr(gap, f)[keep] for f in ("open_time", "close_time", "open", "high", "low",
                                               "close", "spread_min", "tick_count")},
    )  # fmt: skip
    s = strategy()
    assert at(store(gapped), s, T0) is None
    assert at(store(base), s, T0 + timedelta(minutes=15)) is None  # no bar closing at T
    assert s.diagnostics() == {"stop_below_minimum": 0, "stop_above_cap": 0, "data_gap": 2}


def test_stop_bounds_rounding_and_counters() -> None:
    class FixedAtr(TrendPullback):
        def _atr(self, bars: BarSeries, j: int) -> float | None:
            return 6.0

    s = FixedAtr(
        TrendPullbackParams(), symbol="XAUUSD", point=0.01, decision_timeframe=Timeframe.M15,
        entries_allowed=lambda t: True,
    )  # fmt: skip
    m15 = series(Timeframe.M15, m15_closes(), T0)
    ref = 2009.0
    # min low = stop + 3.00 + 0.005 (mid-cell): the stop rounds down to exactly ref - d points.
    for d, expected in ((499, None), (500, 500.0), (2000, 2000.0), (2001, None)):
        low = ref - d * 0.01 + 3.0 + 0.005
        sig = s._signal(T0, Side.BUY, ref, np.array([low, 2010.0]), m15, len(m15) - 1, ("x",))
        got = None if sig is None else sig.features["stop_points"]
        assert got == expected, f"d={d}"
    assert dict(s.diagnostics()) == {"stop_below_minimum": 1, "stop_above_cap": 1, "data_gap": 0}
    # Short: the stop rounds up, the target up (toward the entry).
    sig = s._signal(T0, Side.SELL, 1991.0, np.array([1996.004, 1990.0]), m15, len(m15) - 1, ("x",))
    assert sig is not None
    assert (sig.stop_loss, sig.take_profit) == (1999.01, 1978.99)  # 1991 - 1.5 x 8.01 = 1978.985


def m5_store(m5_closes: dict[str, float], m15_after: list[float] | None = None) -> BarStore:
    """M5 bars: flat history, the 14:15 bar at 2009 (high 2009.50), then ``m5_closes`` keyed
    "HH:MM" (high = close + 0.50, low = close - 1.00)."""
    times = sorted(m5_closes)
    closes = [2000.0] * FLAT + [2009.0] + [m5_closes[t] for t in times]
    last = T0.replace(hour=int(times[-1][:2]), minute=int(times[-1][3:]))
    n = len(closes)
    m5 = series(Timeframe.M5, closes, last,
                highs={i: closes[i] + 0.5 for i in range(n - len(times) - 1, n)},
                lows={i: closes[i] - 1.0 for i in range(n - len(times) - 1, n)})  # fmt: skip
    m15 = series(
        Timeframe.M15, m15_closes(m15_after), T0 + timedelta(minutes=15 * len(m15_after or []))
    )
    return BarStore({Timeframe.H1: h1(), Timeframe.M15: m15, Timeframe.M5: m5})


def m5_times(*hhmm: str) -> list[datetime]:
    return [T0.replace(hour=int(t[:2]), minute=int(t[3:])) for t in hhmm]


def test_m5_window_expiry_replacement_one_use_and_restart() -> None:
    later = [2002.0, 2002.0]  # M15 closes 14:30 and 14:45: below EMA20, so not setups
    # 14:20 no trigger (2006 < previous high 2009.50); 14:25 triggers; 14:30 would trigger again.
    store = m5_store({"14:20": 2006.0, "14:25": 2007.0, "14:30": 2008.0}, later)
    s = strategy("m5")
    t20, t25, t30 = m5_times("14:20", "14:25", "14:30")
    assert at(store, s, t20) is None
    sig = at(store, s, t25)
    assert sig is not None
    assert sig.reasons[0].startswith("M15 setup at 14:15Z")
    # Stop over b7..b0 and the M5 bars since 14:15 (lows 2005, 2006): min 1996.25 - 3.00.
    assert (sig.stop_loss, sig.take_profit) == (1993.25, 2027.62)  # 2007 + 1.5 x 13.75
    # One use: the 14:15 setup is spent, also for a fresh instance (a restart).
    assert at(store, s, t30) is None
    assert at(store, strategy("m5"), t30) is None

    closes = m15_closes()
    e50 = ema(closes, -1, 50)
    expired = m5_store({"14:20": e50, "14:25": 2007.0}, later)  # close == EMA50(b_s): expires
    assert at(expired, strategy("m5"), t20) is None  # expiry at T itself
    assert at(expired, strategy("m5"), t25) is None
    s = strategy("m5")
    assert at(expired, s, T0 + timedelta(minutes=45)) is None  # no M5 bar closes at T
    assert s.diagnostics()["data_gap"] == 1

    quiet = {"14:20": 2006.0, "14:25": 2005.5, "14:30": 2005.0, "14:35": 2004.5, "14:40": 2004.0}
    t45, t50 = m5_times("14:45", "14:50")
    assert at(m5_store({**quiet, "14:45": 2006.0}, later), strategy("m5"), t45) is not None, "T0+30"
    beyond = m5_store({**quiet, "14:45": 2004.0, "14:50": 2006.0}, later)
    assert at(beyond, strategy("m5"), t50) is None, "after T0+30 the window is closed"

    # 14:30 is both an M5 close and a new setup's close: the 14:15 setup is evaluated at 14:30;
    # the new one can trigger from 14:35 on.
    rising = m5_store(
        {"14:20": 2006.0, "14:25": 2005.0, "14:30": 2007.0, "14:35": 2008.0}, [2010.0]
    )
    s = strategy("m5")
    t35 = m5_times("14:35")[0]
    first, second = at(rising, s, t30), at(rising, s, t35)
    assert first is not None
    assert first.reasons[0].startswith("M15 setup at 14:15Z")
    assert second is not None
    assert second.reasons[0].startswith("M15 setup at 14:30Z")


def test_invalidation_only_on_completed_h1_closes_after_the_decision() -> None:
    hourly_closes = [2000.0] * FLAT + [2001.0, 2002.0, 2003.0, 2004.0, 2005.0]
    at_ema = ema([*hourly_closes, 0.0], -2, 50)  # close(15:00) == EMA_H1(15:00): not invalid
    sig = at(BarStore({Timeframe.H1: h1(), Timeframe.M15: series(Timeframe.M15, m15_closes(), T0)}),
             strategy(), T0)  # fmt: skip
    assert sig is not None
    pos = PositionState(
        position_id=1, symbol="XAUUSD", side=Side.BUY, volume=0.01, open_price=2009.2,
        open_time=T0, stop_loss=sig.stop_loss,
    )  # fmt: skip
    s = strategy()
    for extra, now, invalid in (
        ([1990.0], T0 + timedelta(minutes=30), False),  # the 15:00 bar has not closed yet
        ([1990.0], T0 + timedelta(minutes=45), True),  # it has, below EMA_H1
        ([at_ema], T0 + timedelta(minutes=45), False),  # equal: strict
    ):
        view = BarStore({Timeframe.H1: h1(extra)}).view_at(now)
        assert (s.invalidation(view, pos, sig) is not None) is invalid, (extra, now)
