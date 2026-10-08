"""MT5Broker: BrokerPort over a locally running MetaTrader 5 terminal (Windows VM).

Phase 0 scope: connection, ``account_info`` and the live-trading guard. Every broker write
(send/modify/close/cancel) first re-reads ``account_info()`` from the terminal — never a cached
value — and runs ``aurum.core.safety.assert_order_permitted``. Real order/data calls arrive in
Phase 1 and must keep that ordering.

The MetaTrader5 module is loaded lazily, or injected (tests use a mock module).
"""

from collections.abc import Sequence
from datetime import datetime
from types import ModuleType
from typing import Any

import structlog

from aurum.adapters.mt5.loader import load_mt5
from aurum.core.clock import SystemClock
from aurum.core.enums import AccountTradeMode
from aurum.core.errors import BrokerDisconnectedError, BrokerError, SafetyViolationError
from aurum.core.models import (
    AccountSnapshot,
    Bar,
    DealRecord,
    ExecutionReport,
    OrderCheckResult,
    OrderIntent,
    OrderState,
    PositionState,
    SymbolSpec,
    Tick,
)
from aurum.core.safety import assert_order_permitted
from aurum.core.timeframes import Timeframe
from aurum.ports.clock import ClockPort
from aurum.settings import Settings

log = structlog.get_logger(__name__)

_PHASE_1 = "implemented in Phase 1 (MT5 connectivity spike)"


class MT5Broker:
    def __init__(
        self,
        settings: Settings,
        *,
        mt5_module: ModuleType | Any | None = None,
        clock: ClockPort | None = None,
    ) -> None:
        self._settings = settings
        self._mt5_module = mt5_module
        self._clock = clock or SystemClock()
        self._connected = False

    @property
    def _mt5(self) -> Any:
        if self._mt5_module is None:
            self._mt5_module = load_mt5()
        return self._mt5_module

    # ------------------------------------------------------------------ connection
    def connect(self) -> None:
        env = self._settings.env
        kwargs: dict[str, Any] = {}
        if env.mt5_path is not None:
            kwargs["path"] = str(env.mt5_path)
        if env.has_mt5_credentials:
            assert env.mt5_password is not None
            kwargs.update(
                login=env.mt5_login,
                password=env.mt5_password.get_secret_value(),
                server=env.mt5_server,
            )
        # Never log kwargs: it contains the password.
        log.info("mt5.connect", login=env.mt5_login, server=env.mt5_server)
        if not self._mt5.initialize(**kwargs):
            raise BrokerError(f"MT5 initialize failed: {self._mt5.last_error()}")
        self._connected = True

    def disconnect(self) -> None:
        if self._mt5_module is not None:
            self._mt5.shutdown()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------ account
    def _trade_mode(self, raw: int) -> AccountTradeMode:
        """Map MT5's integer trade mode. Anything unrecognised counts as REAL (fail safe)."""
        mt5 = self._mt5
        if raw == mt5.ACCOUNT_TRADE_MODE_DEMO:
            return AccountTradeMode.DEMO
        if raw == mt5.ACCOUNT_TRADE_MODE_CONTEST:
            return AccountTradeMode.CONTEST
        return AccountTradeMode.REAL

    def account_info(self) -> AccountSnapshot:
        info = self._mt5.account_info()
        if info is None:
            raise BrokerDisconnectedError(f"MT5 account_info failed: {self._mt5.last_error()}")
        return AccountSnapshot(
            login=info.login,
            server=info.server,
            currency=info.currency,
            trade_mode=self._trade_mode(info.trade_mode),
            balance=info.balance,
            equity=info.equity,
            margin=info.margin,
            margin_free=info.margin_free,
            leverage=info.leverage,
            time=self._clock.now(),
        )

    def _ensure_write_permitted(self) -> AccountSnapshot:
        """Fresh account check + live-trading guard. Called first by every broker write."""
        account = self.account_info()
        env, promotion = self._settings.env, self._settings.config.promotion
        try:
            assert_order_permitted(
                account_trade_mode=account.trade_mode,
                mode=env.aurum_mode,
                live_ack=env.aurum_live_ack,
                live_enabled=promotion.live_enabled,
                signed_off_date=promotion.signed_off_date,
                today=self._clock.now().date(),
            )
        except SafetyViolationError as exc:
            log.error("mt5.write_blocked", login=account.login, reason=str(exc))
            raise
        return account

    # ------------------------------------------------------------------ trading (guarded)
    def send_order(self, intent: OrderIntent) -> ExecutionReport:
        if not intent.risk_decision.approved:
            raise SafetyViolationError("refusing order without an approved RiskDecision")
        self._ensure_write_permitted()
        raise NotImplementedError(f"send_order {_PHASE_1}")

    def modify_position(
        self, position_id: int, stop_loss: float, take_profit: float | None
    ) -> ExecutionReport:
        self._ensure_write_permitted()
        raise NotImplementedError(f"modify_position {_PHASE_1}")

    def close_position(self, position_id: int, volume: float | None = None) -> ExecutionReport:
        self._ensure_write_permitted()
        raise NotImplementedError(f"close_position {_PHASE_1}")

    def cancel_order(self, order_id: int) -> ExecutionReport:
        self._ensure_write_permitted()
        raise NotImplementedError(f"cancel_order {_PHASE_1}")

    # ------------------------------------------------------------------ Phase 1
    def symbol_info(self, symbol: str) -> SymbolSpec:
        raise NotImplementedError(f"symbol_info {_PHASE_1}")

    def latest_tick(self, symbol: str) -> Tick:
        raise NotImplementedError(f"latest_tick {_PHASE_1}")

    def bars(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> Sequence[Bar]:
        raise NotImplementedError(f"bars {_PHASE_1}")

    def ticks(self, symbol: str, start: datetime, end: datetime) -> Sequence[Tick]:
        raise NotImplementedError(f"ticks {_PHASE_1}")

    def positions(self, symbol: str | None = None) -> Sequence[PositionState]:
        raise NotImplementedError(f"positions {_PHASE_1}")

    def orders(self, symbol: str | None = None) -> Sequence[OrderState]:
        raise NotImplementedError(f"orders {_PHASE_1}")

    def history_deals(self, start: datetime, end: datetime) -> Sequence[DealRecord]:
        raise NotImplementedError(f"history_deals {_PHASE_1}")

    def check_order(self, intent: OrderIntent) -> OrderCheckResult:
        raise NotImplementedError(f"check_order {_PHASE_1}")
