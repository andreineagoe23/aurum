"""In-memory deterministic broker for unit tests on any platform.

* Market orders fill immediately: buys at ask + slippage, sells at bid - slippage, using the latest
  tick with ``time <= clock.now()``.
* LIMIT/STOP orders are only stored as pending (trigger simulation belongs to SimBroker, Phase 3).
* Commission is booked per side at each fill (negative ``commission`` on deals) to balance.
* Connection loss is simulated with ``disconnect()``: every call then raises.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TypeVar

from aurum.core.enums import (
    AccountTradeMode,
    DealEntry,
    ExecutionStatus,
    OrderType,
    Side,
)
from aurum.core.errors import (
    BrokerDisconnectedError,
    BrokerError,
    NoMarketDataError,
    SafetyViolationError,
)
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
    check_protective_levels,
)
from aurum.core.timeframes import Timeframe
from aurum.core.timeutil import to_utc
from aurum.ports.clock import ClockPort

T = TypeVar("T")


def example_xauusd_spec() -> SymbolSpec:
    """Illustrative 2-digit XAUUSD spec for tests. Real values come from the broker (Phase 1)."""
    return SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        contract_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level_points=0,
    )


class FakeBroker:
    def __init__(
        self,
        *,
        clock: ClockPort,
        symbol_spec: SymbolSpec | None = None,
        balance: float = 10_000.0,
        currency: str = "USD",
        trade_mode: AccountTradeMode = AccountTradeMode.DEMO,
        leverage: int = 100,
        commission_per_lot_side: float = 2.25,
        slippage_points: float = 0.0,
    ) -> None:
        self._clock = clock
        self._spec = symbol_spec or example_xauusd_spec()
        self._balance = balance
        self._currency = currency
        self._trade_mode = trade_mode
        self._leverage = leverage
        self._commission = commission_per_lot_side
        self._slippage = slippage_points * self._spec.point
        self._connected = False
        self._ticks: list[Tick] = []
        self._bars: dict[Timeframe, list[Bar]] = {}
        self._positions: dict[int, PositionState] = {}
        self._orders: dict[int, OrderState] = {}
        self._deals: list[DealRecord] = []
        self._reports_by_key: dict[str, ExecutionReport] = {}
        self._next_id = 1
        self.sent_intents: list[OrderIntent] = []
        """Every intent that reached the order book (for assertions in tests)."""

    # ------------------------------------------------------------------ test helpers
    def add_ticks(self, ticks: Sequence[Tick]) -> None:
        self._ticks.extend(ticks)
        self._ticks.sort(key=lambda t: t.time)

    def add_bars(self, bars: Sequence[Bar]) -> None:
        for bar in bars:
            self._bars.setdefault(bar.timeframe, []).append(bar)
        for series in self._bars.values():
            series.sort(key=lambda b: b.open_time)

    # ------------------------------------------------------------------ connection
    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _online(self, fn: Callable[[], T]) -> T:
        if not self._connected:
            raise BrokerDisconnectedError("FakeBroker is disconnected")
        return fn()

    # ------------------------------------------------------------------ account / instrument
    def account_info(self) -> AccountSnapshot:
        def build() -> AccountSnapshot:
            floating = sum(p.unrealized_pnl for p in self._marked_positions())
            return AccountSnapshot(
                login=1,
                server="Fake-Demo",
                currency=self._currency,
                trade_mode=self._trade_mode,
                balance=self._balance,
                equity=self._balance + floating,
                margin=0.0,
                margin_free=self._balance + floating,
                leverage=self._leverage,
                time=self._clock.now(),
            )

        return self._online(build)

    def symbol_info(self, symbol: str) -> SymbolSpec:
        return self._online(lambda: self._symbol(symbol))

    def _symbol(self, symbol: str) -> SymbolSpec:
        if symbol != self._spec.name:
            raise BrokerError(f"unknown symbol {symbol!r}")
        return self._spec

    # ------------------------------------------------------------------ market data
    def latest_tick(self, symbol: str) -> Tick:
        return self._online(lambda: self._latest_tick(symbol))

    def _latest_tick(self, symbol: str) -> Tick:
        self._symbol(symbol)
        now = self._clock.now()
        visible = [t for t in self._ticks if t.symbol == symbol and t.time <= now]
        if not visible:
            raise NoMarketDataError(f"no tick for {symbol} at or before {now.isoformat()}")
        return visible[-1]

    def bars(self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime) -> list[Bar]:
        start, end = to_utc(start), to_utc(end)

        def select() -> list[Bar]:
            now = self._clock.now()
            return [
                b
                for b in self._bars.get(timeframe, [])
                if b.symbol == symbol and start <= b.open_time < end and b.close_time <= now
            ]

        return self._online(select)

    def ticks(self, symbol: str, start: datetime, end: datetime) -> list[Tick]:
        start, end = to_utc(start), to_utc(end)

        def select() -> list[Tick]:
            now = self._clock.now()
            return [
                t
                for t in self._ticks
                if t.symbol == symbol and start <= t.time < end and t.time <= now
            ]

        return self._online(select)

    # ------------------------------------------------------------------ state
    def _marked_positions(self) -> list[PositionState]:
        marked = []
        for pos in self._positions.values():
            try:
                tick = self._latest_tick(pos.symbol)
            except NoMarketDataError:
                marked.append(pos)
                continue
            pnl = self._pnl(pos.side, pos.open_price, tick.exit_price_for(pos.side), pos.volume)
            marked.append(pos.model_copy(update={"unrealized_pnl": pnl}))
        return marked

    def positions(self, symbol: str | None = None) -> list[PositionState]:
        return self._online(
            lambda: [p for p in self._marked_positions() if symbol is None or p.symbol == symbol]
        )

    def orders(self, symbol: str | None = None) -> list[OrderState]:
        return self._online(
            lambda: [o for o in self._orders.values() if symbol is None or o.symbol == symbol]
        )

    def history_deals(self, start: datetime, end: datetime) -> list[DealRecord]:
        start, end = to_utc(start), to_utc(end)
        return self._online(lambda: [d for d in self._deals if start <= d.time < end])

    # ------------------------------------------------------------------ trading
    def check_order(self, intent: OrderIntent) -> OrderCheckResult:
        return self._online(lambda: self._check(intent))

    def _check(self, intent: OrderIntent) -> OrderCheckResult:
        reasons: list[str] = []
        if not intent.risk_decision.approved:
            reasons.append("risk decision not approved")
        try:
            spec = self._symbol(intent.symbol)
        except BrokerError as exc:
            return OrderCheckResult(ok=False, reasons=(str(exc),))
        if not spec.is_valid_volume(intent.volume):
            reasons.append(f"invalid volume {intent.volume}")
        reference = intent.price
        if intent.order_type is OrderType.MARKET:
            try:
                reference = self._latest_tick(intent.symbol).price_for(intent.side)
            except NoMarketDataError as exc:
                reasons.append(str(exc))
        if reference is not None:
            try:
                check_protective_levels(
                    intent.side, reference, intent.stop_loss, intent.take_profit
                )
            except ValueError as exc:
                reasons.append(str(exc))
            min_distance = spec.points_to_price(spec.stops_level_points)
            if abs(reference - intent.stop_loss) < min_distance:
                reasons.append("stop_loss closer than stops level")
        return OrderCheckResult(ok=not reasons, reasons=tuple(reasons))

    def send_order(self, intent: OrderIntent) -> ExecutionReport:
        return self._online(lambda: self._send(intent))

    def _send(self, intent: OrderIntent) -> ExecutionReport:
        # Defense in depth: OrderIntent validation already requires approval, but a model built
        # with model_construct() skips validation.
        if not intent.risk_decision.approved:
            raise SafetyViolationError("refusing order without an approved RiskDecision")
        prior = self._reports_by_key.get(intent.idempotency_key)
        if prior is not None:
            return prior.model_copy(update={"duplicate": True})

        now = self._clock.now()
        check = self._check(intent)
        if not check.ok:
            return ExecutionReport(
                status=ExecutionStatus.REJECTED,
                time=now,
                idempotency_key=intent.idempotency_key,
                message="; ".join(check.reasons),
            )

        self.sent_intents.append(intent)
        order_id = self._new_id()
        if intent.order_type is not OrderType.MARKET:
            assert intent.price is not None  # guaranteed by OrderIntent validation
            self._orders[order_id] = OrderState(
                order_id=order_id,
                symbol=intent.symbol,
                side=intent.side,
                order_type=intent.order_type,
                volume=intent.volume,
                price=intent.price,
                stop_loss=intent.stop_loss,
                take_profit=intent.take_profit,
                created_at=now,
                idempotency_key=intent.idempotency_key,
                magic=intent.magic,
            )
            report = ExecutionReport(
                status=ExecutionStatus.PLACED,
                time=now,
                idempotency_key=intent.idempotency_key,
                order_id=order_id,
                requested_price=intent.price,
                volume=intent.volume,
            )
        else:
            tick = self._latest_tick(intent.symbol)
            price = tick.price_for(intent.side) + intent.side.sign * self._slippage
            position_id = self._new_id()
            commission = -self._commission * intent.volume
            self._balance += commission
            self._positions[position_id] = PositionState(
                position_id=position_id,
                symbol=intent.symbol,
                side=intent.side,
                volume=intent.volume,
                open_price=price,
                open_time=now,
                stop_loss=intent.stop_loss,
                take_profit=intent.take_profit,
                idempotency_key=intent.idempotency_key,
                magic=intent.magic,
            )
            deal = self._record_deal(
                order_id,
                position_id,
                intent.symbol,
                intent.side,
                DealEntry.IN,
                intent.volume,
                price,
                commission,
                0.0,
                intent.idempotency_key,
                intent.magic,
            )
            report = ExecutionReport(
                status=ExecutionStatus.FILLED,
                time=now,
                idempotency_key=intent.idempotency_key,
                order_id=order_id,
                deal_id=deal.deal_id,
                position_id=position_id,
                requested_price=tick.price_for(intent.side),
                fill_price=price,
                volume=intent.volume,
                commission=commission,
            )
        self._reports_by_key[intent.idempotency_key] = report
        return report

    def modify_position(
        self, position_id: int, stop_loss: float, take_profit: float | None
    ) -> ExecutionReport:
        def modify() -> ExecutionReport:
            pos = self._position(position_id)
            reference = self._latest_tick(pos.symbol).exit_price_for(pos.side)
            check_protective_levels(pos.side, reference, stop_loss, take_profit)
            self._positions[position_id] = pos.model_copy(
                update={"stop_loss": stop_loss, "take_profit": take_profit}
            )
            return ExecutionReport(
                status=ExecutionStatus.MODIFIED,
                time=self._clock.now(),
                idempotency_key=pos.idempotency_key,
                position_id=position_id,
            )

        return self._online(modify)

    def close_position(self, position_id: int, volume: float | None = None) -> ExecutionReport:
        def close() -> ExecutionReport:
            pos = self._position(position_id)
            qty = pos.volume if volume is None else volume
            if not 0 < qty <= pos.volume + 1e-12:
                raise BrokerError(f"cannot close {qty} of {pos.volume} lots")
            tick = self._latest_tick(pos.symbol)
            price = tick.exit_price_for(pos.side) - pos.side.sign * self._slippage
            profit = self._pnl(pos.side, pos.open_price, price, qty)
            commission = -self._commission * qty
            self._balance += profit + commission
            remaining = round(pos.volume - qty, 8)
            if remaining > 0:
                self._positions[position_id] = pos.model_copy(update={"volume": remaining})
            else:
                del self._positions[position_id]
            deal = self._record_deal(
                None,
                position_id,
                pos.symbol,
                pos.side.opposite,
                DealEntry.OUT,
                qty,
                price,
                commission,
                profit,
                pos.idempotency_key,
                pos.magic,
            )
            return ExecutionReport(
                status=ExecutionStatus.CLOSED,
                time=self._clock.now(),
                idempotency_key=pos.idempotency_key,
                deal_id=deal.deal_id,
                position_id=position_id,
                requested_price=tick.exit_price_for(pos.side),
                fill_price=price,
                volume=qty,
                commission=commission,
            )

        return self._online(close)

    def cancel_order(self, order_id: int) -> ExecutionReport:
        def cancel() -> ExecutionReport:
            order = self._orders.pop(order_id, None)
            if order is None:
                raise BrokerError(f"unknown order {order_id}")
            return ExecutionReport(
                status=ExecutionStatus.CANCELLED,
                time=self._clock.now(),
                idempotency_key=order.idempotency_key,
                order_id=order_id,
            )

        return self._online(cancel)

    # ------------------------------------------------------------------ internals
    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id - 1

    def _position(self, position_id: int) -> PositionState:
        try:
            return self._positions[position_id]
        except KeyError:
            raise BrokerError(f"unknown position {position_id}") from None

    def _pnl(self, side: Side, open_price: float, close_price: float, volume: float) -> float:
        """Account-currency P&L of moving from open_price to close_price on ``volume`` lots."""
        move = (close_price - open_price) * side.sign
        return move * self._spec.money_per_price_unit_per_lot * volume

    def _record_deal(
        self,
        order_id: int | None,
        position_id: int,
        symbol: str,
        side: Side,
        entry: DealEntry,
        volume: float,
        price: float,
        commission: float,
        profit: float,
        key: str | None,
        magic: int = 0,
    ) -> DealRecord:
        deal = DealRecord(
            deal_id=self._new_id(),
            order_id=order_id,
            position_id=position_id,
            symbol=symbol,
            side=side,
            entry=entry,
            volume=volume,
            price=price,
            time=self._clock.now(),
            commission=commission,
            profit=profit,
            idempotency_key=key,
            magic=magic,
        )
        self._deals.append(deal)
        return deal
