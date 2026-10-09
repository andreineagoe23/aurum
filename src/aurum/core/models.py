"""Shared domain models (Pydantic v2, frozen, extra fields forbidden).

Units used throughout (see docs/DATA_DICTIONARY.md):

* **price**: quote currency per unit (USD per troy oz for XAUUSD).
* **points**: multiples of ``SymbolSpec.point`` (0.01 for a 2-digit XAUUSD).
* **volume**: lots. **money**: account currency (read from the broker; the demo is USD).
* **time**: timezone-aware UTC. Bars are keyed by open time and visible only at close time.
"""

import hashlib
import math
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from aurum.core.enums import (
    AccountTradeMode,
    DealEntry,
    DealReason,
    ExecutionStatus,
    OrderType,
    SessionPhase,
    Side,
)
from aurum.core.timeframes import Timeframe
from aurum.core.timeutil import UtcDatetime

Price = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Lots = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Money = Annotated[float, Field(allow_inf_nan=False)]
SymbolName = Annotated[str, Field(min_length=1, max_length=32)]
IdempotencyKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,29}$")]
"""Sent as the MT5 order comment. The MetaTrader5 Python API rejects comments over 29 chars
(measured in Phase 1); Fusion preserves the comment on orders, positions and deals (ADR-0005)."""

FeatureValue = float | int | bool | str


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def check_protective_levels(
    side: Side, reference: float, stop_loss: float | None, take_profit: float | None
) -> None:
    """Raise ``ValueError`` unless SL/TP sit on the correct side of ``reference`` (a price).

    BUY: stop_loss < reference < take_profit. SELL: take_profit < reference < stop_loss.
    """
    if stop_loss is not None and (stop_loss - reference) * side.sign >= 0:
        raise ValueError(f"{side} stop_loss {stop_loss} is not beyond entry {reference}")
    if take_profit is not None and (take_profit - reference) * side.sign <= 0:
        raise ValueError(f"{side} take_profit {take_profit} is not beyond entry {reference}")


# ------------------------------------------------------------------------------ market data


class Tick(DomainModel):
    """One quote. ``time`` is the broker quote time (UTC).

    Crossed/zero spreads are allowed here on purpose: data QA (Phase 2) flags them and the
    risk engine refuses to trade on them, but raw data must be representable.
    """

    symbol: SymbolName
    time: UtcDatetime
    bid: Price
    ask: Price
    last: float | None = None
    volume: float = Field(default=0.0, ge=0)

    @property
    def spread(self) -> float:
        """ask - bid, in price units."""
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return (self.ask + self.bid) / 2

    def price_for(self, side: Side) -> float:
        """Executable price for opening ``side``: buys pay the ask, sells hit the bid."""
        return self.ask if side is Side.BUY else self.bid

    def exit_price_for(self, side: Side) -> float:
        """Executable price for closing a ``side`` position (BUY closes at bid, SELL at ask)."""
        return self.bid if side is Side.BUY else self.ask


class Bar(DomainModel):
    """OHLC bar keyed by ``open_time``; covers ``[open_time, close_time)``. Prices are bid."""

    symbol: SymbolName
    timeframe: Timeframe
    open_time: UtcDatetime
    open: Price
    high: Price
    low: Price
    close: Price
    tick_volume: int = Field(default=0, ge=0)
    spread_points: int | None = Field(default=None, ge=0)
    real_volume: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.timeframe.is_aligned(self.open_time):
            raise ValueError(f"open_time {self.open_time} not aligned to {self.timeframe}")
        if self.high < max(self.open, self.close, self.low) or self.low > min(
            self.open, self.close
        ):
            raise ValueError(f"inconsistent OHLC: {self.open} {self.high} {self.low} {self.close}")
        return self

    @property
    def close_time(self) -> datetime:
        """First instant at which this bar is complete and may be used."""
        return self.open_time + self.timeframe.duration


class MarketEvent(DomainModel):
    """A tick or completed bar delivered to the core at ``time`` (when it became known).

    Causality guard: an event can never be delivered before its data exists
    (``tick.time <= time``; ``bar.close_time <= time``).
    """

    time: UtcDatetime
    symbol: SymbolName
    tick: Tick | None = None
    bar: Bar | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.tick is None) == (self.bar is None):
            raise ValueError("MarketEvent needs exactly one of tick or bar")
        if self.tick is not None:
            if self.tick.symbol != self.symbol:
                raise ValueError("tick symbol mismatch")
            if self.tick.time > self.time:
                raise ValueError("tick delivered before its quote time")
        if self.bar is not None:
            if self.bar.symbol != self.symbol:
                raise ValueError("bar symbol mismatch")
            if self.bar.close_time > self.time:
                raise ValueError(
                    f"bar {self.bar.open_time} delivered at {self.time} before close "
                    f"{self.bar.close_time} (look-ahead)"
                )
        return self


# ------------------------------------------------------------------------------ sessions


class SessionState(DomainModel):
    """Session phase at instant ``time``. ``local_time`` is the session-timezone wall clock."""

    time: UtcDatetime
    local_time: AwareDatetime
    phase: SessionPhase
    session: str | None = None
    """Name of the active session window (only set while OPEN)."""
    trading_day: date | None = None
    """Local date of the trading day, or None on non-trading days."""
    force_flat_at: UtcDatetime | None = None

    @property
    def entries_allowed(self) -> bool:
        return self.phase.entries_allowed

    @property
    def must_be_flat(self) -> bool:
        return self.phase.must_be_flat


# ------------------------------------------------------------------------------ broker state


class SymbolSpec(DomainModel):
    """Contract specification snapshot from the broker (never hard-coded for real trading)."""

    name: SymbolName
    digits: int = Field(ge=0, le=8)
    point: Price
    tick_size: Price
    tick_value: Price
    """Account-currency value of a ``tick_size`` move for 1.0 lot."""
    contract_size: Price
    volume_min: Lots
    volume_max: Lots
    volume_step: Lots
    stops_level_points: int = Field(default=0, ge=0)
    freeze_level_points: int = Field(default=0, ge=0)
    currency_profit: str = "USD"
    currency_margin: str = "USD"
    captured_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.volume_min > self.volume_max:
            raise ValueError("volume_min > volume_max")
        return self

    @property
    def money_per_price_unit_per_lot(self) -> float:
        """Account currency gained per 1.0 price move per 1.0 lot (USD 100 for 100 oz gold)."""
        return self.tick_value / self.tick_size

    def price_to_points(self, price_distance: float) -> float:
        return price_distance / self.point

    def points_to_price(self, points: float) -> float:
        return points * self.point

    def round_price(self, price: float) -> float:
        return round(round(price / self.tick_size) * self.tick_size, self.digits)

    def is_valid_volume(self, volume: float) -> bool:
        """Within [min, max] and an integer multiple of ``volume_step`` (1e-9 tolerance)."""
        if not self.volume_min - 1e-12 <= volume <= self.volume_max + 1e-12:
            return False
        steps = volume / self.volume_step
        return math.isclose(steps, round(steps), abs_tol=1e-9)


class AccountSnapshot(DomainModel):
    login: int
    server: str
    currency: str
    trade_mode: AccountTradeMode
    balance: Money
    equity: Money
    margin: Money = 0.0
    margin_free: Money
    leverage: int = Field(ge=1)
    time: UtcDatetime

    @property
    def is_demo(self) -> bool:
        return self.trade_mode is AccountTradeMode.DEMO


class PositionState(DomainModel):
    position_id: int
    symbol: SymbolName
    side: Side
    volume: Lots
    open_price: Price
    open_time: UtcDatetime
    stop_loss: Price | None
    """None means the broker shows no stop: reconciliation must treat that as an incident."""
    take_profit: Price | None = None
    idempotency_key: str | None = None
    magic: int = 0
    unrealized_pnl: Money = 0.0


class OrderState(DomainModel):
    """A pending (not yet filled) order at the broker."""

    order_id: int
    symbol: SymbolName
    side: Side
    order_type: OrderType
    volume: Lots
    price: Price
    stop_loss: Price | None
    take_profit: Price | None = None
    created_at: UtcDatetime
    idempotency_key: str | None = None
    magic: int = 0


class DealRecord(DomainModel):
    """A fill from the broker's deal history."""

    deal_id: int
    order_id: int | None
    position_id: int
    symbol: SymbolName
    side: Side
    entry: DealEntry
    volume: Lots
    price: Price
    time: UtcDatetime
    commission: Money = 0.0
    """Signed, as the broker books it (costs are negative)."""
    profit: Money = 0.0
    idempotency_key: str | None = None
    reason: DealReason | None = None
    """Why the deal happened (stop loss, take profit, API request...), when the broker says."""
    magic: int = 0
    """The order's magic number (ownership needs it together with the "AU" key prefix)."""


# ------------------------------------------------------------------------------ decisions


class Signal(DomainModel):
    """A strategy's proposal. Strategies return Signals only; they can never create orders.

    ``data_as_of`` is the latest close time (or tick time) of any data the decision used; it must
    not exceed the decision ``time``. ``reasons``/``features`` describe the decision in
    objective terms (numbers and comparisons), never subjective words.
    """

    strategy_id: Annotated[str, Field(pattern=r"^[a-z0-9_.:-]{1,64}$")]
    symbol: SymbolName
    side: Side
    time: UtcDatetime
    data_as_of: UtcDatetime
    entry_type: OrderType = OrderType.MARKET
    entry_price: Price | None = None
    """Required for LIMIT/STOP; for MARKET an optional reference price."""
    stop_loss: Price
    take_profit: Price | None = None
    max_hold: timedelta | None = None
    reasons: tuple[str, ...] = Field(min_length=1)
    features: dict[str, FeatureValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.data_as_of > self.time:
            raise ValueError(f"data_as_of {self.data_as_of} is after decision time {self.time}")
        if self.entry_type is not OrderType.MARKET and self.entry_price is None:
            raise ValueError(f"{self.entry_type} signal needs entry_price")
        if self.entry_price is not None:
            check_protective_levels(self.side, self.entry_price, self.stop_loss, self.take_profit)
        if self.max_hold is not None and self.max_hold <= timedelta(0):
            raise ValueError("max_hold must be positive")
        return self

    @property
    def key(self) -> str:
        """Deterministic identity: same strategy, symbol, side and decision time -> same key.

        Used to derive the order idempotency key so a restart re-deciding the same signal can
        never produce a second order.
        """
        raw = f"{self.strategy_id}|{self.symbol}|{self.side}|{self.time.isoformat()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:20]


class RiskDecision(DomainModel):
    """The risk engine's verdict on one Signal. Only ``approved=True`` decisions become orders."""

    decision_id: str = Field(min_length=1)
    signal_key: str = Field(min_length=1)
    approved: bool
    volume: float = Field(ge=0, allow_inf_nan=False)
    """Approved size in lots (0 when rejected)."""
    stop_distance_points: float | None = Field(default=None, gt=0)
    risk_amount: Money | None = None
    """Account-currency loss if the stop is hit at the approved volume (incl. costs)."""
    reasons: tuple[str, ...] = ()
    decided_at: UtcDatetime

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.approved and self.volume <= 0:
            raise ValueError("approved decision must have volume > 0")
        if not self.approved:
            if self.volume != 0:
                raise ValueError("rejected decision must have volume 0")
            if not self.reasons:
                raise ValueError("rejected decision must state reasons")
        return self


class OrderIntent(DomainModel):
    """An order ready for a broker. Construction itself enforces the safety invariants:

    * it embeds an **approved** ``RiskDecision`` and cannot exceed its volume;
    * it always carries a broker-side ``stop_loss``;
    * it has an idempotency key (sent as the MT5 order comment).
    """

    idempotency_key: IdempotencyKey
    symbol: SymbolName
    side: Side
    order_type: OrderType
    volume: Lots
    price: Price | None = None
    """Required for LIMIT/STOP. For MARKET, optional reference price used for SL/TP checks."""
    stop_loss: Price
    take_profit: Price | None = None
    created_at: UtcDatetime
    risk_decision: RiskDecision
    magic: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.risk_decision.approved:
            raise ValueError("OrderIntent requires an approved RiskDecision")
        if self.volume > self.risk_decision.volume + 1e-12:
            raise ValueError(
                f"volume {self.volume} exceeds approved volume {self.risk_decision.volume}"
            )
        if self.order_type is not OrderType.MARKET and self.price is None:
            raise ValueError(f"{self.order_type} order needs a price")
        if self.price is not None:
            check_protective_levels(self.side, self.price, self.stop_loss, self.take_profit)
        return self

    @classmethod
    def from_signal(
        cls, signal: Signal, decision: RiskDecision, *, created_at: datetime, magic: int = 0
    ) -> "OrderIntent":
        """Build the intent for an approved decision about ``signal``."""
        if decision.signal_key != signal.key:
            raise ValueError("RiskDecision does not belong to this Signal")
        return cls(
            idempotency_key=f"AU{signal.key}",
            symbol=signal.symbol,
            side=signal.side,
            order_type=signal.entry_type,
            volume=decision.volume,
            price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            created_at=created_at,
            risk_decision=decision,
            magic=magic,
        )


class OrderCheckResult(DomainModel):
    """Pre-trade check outcome (mirrors MT5 ``order_check``)."""

    ok: bool
    reasons: tuple[str, ...] = ()
    margin_required: Money | None = None


class ExecutionReport(DomainModel):
    """Outcome of one broker action."""

    status: ExecutionStatus
    time: UtcDatetime
    idempotency_key: str | None = None
    order_id: int | None = None
    deal_id: int | None = None
    position_id: int | None = None
    requested_price: float | None = None
    fill_price: float | None = None
    volume: float | None = None
    retcode: int | None = None
    message: str = ""
    commission: Money = 0.0
    duplicate: bool = False
    """True when the idempotency key was already known and nothing new was sent."""


# ------------------------------------------------------------------------------ research


class TrialRecord(DomainModel):
    """One immutable row of the trial ledger (every backtest/parameter trial, good or bad)."""

    trial_id: str
    created_at: UtcDatetime
    strategy: str
    variant: str
    params: dict[str, Any]
    data_start: UtcDatetime
    data_end: UtcDatetime
    metrics: dict[str, float | None]
    notes: str = ""
    code_version: str | None = None
    config_hash: str | None = None
    sealed_test_opened: bool = False
    record_hash: str
