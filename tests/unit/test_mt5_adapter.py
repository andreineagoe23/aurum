"""MT5Broker guard behaviour, using a mock MetaTrader5 module (runs on macOS)."""

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from aurum.adapters.mt5 import MT5Broker, is_available, load_mt5
from aurum.core.clock import ManualClock
from aurum.core.enums import AccountTradeMode, Mode
from aurum.core.errors import (
    BrokerDisconnectedError,
    BrokerError,
    LiveTradingBlockedError,
    MT5UnavailableError,
    SafetyViolationError,
)
from aurum.core.models import OrderIntent
from aurum.core.safety import LIVE_ACK_PHRASE
from aurum.settings import AppConfig, EnvSettings, PromotionConfig, Settings
from tests.factories import intent

DEMO, CONTEST, REAL = 0, 1, 2


def fake_mt5(trade_mode: int = DEMO) -> MagicMock:
    mt5 = MagicMock(name="MetaTrader5")
    mt5.ACCOUNT_TRADE_MODE_DEMO = DEMO
    mt5.ACCOUNT_TRADE_MODE_CONTEST = CONTEST
    mt5.ACCOUNT_TRADE_MODE_REAL = REAL
    mt5.initialize.return_value = True
    mt5.account_info.return_value = SimpleNamespace(
        login=12345,
        server="FusionMarkets-Demo",
        currency="USD",
        trade_mode=trade_mode,
        balance=10_000.0,
        equity=10_000.0,
        margin=0.0,
        margin_free=10_000.0,
        leverage=500,
    )
    return mt5


def make_settings(
    app_config: AppConfig,
    *,
    mode: Mode = Mode.DEMO,
    live_ack: str | None = None,
    promotion: PromotionConfig | None = None,
) -> Settings:
    env = EnvSettings(
        _env_file=None,
        aurum_mode=mode,
        aurum_live_ack=live_ack,
        mt5_login=12345,
        mt5_password=SecretStr("s3cret-pw"),
        mt5_server="FusionMarkets-Demo",
    )
    config = app_config.model_copy(update={"promotion": promotion or app_config.promotion})
    return Settings(
        env=env,
        config=config,
        project_root=Path("."),
        config_dir=Path("configs"),
        data_dir=Path("data"),
    )


def broker(settings: Settings, mt5: MagicMock) -> MT5Broker:
    return MT5Broker(
        settings, mt5_module=mt5, clock=ManualClock(datetime(2026, 10, 8, 9, tzinfo=UTC))
    )


WRITES = [
    ("send_order", lambda b: b.send_order(intent())),
    ("modify_position", lambda b: b.modify_position(1, 1990.0, None)),
    ("close_position", lambda b: b.close_position(1)),
    ("cancel_order", lambda b: b.cancel_order(1)),
]


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
    app_config: AppConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    mt5 = fake_mt5()
    b = broker(make_settings(app_config), mt5)
    b.connect()
    assert b.is_connected()
    kwargs = mt5.initialize.call_args.kwargs
    assert kwargs["password"] == "s3cret-pw"
    assert kwargs["login"] == 12345
    captured = capsys.readouterr()
    assert "s3cret-pw" not in captured.out + captured.err
    b.disconnect()
    mt5.shutdown.assert_called_once()
    assert not b.is_connected()


def test_connect_failure_raises(app_config: AppConfig) -> None:
    mt5 = fake_mt5()
    mt5.initialize.return_value = False
    mt5.last_error.return_value = (-6, "Authorization failed")
    with pytest.raises(BrokerError, match="Authorization failed"):
        broker(make_settings(app_config), mt5).connect()


def test_account_info_none_means_disconnected(app_config: AppConfig) -> None:
    mt5 = fake_mt5()
    mt5.account_info.return_value = None
    with pytest.raises(BrokerDisconnectedError):
        broker(make_settings(app_config), mt5).account_info()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (DEMO, AccountTradeMode.DEMO),
        (CONTEST, AccountTradeMode.CONTEST),
        (REAL, AccountTradeMode.REAL),
        (99, AccountTradeMode.REAL),
    ],
)
def test_trade_mode_mapping_fails_safe(
    app_config: AppConfig, raw: int, expected: AccountTradeMode
) -> None:
    assert broker(make_settings(app_config), fake_mt5(raw)).account_info().trade_mode is expected


# --------------------------------------------------------------------------- the guard


@pytest.mark.parametrize(("name", "call"), WRITES)
@pytest.mark.parametrize("raw_mode", [REAL, CONTEST, 99])
def test_every_write_blocked_on_non_demo_account(
    app_config: AppConfig, name: str, call: object, raw_mode: int
) -> None:
    mt5 = fake_mt5(raw_mode)
    b = broker(make_settings(app_config), mt5)
    with pytest.raises(LiveTradingBlockedError):
        call(b)  # type: ignore[operator]
    mt5.order_send.assert_not_called()


@pytest.mark.parametrize(("name", "call"), WRITES)
@pytest.mark.parametrize("mode", [Mode.BACKTEST, Mode.SHADOW])
def test_every_write_blocked_in_non_trading_modes_even_on_demo(
    app_config: AppConfig, name: str, call: object, mode: Mode
) -> None:
    mt5 = fake_mt5(DEMO)
    with pytest.raises(LiveTradingBlockedError, match="never sends orders"):
        call(broker(make_settings(app_config, mode=mode), mt5))  # type: ignore[operator]
    mt5.order_send.assert_not_called()


@pytest.mark.parametrize(("name", "call"), WRITES)
def test_demo_account_passes_guard(app_config: AppConfig, name: str, call: object) -> None:
    # Passing the guard reaches the Phase 1 placeholder.
    with pytest.raises(NotImplementedError, match="Phase 1"):
        call(broker(make_settings(app_config), fake_mt5(DEMO)))  # type: ignore[operator]


def test_guard_rereads_account_on_every_write(app_config: AppConfig) -> None:
    """A demo account swapped for a real one between calls must be caught (no caching)."""
    mt5 = fake_mt5(DEMO)
    b = broker(make_settings(app_config), mt5)
    with pytest.raises(NotImplementedError):
        b.send_order(intent())
    mt5.account_info.return_value = SimpleNamespace(
        **{**vars(mt5.account_info.return_value), "trade_mode": REAL}
    )
    with pytest.raises(LiveTradingBlockedError):
        b.send_order(intent())
    assert mt5.account_info.call_count == 2


def test_real_account_with_full_live_authorisation_passes_guard(app_config: AppConfig) -> None:
    promotion = PromotionConfig(
        live_enabled=True, signed_off_date=date(2026, 10, 1), signed_off_by="Andrei"
    )
    settings = make_settings(
        app_config, mode=Mode.LIVE, live_ack=LIVE_ACK_PHRASE, promotion=promotion
    )
    with pytest.raises(NotImplementedError):
        broker(settings, fake_mt5(REAL)).send_order(intent())


def test_real_account_live_mode_but_repo_promotion_config_blocks(app_config: AppConfig) -> None:
    settings = make_settings(app_config, mode=Mode.LIVE, live_ack=LIVE_ACK_PHRASE)
    assert app_config.promotion.live_enabled is False  # the committed config
    with pytest.raises(LiveTradingBlockedError, match="live_enabled is false"):
        broker(settings, fake_mt5(REAL)).send_order(intent())


def test_unapproved_intent_refused_before_touching_terminal(app_config: AppConfig) -> None:
    good = intent()
    bypassed = OrderIntent.model_construct(
        **{
            **dict(good),
            "risk_decision": good.risk_decision.model_construct(
                **{**dict(good.risk_decision), "approved": False}
            ),
        }
    )
    mt5 = fake_mt5(DEMO)
    with pytest.raises(SafetyViolationError, match="approved RiskDecision"):
        broker(make_settings(app_config), mt5).send_order(bypassed)
    mt5.account_info.assert_not_called()
    mt5.order_send.assert_not_called()
