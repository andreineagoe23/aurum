"""HistoricalMarket: data mode per day, next/last quote, price-path segments."""

from datetime import date

import pytest

from aurum.backtest.market import BarSegment, DataMode, HistoricalMarket, TickSegment
from aurum.core.timeframes import Timeframe
from aurum.features.view import BarSeries, to_ms
from tests.simdata import at, bars_frame, tick_day

D1, D2 = date(2026, 9, 14), date(2026, 9, 15)  # D1 bar-only, D2 has exact ticks
D1_BARS = [
    ("19:58", 2000.0, 2000.5, 1999.5, 2000.2, 8),
    ("19:59", 2000.2, 2000.4, 2000.0, 2000.3, 6),
]
D2_TICKS = [
    ("08:00:00.500", 2001.0, 2001.1),
    ("08:00:00.500", 2001.2, 2001.3),
    ("08:01:10", 2002.0, 2002.2),
]


def mixed() -> HistoricalMarket:
    m1 = bars_frame(Timeframe.M1, D1_BARS, D1)
    tapes = {D2: tick_day(D2_TICKS, D2)}
    return HistoricalMarket(
        symbol="XAUUSD",
        point=0.01,
        bars={Timeframe.M1: BarSeries.from_frame(Timeframe.M1, m1)},
        tick_days=frozenset(tapes),
        tick_loader=tapes.__getitem__,
        bar_spread_extra_points=2,
    )


def test_mode_per_day() -> None:
    mkt = mixed()
    assert mkt.mode(D1) is DataMode.BARS
    assert mkt.mode(D2) is DataMode.TICKS


def test_next_quote_crosses_days_and_respects_wait() -> None:
    mkt = mixed()
    q = mkt.next_quote(to_ms(at("20:00", D1)), to_ms(at("09:00", D2)))
    assert q is not None
    assert (q.mode, q.time, q.bid, q.index) == (DataMode.TICKS, at("08:00:00.500", D2), 2001.0, 0)
    assert mkt.next_quote(to_ms(at("20:00", D1)), to_ms(at("07:00", D2))) is None


def test_next_quote_on_bars_is_the_bar_open_with_spread() -> None:
    q = mixed().next_quote(to_ms(at("19:58", D1)), to_ms(at("20:00", D1)))
    assert q is not None
    assert q.bid == 2000.0
    assert q.ask == pytest.approx(2000.10)  # (8 + 2) points


def test_last_quote_falls_back_to_previous_bar_day() -> None:
    mkt = mixed()
    q = mkt.last_quote(to_ms(at("07:00", D2)))  # no tick yet on D2
    assert q is not None
    assert q.mode is DataMode.BARS
    assert q.time == at("19:59:59.999", D1)
    assert q.bid == 2000.3
    q2 = mkt.last_quote(to_ms(at("08:00:00.500", D2)))
    assert q2 is not None
    assert (q2.bid, q2.index) == (2001.2, 1)  # the later of two same-millisecond ticks


def test_segments_split_by_day_and_resume_at_index() -> None:
    mkt = mixed()
    segs = list(mkt.segments(to_ms(at("19:58:30", D1)), to_ms(at("09:00", D2))))
    assert [type(s) for s in segs] == [BarSegment, TickSegment]
    assert isinstance(segs[0], BarSegment)
    assert len(segs[0].open) == 2  # the 19:58 bar contains the start, so it is included
    resumed = list(mkt.segments(to_ms(at("08:00:00.500", D2)), to_ms(at("09:00", D2)), 1))
    assert isinstance(resumed[0], TickSegment)
    assert resumed[0].start == 1
    assert list(resumed[0].bid) == [2001.2, 2002.0]


def test_ticks_require_a_loader() -> None:
    with pytest.raises(ValueError, match="tick_loader"):
        HistoricalMarket(
            symbol="XAUUSD",
            point=0.01,
            bars={
                Timeframe.M1: BarSeries.from_frame(
                    Timeframe.M1, bars_frame(Timeframe.M1, D1_BARS, D1)
                )
            },
            tick_days=frozenset({D2}),
        )
    with pytest.raises(KeyError):
        mixed().ticks(D1)
