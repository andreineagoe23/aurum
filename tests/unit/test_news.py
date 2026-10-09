"""News blackout windows in New York time, across DST, weekends and FOMC dates."""

from datetime import UTC, date, datetime

from aurum.sessions.news import NewsBlackout
from aurum.settings import AppConfig


def utc(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


def test_daily_windows_follow_us_daylight_time(app_config: AppConfig) -> None:
    news = NewsBlackout(app_config.risk.news_blackout)
    # Summer (EDT, UTC-4): 08:30 ET = 12:30 UTC. Winter (EST, UTC-5): 13:30 UTC.
    assert news.reason_at(utc(2026, 9, 15, 12, 30)) is not None
    assert news.reason_at(utc(2026, 12, 15, 12, 30)) is None
    assert news.reason_at(utc(2026, 12, 15, 13, 30)) is not None
    reason = news.reason_at(utc(2026, 9, 15, 14, 0))  # 10:00 ET
    assert reason is not None
    assert "09:50-10:15 America/New_York" in reason


def test_window_bounds_are_half_open(app_config: AppConfig) -> None:
    news = NewsBlackout(app_config.risk.news_blackout)
    assert news.reason_at(utc(2026, 9, 15, 12, 19)) is None  # 08:19 ET
    assert news.reason_at(utc(2026, 9, 15, 12, 20)) is not None  # 08:20
    assert news.reason_at(utc(2026, 9, 15, 12, 45)) is None  # 08:45


def test_weekends_are_never_blocked(app_config: AppConfig) -> None:
    news = NewsBlackout(app_config.risk.news_blackout)
    assert news.reason_at(utc(2026, 9, 13, 12, 30)) is None  # Sunday


def test_fomc_window_only_on_listed_dates(app_config: AppConfig) -> None:
    news = NewsBlackout(app_config.risk.news_blackout)
    fomc = news.reason_at(utc(2026, 9, 16, 18, 0))  # 14:00 ET on a statement date
    assert fomc is not None
    assert "FOMC statement, SEP" in fomc
    assert news.reason_at(utc(2026, 9, 16, 17, 29)) is None  # 13:29 ET
    assert news.reason_at(utc(2026, 9, 16, 19, 0)) is None  # 15:00 ET
    assert news.reason_at(utc(2026, 9, 15, 18, 0)) is None  # the day before
    assert news.last_fomc_date >= date(2026, 12, 9)
