"""MT5Broker against the fake MetaTrader5 module (runs on any OS)."""

import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from aurum.adapters.mt5 import MT5Broker, is_available, load_mt5
from aurum.adapters.mt5.servertime import OffsetCheck, ServerTimeZone
from aurum.core.clock import ManualClock
from aurum.core.enums import AccountTradeMode, DealEntry, ExecutionStatus, Mode, OrderType, Side
from aurum.core.errors import (
    BrokerDisconnectedError,
    BrokerError,
    LiveTradingBlockedError,
    MT5UnavailableError,
    SafetyViolationError,
)
from aurum.core.models import OrderIntent
from aurum.core.safety import LIVE_ACK_PHRASE
from aurum.core.timeframes import Timeframe
from aurum.data.schemas import RAW_BARS, RAW_TICKS
from aurum.settings import AppConfig, EnvSettings, PromotionConfig, Settings
from tests import fake_mt5 as f
from tests.factories import intent
from tests.fake_mt5 import FakeMT5

NOW = datetime(2026, 10, 9, 0, 35, tzinfo=UTC)  # Friday; server clock 03:35 (UTC+3)


def make_settings(
    app_config: AppConfig,
    *,
    mode: Mode = Mode.DEMO,
    live_ack: str | None = None,
    promotion: PromotionConfig | None = None,
    **broker_changes: object,
) -> Settings:
    env = EnvSettings(
        _env_file=None,
        aurum_mode=mode,
        aurum_live_ack=live_ack,
        mt5_login=504366,
        mt5_password=SecretStr("s3cret-pw"),
        mt5_server="FusionMarkets-Demo",
    )
    broker_cfg = app_config.broker.model_copy(update=broker_changes)
    config = app_config.model_copy(
        update={"promotion": promotion or app_config.promotion, "broker": broker_cfg}
    )
    return Settings(
        env=env,
        config=config,
        project_root=Path("."),
        config_dir=Path("configs"),
        data_dir=Path("data"),
    )


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(NOW)


@pytest.fixture
def mt5(clock: ManualClock) -> FakeMT5:
    return FakeMT5(now=clock.now)


@pytest.fixture
def settings(app_config: AppConfig) -> Settings:
    return make_settings(app_config)


def make_broker(
    settings: Settings, mt5: FakeMT5, clock: ManualClock, sleeps: list[float] | None = None
) -> MT5Broker:
    record = sleeps if sleeps is not None else []
    return MT5Broker(settings, mt5_module=mt5, clock=clock, sleep=record.append)


@pytest.fixture
def broker(settings: Settings, mt5: FakeMT5, clock: ManualClock) -> MT5Broker:
    b = make_broker(settings, mt5, clock)
    b.connect()
    return b


def gold_intent(side: Side = Side.BUY, volume: float = 0.01, **kw: object) -> OrderIntent:
    """An intent near the fake's 4142 quote, decided at NOW."""
    sl, tp = (4127.0, 4157.0) if side is Side.BUY else (4157.0, 4127.0)
    return intent(side, volume, t=NOW, stop_loss=sl, take_profit=tp, **kw)


# --------------------------------------------------------------------------- loader


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_package_imports_on_macos_without_metatrader5() -> None:
    assert "MetaTrader5" not in sys.modules


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_mt5_unavailable_off_windows() -> None:
    assert is_available() is False
    with pytest.raises(MT5UnavailableError, match="Windows-only"):
        load_mt5()


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_broker_without_injected_module_fails_cleanly(app_config: AppConfig) -> None:
    b = MT5Broker(make_settings(app_config))
    with pytest.raises(MT5UnavailableError):
        b.account_info()


# --------------------------------------------------------------------------- connection


def test_connect_passes_credentials_but_never_logs_password(
    settings: Settings, mt5: FakeMT5, clock: ManualClock, capsys: pytest.CaptureFixture[str]
) -> None:
    b = make_broker(settings, mt5, clock)
    b.connect()
    assert b.is_connected()
    kwargs = mt5.initialize_calls[0]
    assert kwargs["password"] == "s3cret-pw"
    assert kwargs["login"] == 504366
    captured = capsys.readouterr()
    assert "s3cret-pw" not in captured.out + captured.err
    b.disconnect()
    assert not b.is_connected()


def test_connect_retries_with_exponential_backoff(
    settings: Settings, mt5: FakeMT5, clock: ManualClock
) -> None:
    mt5.initialize_results = [False, False, True]
    sleeps: list[float] = []
    make_broker(settings, mt5, clock, sleeps).connect()
    assert len(mt5.initialize_calls) == 3
    assert sleeps == [1.0, 2.0]


def test_connect_gives_up_after_attempts(
    settings: Settings, mt5: FakeMT5, clock: ManualClock
) -> None:
    mt5.initialize_results = [False, False, False]
    with pytest.raises(BrokerError, match=r"after 3 attempts.*IPC timeout"):
        make_broker(settings, mt5, clock).connect()


def test_configured_account_currency_is_usd(app_config: AppConfig) -> None:
    assert app_config.broker.account_currency == "USD"


def test_account_currency_matching_expectation_connects(broker: MT5Broker) -> None:
    assert broker.account_info().currency == "USD"


def test_account_currency_auto_accepts_any(
    app_config: AppConfig, mt5: FakeMT5, clock: ManualClock
) -> None:
    mt5.currency = "GBP"
    b = make_broker(make_settings(app_config, account_currency="auto"), mt5, clock)
    b.connect()
    assert b.account_info().currency == "GBP"


def test_account_currency_expectation_enforced(
    app_config: AppConfig, mt5: FakeMT5, clock: ManualClock
) -> None:
    mt5.currency = "GBP"
    b = make_broker(make_settings(app_config, account_currency="USD"), mt5, clock)
    with pytest.raises(BrokerError, match=r"GBP but configs/broker.yaml expects USD"):
        b.connect()
    assert not b.is_connected()


def test_connect_verifies_server_clock(broker: MT5Broker) -> None:
    check = broker.server_time_check
    assert check is not None
    assert check.status is OffsetCheck.VERIFIED
    assert check.expected == timedelta(hours=3)


def test_connect_refuses_server_clock_ahead_of_rule(settings: Settings, clock: ManualClock) -> None:
    mt5 = FakeMT5(now=clock.now, server_tz=ServerTimeZone(shift_hours=8))  # server really +4h
    with pytest.raises(BrokerError, match="server_time"):
        make_broker(settings, mt5, clock).connect()


def test_stale_quote_cannot_verify_but_connects(
    settings: Settings, mt5: FakeMT5, clock: ManualClock
) -> None:
    mt5.quote_age = timedelta(days=2)  # weekend
    b = make_broker(settings, mt5, clock)
    b.connect()
    assert b.server_time_check is not None
    assert b.server_time_check.status is OffsetCheck.STALE_QUOTE


def test_account_info_none_means_disconnected(
    settings: Settings, mt5: FakeMT5, clock: ManualClock
) -> None:
    with pytest.raises(BrokerDisconnectedError):
        make_broker(settings, mt5, clock).account_info()


def test_data_call_after_terminal_disconnect_raises(broker: MT5Broker, mt5: FakeMT5) -> None:
    mt5.connected = False
    assert not broker.is_connected()
    mt5.positions_get = lambda *a, **k: None  # type: ignore[method-assign]
    with pytest.raises(BrokerDisconnectedError):
        broker.positions()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f.ACCOUNT_TRADE_MODE_DEMO, AccountTradeMode.DEMO),
        (f.ACCOUNT_TRADE_MODE_CONTEST, AccountTradeMode.CONTEST),
        (f.ACCOUNT_TRADE_MODE_REAL, AccountTradeMode.REAL),
        (99, AccountTradeMode.REAL),
    ],
)
def test_trade_mode_mapping_fails_safe(
    broker: MT5Broker, mt5: FakeMT5, raw: int, expected: AccountTradeMode
) -> None:
    mt5.trade_mode = raw
    assert broker.account_info().trade_mode is expected


# --------------------------------------------------------------------------- instrument + data


def test_symbol_discovery_and_spec(broker: MT5Broker) -> None:
    assert broker.find_symbols("gold") == ["XAUUSD"]
    assert broker.resolve_symbol() == "XAUUSD"
    spec = broker.symbol_info("XAUUSD")
    assert (spec.digits, spec.point, spec.volume_min, spec.contract_size) == (2, 0.01, 0.01, 100)
    assert spec.captured_at == NOW


def test_unknown_symbol_candidates(app_config: AppConfig, mt5: FakeMT5, clock: ManualClock) -> None:
    b = make_broker(make_settings(app_config, symbol_candidates=["GOLDX"]), mt5, clock)
    with pytest.raises(BrokerError, match="aurum mt5 symbols"):
        b.connect()


def test_latest_tick_is_utc(broker: MT5Broker, mt5: FakeMT5) -> None:
    t = broker.latest_tick("XAUUSD")
    assert t.time == NOW  # server 03:35 -> 00:35 UTC
    assert (t.bid, t.ask) == (mt5.bid, mt5.ask)


def _rate(mt5: FakeMT5, open_utc: datetime, price: float = 4140.0) -> dict[str, float]:
    return {
        "time": mt5.server_s(open_utc),
        "open": price,
        "high": price + 2,
        "low": price - 2,
        "close": price + 1,
        "tick_volume": 100,
        "spread": 3,
        "real_volume": 0,
    }


def test_bars_converted_to_utc_and_completed_only(broker: MT5Broker, mt5: FakeMT5) -> None:
    opens = [datetime(2026, 10, 8, 23, 0, tzinfo=UTC), datetime(2026, 10, 9, 0, 0, tzinfo=UTC)]
    mt5.rates[f.TIMEFRAME_H1] = [_rate(mt5, t) for t in opens]  # 00:00 bar still forming
    bars = broker.bars("XAUUSD", Timeframe.H1, datetime(2026, 10, 8, tzinfo=UTC), NOW)
    assert [b.open_time for b in bars] == [opens[0]]
    assert bars[0].close_time <= NOW
    assert bars[0].spread_points == 3


def test_bars_query_bounds_are_sent_in_server_time(broker: MT5Broker, mt5: FakeMT5) -> None:
    seen: list[tuple[int, int]] = []
    mt5.copy_rates_range = lambda s, tf, a, b: seen.append((a, b)) or []  # type: ignore[method-assign]
    start = datetime(2026, 10, 8, 12, tzinfo=UTC)
    broker.bars("XAUUSD", Timeframe.M5, start, NOW)
    assert seen == [(mt5.server_s(start), mt5.server_s(NOW))]
    assert seen[0][0] - int(start.timestamp()) == 3 * 3600


def test_winter_bars_use_plus_two(broker: MT5Broker, mt5: FakeMT5, clock: ManualClock) -> None:
    clock.set(datetime(2026, 12, 2, 12, tzinfo=UTC))
    t = datetime(2026, 12, 2, 10, tzinfo=UTC)
    mt5.rates[f.TIMEFRAME_H1] = [_rate(mt5, t)]
    assert mt5.rates[f.TIMEFRAME_H1][0]["time"] - int(t.timestamp()) == 2 * 3600
    bars = broker.bars("XAUUSD", Timeframe.H1, t, t + timedelta(hours=1))
    assert [b.open_time for b in bars] == [t]


def test_ticks_converted_filtered_and_invalid_skipped(broker: MT5Broker, mt5: FakeMT5) -> None:
    t0 = NOW - timedelta(seconds=10)
    ms = int(mt5.server_tz.to_server_seconds(t0) * 1000)
    row = {"bid": 4142.0, "ask": 4142.1, "last": 0.0, "volume": 0, "volume_real": 0.0}
    mt5.tick_rows = [
        {**row, "time_msc": ms + 123},
        {**row, "time_msc": ms + 500, "bid": 0.0},  # no bid: not a Tick
        {**row, "time_msc": ms + 20_000},  # after NOW
    ]
    ticks = broker.ticks("XAUUSD", t0, NOW + timedelta(minutes=1))
    assert [t.time for t in ticks] == [t0 + timedelta(milliseconds=123)]


def test_history_deals_skip_balance_entries(broker: MT5Broker, mt5: FakeMT5) -> None:
    broker.send_order(gold_intent())
    mt5.deals.append(
        type(mt5.deals[0])(**{**vars(mt5.deals[0]), "ticket": 1, "type": f.DEAL_TYPE_BALANCE})
    )
    deals = broker.history_deals(NOW - timedelta(hours=1), NOW + timedelta(seconds=1))
    assert len(deals) == 1
    assert deals[0].entry is DealEntry.IN
    assert deals[0].time == NOW


# --------------------------------------------------------------------------- the guard

WRITES: list[tuple[str, Callable[[MT5Broker], object]]] = [
    ("send_order", lambda b: b.send_order(gold_intent())),
    ("modify_position", lambda b: b.modify_position(1, 1990.0, None)),
    ("close_position", lambda b: b.close_position(1)),
    ("cancel_order", lambda b: b.cancel_order(1)),
]


@pytest.mark.parametrize(("name", "call"), WRITES)
@pytest.mark.parametrize("raw_mode", [f.ACCOUNT_TRADE_MODE_REAL, f.ACCOUNT_TRADE_MODE_CONTEST, 99])
def test_every_write_blocked_on_non_demo_account(
    broker: MT5Broker, mt5: FakeMT5, name: str, call: Callable[[MT5Broker], object], raw_mode: int
) -> None:
    mt5.trade_mode = raw_mode
    with pytest.raises(LiveTradingBlockedError):
        call(broker)
    assert mt5.sent == []


@pytest.mark.parametrize(("name", "call"), WRITES)
@pytest.mark.parametrize("mode", [Mode.BACKTEST, Mode.SHADOW])
def test_every_write_blocked_in_non_trading_modes_even_on_demo(
    app_config: AppConfig,
    mt5: FakeMT5,
    clock: ManualClock,
    name: str,
    call: Callable[[MT5Broker], object],
    mode: Mode,
) -> None:
    b = make_broker(make_settings(app_config, mode=mode), mt5, clock)
    b.connect()
    with pytest.raises(LiveTradingBlockedError, match="never sends orders"):
        call(b)
    assert mt5.sent == []


def test_guard_rereads_account_on_every_write(broker: MT5Broker, mt5: FakeMT5) -> None:
    """A demo account swapped for a real one between calls must be caught (no caching)."""
    first = broker.send_order(gold_intent())
    assert first.status is ExecutionStatus.FILLED
    mt5.trade_mode = f.ACCOUNT_TRADE_MODE_REAL
    with pytest.raises(LiveTradingBlockedError):
        broker.close_position(first.position_id or 0)
    assert len(mt5.sent) == 1


def test_real_account_with_full_live_authorisation_passes_guard(
    app_config: AppConfig, mt5: FakeMT5, clock: ManualClock
) -> None:
    promotion = PromotionConfig(
        live_enabled=True, signed_off_date=date(2026, 10, 1), signed_off_by="Andrei"
    )
    settings = make_settings(
        app_config, mode=Mode.LIVE, live_ack=LIVE_ACK_PHRASE, promotion=promotion
    )
    mt5.trade_mode = f.ACCOUNT_TRADE_MODE_REAL
    b = make_broker(settings, mt5, clock)
    b.connect()
    assert b.send_order(gold_intent()).status is ExecutionStatus.FILLED


def test_real_account_live_mode_but_repo_promotion_config_blocks(
    app_config: AppConfig, mt5: FakeMT5, clock: ManualClock
) -> None:
    settings = make_settings(app_config, mode=Mode.LIVE, live_ack=LIVE_ACK_PHRASE)
    assert app_config.promotion.live_enabled is False  # the committed config
    mt5.trade_mode = f.ACCOUNT_TRADE_MODE_REAL
    b = make_broker(settings, mt5, clock)
    b.connect()
    with pytest.raises(LiveTradingBlockedError, match="live_enabled is false"):
        b.send_order(gold_intent())


def test_unapproved_intent_refused_before_touching_terminal(
    broker: MT5Broker, mt5: FakeMT5
) -> None:
    good = gold_intent()
    bypassed = OrderIntent.model_construct(
        **{
            **dict(good),
            "risk_decision": good.risk_decision.model_construct(
                **{**dict(good.risk_decision), "approved": False}
            ),
        }
    )
    with pytest.raises(SafetyViolationError, match="approved RiskDecision"):
        broker.send_order(bypassed)
    assert mt5.sent == []


# --------------------------------------------------------------------------- trading


def test_market_order_round_trip(broker: MT5Broker, mt5: FakeMT5, app_config: AppConfig) -> None:
    i = gold_intent()
    report = broker.send_order(i)
    assert report.status is ExecutionStatus.FILLED
    assert report.fill_price == mt5.ask  # buys fill at ask
    assert report.commission < 0
    req = mt5.sent[0]
    assert req["comment"] == i.idempotency_key
    assert req["magic"] == app_config.broker.magic_number
    assert req["sl"] == 4127.0
    assert req["type_filling"] == f.ORDER_FILLING_IOC
    (pos,) = broker.positions()
    assert pos.idempotency_key == i.idempotency_key
    assert pos.stop_loss == 4127.0
    assert pos.open_time == NOW

    assert (
        broker.modify_position(pos.position_id, 4120.0, 4170.0).status is ExecutionStatus.MODIFIED
    )
    assert broker.positions()[0].stop_loss == 4120.0

    closed = broker.close_position(pos.position_id)
    assert closed.status is ExecutionStatus.CLOSED
    assert closed.fill_price == mt5.bid  # a long closes at the bid
    assert broker.positions() == []
    deals = broker.position_deals(pos.position_id)
    assert [d.entry for d in deals] == [DealEntry.IN, DealEntry.OUT]
    assert all(d.idempotency_key == i.idempotency_key for d in deals)


def test_same_intent_twice_sends_once(broker: MT5Broker, mt5: FakeMT5) -> None:
    i = gold_intent()
    first = broker.send_order(i)
    again = broker.send_order(i)
    assert again.duplicate
    assert again.position_id == first.position_id
    assert len(mt5.sent) == 1


def test_same_intent_after_close_is_still_a_duplicate(broker: MT5Broker, mt5: FakeMT5) -> None:
    i = gold_intent()
    pos_id = broker.send_order(i).position_id or 0
    broker.close_position(pos_id)
    assert broker.send_order(i).duplicate
    assert len(mt5.sent) == 2  # open + close, no second open


def test_closing_order_of_other_key_does_not_block_new_intent(broker: MT5Broker) -> None:
    a = gold_intent()
    broker.close_position(broker.send_order(a).position_id or 0)
    b = intent(Side.BUY, 0.01, t=NOW + timedelta(minutes=5), stop_loss=4127.0)
    assert not broker.send_order(b).duplicate


def test_lost_reply_is_reconciled_not_resent(broker: MT5Broker, mt5: FakeMT5) -> None:
    mt5.lose_next_reply = True
    i = gold_intent()
    report = broker.send_order(i)
    assert report.status is ExecutionStatus.FILLED
    assert not report.duplicate
    assert broker.send_order(i).duplicate
    assert len(mt5.sent) == 1
    assert len(broker.positions()) == 1


def test_broker_rejection_reported(broker: MT5Broker, mt5: FakeMT5) -> None:
    mt5.reject_next = f.TRADE_RETCODE_REQUOTE
    report = broker.send_order(gold_intent())
    assert report.status is ExecutionStatus.REJECTED
    assert report.retcode == f.TRADE_RETCODE_REQUOTE
    assert broker.positions() == []


def test_invalid_volume_rejected_without_sending(broker: MT5Broker, mt5: FakeMT5) -> None:
    report = broker.send_order(gold_intent(volume=0.015))
    assert report.status is ExecutionStatus.REJECTED
    assert mt5.sent == []


def test_fill_without_broker_stop_is_closed_immediately(broker: MT5Broker, mt5: FakeMT5) -> None:
    mt5.drop_stops = True
    with pytest.raises(SafetyViolationError, match="no broker-side stop"):
        broker.send_order(gold_intent())
    assert broker.positions() == []


def test_sell_fills_at_bid(broker: MT5Broker, mt5: FakeMT5) -> None:
    report = broker.send_order(gold_intent(Side.SELL))
    assert report.fill_price == mt5.bid


def test_pending_order_place_cancel_and_dedupe(broker: MT5Broker, mt5: FakeMT5) -> None:
    i = intent(
        Side.BUY,
        0.01,
        t=NOW,
        entry_type=OrderType.LIMIT,
        entry_price=4130.0,
        stop_loss=4120.0,
    )
    placed = broker.send_order(i)
    assert placed.status is ExecutionStatus.PLACED
    (order,) = broker.orders()
    assert (order.order_type, order.price, order.idempotency_key) == (
        OrderType.LIMIT,
        4130.0,
        i.idempotency_key,
    )
    assert broker.send_order(i).duplicate  # still pending
    assert broker.cancel_order(order.order_id).status is ExecutionStatus.CANCELLED
    assert broker.orders() == []
    assert broker.send_order(i).duplicate  # cancelled orders stay acted upon
    assert len(mt5.sent) == 2


def test_check_order(broker: MT5Broker) -> None:
    result = broker.check_order(gold_intent())
    assert result.ok
    assert result.margin_required == 6.26
    assert not broker.check_order(gold_intent(volume=0.015)).ok


def test_symbol_snapshot_written(broker: MT5Broker, tmp_path: Path) -> None:
    from aurum.adapters.mt5.snapshot import write_symbol_snapshot

    spec = broker.symbol_info("XAUUSD")
    path = write_symbol_snapshot(tmp_path, spec, broker.symbol_snapshot("XAUUSD"), server="S")
    assert path.parent == tmp_path / "specs" / "XAUUSD"
    assert '"filling_mode": 3' in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_symbol_snapshot(tmp_path, spec, {}, server="S")


# --------------------------------------------------------------------------- raw frames


def test_tick_frame_keeps_every_row_in_utc(broker: MT5Broker, mt5: FakeMT5) -> None:
    t0 = NOW - timedelta(seconds=10)
    ms = int(mt5.server_tz.to_server_seconds(t0) * 1000)
    mt5.tick_rows = [
        {"time_msc": ms + 5, "bid": 4142.0, "ask": 4142.1, "flags": 6},
        {"time_msc": ms + 5, "bid": 0.0, "ask": 4142.1, "flags": 4},  # invalid, still kept
        {"time_msc": ms + 2, "bid": 4141.9, "ask": 4142.0, "flags": 2},  # out of order, kept
        {"time_msc": ms - 1, "bid": 4141.0, "ask": 4141.1},  # before start: dropped
    ]
    frame = broker.tick_frame("XAUUSD", t0, NOW)
    assert frame.columns == RAW_TICKS.names
    assert frame["time"].to_list() == [
        t0 + timedelta(milliseconds=5),
        t0 + timedelta(milliseconds=5),
        t0 + timedelta(milliseconds=2),
    ]
    assert frame["bid"].to_list() == [4142.0, 0.0, 4141.9]
    assert frame["flags"].to_list() == [6, 4, 2]


def test_tick_frame_end_is_exclusive_and_ms_precise(broker: MT5Broker, mt5: FakeMT5) -> None:
    t0 = NOW - timedelta(seconds=10)
    ms = int(mt5.server_tz.to_server_seconds(t0) * 1000)
    mt5.tick_rows = [{"time_msc": ms + i, "bid": 4142.0, "ask": 4142.1} for i in (0, 499, 500)]
    frame = broker.tick_frame("XAUUSD", t0, t0 + timedelta(milliseconds=500))
    assert frame.height == 2


def test_tick_frame_ipc_failure_raises(broker: MT5Broker, mt5: FakeMT5) -> None:
    mt5.fail_next_copies = 1
    with pytest.raises(BrokerError, match="IPC send failed"):
        broker.tick_frame("XAUUSD", NOW - timedelta(minutes=1), NOW)


def test_rate_frame_completed_bars_only_in_utc(broker: MT5Broker, mt5: FakeMT5) -> None:
    opens = [datetime(2026, 10, 8, 23, 0, tzinfo=UTC), datetime(2026, 10, 9, 0, 0, tzinfo=UTC)]
    mt5.rates[f.TIMEFRAME_H1] = [_rate(mt5, t) for t in opens]  # 00:00 bar still forming
    frame = broker.rate_frame("XAUUSD", Timeframe.H1, datetime(2026, 10, 8, tzinfo=UTC), NOW)
    assert frame.columns == RAW_BARS.names
    assert frame["open_time"].to_list() == [opens[0]]
    assert frame["spread"].to_list() == [3]
