"""BrokerPort: everything the core may ask of a broker.

Implementations: ``FakeBroker`` (unit tests), ``SimBroker`` (Phase 3 backtest),
``ShadowBroker`` (Phase 7, live data / no orders), ``MT5Broker`` (Windows, demo orders).

Contract for all implementations:

* All datetimes in and out are timezone-aware UTC.
* ``bars`` returns **completed** bars only (``close_time <= now``), keyed by open time, with
  ``start <= open_time < end``. Never the forming bar.
* ``ticks`` returns ticks with ``start <= time < end`` and ``time <= now``.
* Raises ``BrokerDisconnectedError`` instead of returning stale or partial data when offline.
* ``send_order`` refuses intents whose embedded ``RiskDecision`` is not approved, and treats a
  repeated ``idempotency_key`` as a duplicate (``ExecutionReport.duplicate=True``, no new order).
* Real-broker adapters run the live-trading guard (``aurum.core.safety``) before every write.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

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
from aurum.core.timeframes import Timeframe


@runtime_checkable
class BrokerPort(Protocol):
    # connection
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...

    # account / instrument
    def account_info(self) -> AccountSnapshot: ...
    def symbol_info(self, symbol: str) -> SymbolSpec: ...

    # market data
    def latest_tick(self, symbol: str) -> Tick: ...
    def bars(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> Sequence[Bar]: ...
    def ticks(self, symbol: str, start: datetime, end: datetime) -> Sequence[Tick]: ...

    # state
    def positions(self, symbol: str | None = None) -> Sequence[PositionState]: ...
    def orders(self, symbol: str | None = None) -> Sequence[OrderState]: ...
    def history_deals(self, start: datetime, end: datetime) -> Sequence[DealRecord]: ...

    # trading
    def check_order(self, intent: OrderIntent) -> OrderCheckResult: ...
    def send_order(self, intent: OrderIntent) -> ExecutionReport: ...
    def modify_position(
        self, position_id: int, stop_loss: float, take_profit: float | None
    ) -> ExecutionReport:
        """Change SL/TP (prices). ``stop_loss`` is mandatory: a stop can never be removed."""
        ...

    def close_position(self, position_id: int, volume: float | None = None) -> ExecutionReport:
        """Close ``volume`` lots (None = whole position) at the executable price."""
        ...

    def cancel_order(self, order_id: int) -> ExecutionReport: ...
