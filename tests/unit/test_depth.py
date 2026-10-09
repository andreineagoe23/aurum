from datetime import UTC, date, datetime, timedelta

import pytest

from aurum.adapters.mt5 import MT5Broker
from aurum.core.clock import ManualClock
from aurum.data.depth import continuous_since, measure_depth
from aurum.data.market_hours import MarketHours
from aurum.settings import AppConfig
from tests import fake_mt5 as f
from tests.fake_mt5 import FakeMT5
from tests.unit.test_mt5_adapter import make_settings

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def market(app_config: AppConfig) -> MarketHours:
    return MarketHours(app_config.data.market)


def test_continuous_since_skips_sparse_start(market: MarketHours) -> None:
    dates = market.trading_dates(date(2026, 8, 3), date(2026, 10, 7))
    counts = {d: (2 if d < date(2026, 9, 1) else 1379) for d in dates}
    assert continuous_since(counts, market, date(2026, 10, 7)) == date(2026, 9, 1)
    assert continuous_since({}, market, date(2026, 10, 7)) is None


def test_measure_depth_against_fake_terminal(app_config: AppConfig, market: MarketHours) -> None:
    clock = ManualClock(NOW)
    mt5 = FakeMT5(now=clock.now)
    # H1 bars: one placeholder per day in August, full days from September.
    t = datetime(2026, 8, 3, tzinfo=UTC)
    rows = mt5.rates.setdefault(f.TIMEFRAME_H1, [])
    while t < NOW - timedelta(hours=1):
        if market.is_open(t) and (t.month >= 9 or t.hour == 12):
            rows.append(
                {"time": mt5.server_s(t), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                 "tick_volume": 1, "spread": 3, "real_volume": 0}
            )  # fmt: skip
        t += timedelta(hours=1)
    # Ticks only from 2026-09-21: one every 10 minutes.
    t = datetime(2026, 9, 21, tzinfo=UTC)
    while t < NOW:
        if market.is_open(t):
            mt5.tick_rows.append({"time_msc": mt5.server_s(t) * 1000, "bid": 1.0, "ask": 1.1})
        t += timedelta(minutes=10)
    broker = MT5Broker(make_settings(app_config), mt5_module=mt5, clock=clock)
    broker.connect()
    result = measure_depth(broker, "XAUUSD", market, NOW, refetch_days={date(2026, 9, 22): 99})
    h1 = next(b for b in result.bars if b.timeframe == "H1")
    assert h1.continuous_since == date(2026, 9, 1)
    assert h1.bars_per_year == {2026: len(rows)}
    assert result.first_tick == datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
    # 144 ten-minute slots minus the six inside the daily break (20:59-22:00 UTC)
    assert result.refetch == [{"day": date(2026, 9, 22), "stored_rows": 99, "server_rows_now": 138}]
