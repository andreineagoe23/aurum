"""SimBroker: ``BrokerPort`` over a ``HistoricalMarket`` (MASTER_PROMPT §7 Phase 3).

Execution model (all prices USD/oz, volumes lots, money account currency):

* **Timing.** An action requested at clock time ``now`` reaches the broker at
  ``now + latency``. A market order (or a close) fills on the **next executable quote** at or
  after that instant, never at the trigger bar's close. With no quote within
  ``max_quote_wait`` (market closed, data gap) it is rejected.
* **Prices.** Buys fill at the ask, sells at the bid. Market fills, stop entries and stop-loss
  exits get ``slippage_points`` of adverse slippage. Limit entries and take-profits fill at
  their limit price exactly (no price improvement: conservative).
* **Stops and targets** are broker-side. In TICKS mode they trigger on the first quote that
  reaches them (BUY: bid <= SL / bid >= TP; SELL: ask >= SL / ask <= TP); a stop fills at that
  quote, so gaps through the stop are paid in full. In BARS mode an M1 bar triggers them when
  its range reaches them; a bar that opens beyond the stop fills at the open (gap). If one bar
  reaches **both** stop and target, the order inside the bar is unknown: the stop is assumed to
  come first (worst case) and the trade is flagged ``SL_TP_SAME_BAR``.
* **Pending entries** (STOP/LIMIT) trigger like stops/targets. In BARS mode an entry inside a
  bar leaves the order of entry and exit unknown within that bar: the stop is assumed to
  follow the entry, a target counts only when it must have followed it, and the trade is
  flagged ``ENTRY_BAR`` whenever that assumption decided anything.
* **Costs.** Commission ``commission_per_lot_side x volume`` on every fill, rounded to cents and
  booked negative (like the broker's deals). Profit is booked rounded to cents.
* **Rules.** Volume min/max/step, stops level (minimum SL/TP and pending distance) and freeze
  level from the ``SymbolSpec``, margin (``volume x contract x price / leverage``), duplicate
  idempotency keys, and pluggable rejection hooks.

The strategy and the engine never see the market ahead of ``now``; the broker looks ahead only
to find the quote an order executes on, exactly as a real broker would.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

import numpy as np

from aurum.backtest.market import (
    BarSegment,
    DataMode,
    HistoricalMarket,
    Quote,
    TickSegment,
)
from aurum.core.enums import (
    AccountTradeMode,
    DealEntry,
    DealReason,
    ExecutionStatus,
    OrderType,
    Side,
)
from aurum.core.errors import BrokerError, NoMarketDataError, SafetyViolationError
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
from aurum.data.market_hours import MarketHours
from aurum.features.view import from_ms, to_ms
from aurum.ports.clock import ClockPort

RejectionHook = Callable[[OrderIntent, Quote], str | None]
"""Called before a new order executes; return a reason to reject it (e.g. a spread cap)."""

MONEY_DIGITS = 2


class IntrabarRule(StrEnum):
    """Which BARS-mode assumption, if any, decided a trade's outcome."""

    NONE = "none"
    SL_TP_SAME_BAR = "sl_tp_same_bar"
    """Stop and target both inside one M1 bar: stop assumed first (worst case)."""
    ENTRY_BAR = "entry_bar"
    """A pending entry filled inside a bar that also reached the stop or the target."""


@dataclass(frozen=True, slots=True)
class SimCosts:
    commission_per_lot_side: float
    """Account currency per 1.0 lot per side (broker.yaml)."""
    slippage_points: float = 0.0
    latency: timedelta = timedelta(0)
    max_quote_wait: timedelta = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class SimTrade:
    """One closed position with everything needed for cost attribution.

    ``*_bid``/``*_ask`` are the reference quote of each fill: the quote it executed on, or, for
    limit-type fills and BARS-mode stops, a quote at the fill level with the bar's spread.
    ``best``/``worst`` are the most favourable/adverse exit-side prices seen while open
    (bid for BUY, ask for SELL).
    """

    position_id: int
    idempotency_key: str | None
    magic: int
    side: Side
    volume: float
    entry_type: OrderType
    entry_time: datetime
    entry_price: float
    entry_bid: float
    entry_ask: float
    entry_mode: DataMode
    exit_time: datetime
    exit_price: float
    exit_bid: float
    exit_ask: float
    exit_mode: DataMode
    exit_reason: DealReason
    stop_loss: float | None
    take_profit: float | None
    best: float
    worst: float
    commission: float
    """Sum of both fills, negative."""
    profit: float
    """Booked profit of the closing deal (cents)."""
    intrabar: IntrabarRule


@dataclass(slots=True)
class _Order:
    order_id: int
    key: str
    magic: int
    side: Side
    order_type: OrderType
    volume: float
    price: float
    sl: float
    tp: float | None
    created_ms: int
    cursor_ms: int
    cursor_index: int | None


@dataclass(slots=True)
class _Position:
    position_id: int
    key: str | None
    magic: int
    side: Side
    volume: float
    entry_type: OrderType
    open_ms: int
    open_price: float
    entry_bid: float
    entry_ask: float
    entry_mode: DataMode
    sl: float
    tp: float | None
    cursor_ms: int
    cursor_index: int | None
    best: float
    worst: float
    commission: float
    intrabar: IntrabarRule = IntrabarRule.NONE


@dataclass(frozen=True, slots=True)
class _Exit:
    time_ms: int
    price: float
    bid: float
    ask: float
    mode: DataMode
    reason: DealReason
    best: float
    worst: float
    intrabar: IntrabarRule = IntrabarRule.NONE


@dataclass(slots=True)
class _Book:
    positions: dict[int, _Position] = field(default_factory=dict)
    orders: dict[int, _Order] = field(default_factory=dict)
    deals: list[DealRecord] = field(default_factory=list)
    trades: list[SimTrade] = field(default_factory=list)
    reports_by_key: dict[str, ExecutionReport] = field(default_factory=dict)


class SimBroker:
    def __init__(
        self,
        *,
        market: HistoricalMarket,
        spec: SymbolSpec,
        clock: ClockPort,
        costs: SimCosts,
        balance: float = 10_000.0,
        leverage: int = 100,
        currency: str = "USD",
        market_hours: MarketHours | None = None,
        rejection_hooks: Sequence[RejectionHook] = (),
    ) -> None:
        if spec.name != market.symbol:
            raise ValueError(f"spec {spec.name} does not match market {market.symbol}")
        self._market = market
        self._spec = spec
        self._clock = clock
        self._costs = costs
        self._balance = balance
        self._leverage = leverage
        self._currency = currency
        self._hours = market_hours
        self._hooks = tuple(rejection_hooks)
        self._book = _Book()
        self._connected = False
        self._next_id = 1
        self._processed_ms = to_ms(clock.now())
        self._slip = spec.points_to_price(costs.slippage_points)
        self._value = spec.money_per_price_unit_per_lot

    # ------------------------------------------------------------------ simulator API
    @property
    def trades(self) -> list[SimTrade]:
        """Closed positions so far, in closing order."""
        return list(self._book.trades)

    def advance_to(self, t: datetime) -> None:
        """Process every quote before ``t``: pending triggers, stops and targets."""
        self._advance(to_ms(t))

    # ------------------------------------------------------------------ connection
    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------ account / instrument
    def account_info(self) -> AccountSnapshot:
        floating = sum(p.unrealized_pnl for p in self.positions())
        equity = self._balance + floating
        margin = sum(
            p.volume * self._spec.contract_size * p.open_price / self._leverage
            for p in self._book.positions.values()
        )
        return AccountSnapshot(
            login=0,
            server="Aurum-Sim",
            currency=self._currency,
            trade_mode=AccountTradeMode.DEMO,
            balance=self._balance,
            equity=equity,
            margin=margin,
            margin_free=equity - margin,
            leverage=self._leverage,
            time=self._clock.now(),
        )

    def symbol_info(self, symbol: str) -> SymbolSpec:
        self._check_symbol(symbol)
        return self._spec

    def _check_symbol(self, symbol: str) -> None:
        if symbol != self._spec.name:
            raise BrokerError(f"unknown symbol {symbol!r}")

    # ------------------------------------------------------------------ market data
    def latest_tick(self, symbol: str) -> Tick:
        self._check_symbol(symbol)
        q = self._market.last_quote(to_ms(self._clock.now()))
        if q is None:
            raise NoMarketDataError(f"no quote for {symbol} at {self._clock.now().isoformat()}")
        return Tick(symbol=symbol, time=q.time, bid=q.bid, ask=q.ask)

    def bars(self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime) -> list[Bar]:
        """Completed bars with ``start <= open_time < end`` (slow path; strategies use views)."""
        self._check_symbol(symbol)
        s = self._market.store.view_at(self._clock.now()).bars(timeframe)
        lo, hi = to_ms(start), to_ms(end)
        out = []
        for i in range(len(s)):
            if lo <= s.open_time[i] < hi:
                out.append(
                    Bar(
                        symbol=symbol,
                        timeframe=timeframe,
                        open_time=from_ms(int(s.open_time[i])),
                        open=float(s.open[i]),
                        high=float(s.high[i]),
                        low=float(s.low[i]),
                        close=float(s.close[i]),
                        tick_volume=int(s.tick_count[i]),
                        spread_points=int(s.spread_min[i]) if s.spread_min[i] >= 0 else None,
                    )
                )
        return out

    def ticks(self, symbol: str, start: datetime, end: datetime) -> list[Tick]:
        """Stored ticks (TICKS-mode days only) with ``start <= time < min(end, now]``."""
        self._check_symbol(symbol)
        hi = min(to_ms(end), to_ms(self._clock.now()) + 1)
        out: list[Tick] = []
        for seg in self._market.segments(to_ms(start), hi):
            if isinstance(seg, TickSegment):
                out.extend(
                    Tick(symbol=symbol, time=from_ms(int(t)), bid=float(b), ask=float(a))
                    for t, b, a in zip(seg.time, seg.bid, seg.ask, strict=True)
                )
        return out

    # ------------------------------------------------------------------ state
    def positions(self, symbol: str | None = None) -> list[PositionState]:
        q = self._market.last_quote(to_ms(self._clock.now()))
        out = []
        for p in self._book.positions.values():
            if symbol is not None and symbol != self._spec.name:
                continue
            pnl = 0.0
            if q is not None:
                mark = q.bid if p.side is Side.BUY else q.ask
                pnl = self._pnl(p.side, p.open_price, mark, p.volume)
            out.append(
                PositionState(
                    position_id=p.position_id,
                    symbol=self._spec.name,
                    side=p.side,
                    volume=p.volume,
                    open_price=p.open_price,
                    open_time=from_ms(p.open_ms),
                    stop_loss=p.sl,
                    take_profit=p.tp,
                    idempotency_key=p.key,
                    magic=p.magic,
                    unrealized_pnl=pnl,
                )
            )
        return out

    def orders(self, symbol: str | None = None) -> list[OrderState]:
        if symbol is not None and symbol != self._spec.name:
            return []
        return [
            OrderState(
                order_id=o.order_id,
                symbol=self._spec.name,
                side=o.side,
                order_type=o.order_type,
                volume=o.volume,
                price=o.price,
                stop_loss=o.sl,
                take_profit=o.tp,
                created_at=from_ms(o.created_ms),
                idempotency_key=o.key,
                magic=o.magic,
            )
            for o in self._book.orders.values()
        ]

    def history_deals(self, start: datetime, end: datetime) -> list[DealRecord]:
        lo, hi = to_ms(start), to_ms(end)
        return [d for d in self._book.deals if lo <= to_ms(d.time) < hi]

    # ------------------------------------------------------------------ trading
    def check_order(self, intent: OrderIntent) -> OrderCheckResult:
        reasons = self._static_checks(intent)
        q = self._market.last_quote(to_ms(self._clock.now()))
        if q is None:
            reasons.append("no quote")
        else:
            reasons.extend(self._quote_checks(intent, q))
        price = intent.price if intent.price is not None else (q.ask if q else None)
        margin = self._margin(intent.volume, price) if price is not None else None
        return OrderCheckResult(ok=not reasons, reasons=tuple(reasons), margin_required=margin)

    def send_order(self, intent: OrderIntent) -> ExecutionReport:
        # Defense in depth: OrderIntent validation already requires approval, but a model built
        # with model_construct() skips validation.
        if not intent.risk_decision.approved:
            raise SafetyViolationError("refusing order without an approved RiskDecision")
        prior = self._book.reports_by_key.get(intent.idempotency_key)
        if prior is not None:
            return prior.model_copy(update={"duplicate": True})
        report = self._execute(intent)
        self._book.reports_by_key[intent.idempotency_key] = report
        return report

    def _execute(self, intent: OrderIntent) -> ExecutionReport:
        now = self._clock.now()
        reasons = self._static_checks(intent)
        if reasons:
            return self._rejected(intent, now, reasons)
        arrival = to_ms(now + self._costs.latency)
        if self._hours is not None and not self._hours.is_open(from_ms(arrival)):
            return self._rejected(intent, now, ["market closed"])
        q = self._market.next_quote(arrival, arrival + self._max_wait_ms())
        if q is None:
            return self._rejected(intent, now, [f"no quote within {self._costs.max_quote_wait}"])
        self._advance(q.time_ms)
        reasons = self._quote_checks(intent, q)
        price = intent.price if intent.order_type is not OrderType.MARKET else q.ask
        assert price is not None  # guaranteed by OrderIntent validation
        if self._margin(intent.volume, price) > self._free_margin(q):
            reasons.append("insufficient margin")
        reasons.extend(r for hook in self._hooks if (r := hook(intent, q)) is not None)
        if reasons:
            return self._rejected(intent, now, reasons, requested=price)
        order_id = self._new_id()
        if intent.order_type is OrderType.MARKET:
            fill = q.ask + self._slip if intent.side is Side.BUY else q.bid - self._slip
            pos = self._open(
                order_id=order_id,
                key=intent.idempotency_key,
                magic=intent.magic,
                side=intent.side,
                volume=intent.volume,
                entry_type=OrderType.MARKET,
                time_ms=q.time_ms,
                price=fill,
                bid=q.bid,
                ask=q.ask,
                mode=q.mode,
                sl=intent.stop_loss,
                tp=intent.take_profit,
                cursor_ms=q.time_ms,
                cursor_index=q.index if q.mode is DataMode.TICKS else None,
            )
            return ExecutionReport(
                status=ExecutionStatus.FILLED,
                time=from_ms(q.time_ms),
                idempotency_key=intent.idempotency_key,
                order_id=order_id,
                deal_id=self._book.deals[-1].deal_id,
                position_id=pos.position_id,
                requested_price=q.ask if intent.side is Side.BUY else q.bid,
                fill_price=pos.open_price,
                volume=intent.volume,
                commission=pos.commission,
            )
        assert intent.price is not None
        self._book.orders[order_id] = _Order(
            order_id=order_id,
            key=intent.idempotency_key,
            magic=intent.magic,
            side=intent.side,
            order_type=intent.order_type,
            volume=intent.volume,
            price=intent.price,
            sl=intent.stop_loss,
            tp=intent.take_profit,
            created_ms=q.time_ms,
            cursor_ms=q.time_ms,
            cursor_index=q.index if q.mode is DataMode.TICKS else None,
        )
        return ExecutionReport(
            status=ExecutionStatus.PLACED,
            time=from_ms(q.time_ms),
            idempotency_key=intent.idempotency_key,
            order_id=order_id,
            requested_price=intent.price,
            volume=intent.volume,
        )

    def modify_position(
        self, position_id: int, stop_loss: float, take_profit: float | None
    ) -> ExecutionReport:
        now = self._clock.now()
        q = self._arrival_quote(now)
        pos = self._book.positions.get(position_id)
        if pos is None:
            return self._failed(now, f"position {position_id} not found")
        if q is None:
            return self._failed(now, "no quote", key=pos.key)
        reasons = self._freeze_check_position(pos, q)
        reasons += self._stops_vs_quote(pos.side, stop_loss, take_profit, q)
        if reasons:
            return self._failed(now, "; ".join(reasons), key=pos.key)
        pos.sl, pos.tp = stop_loss, take_profit
        return ExecutionReport(
            status=ExecutionStatus.MODIFIED,
            time=from_ms(q.time_ms),
            idempotency_key=pos.key,
            position_id=position_id,
        )

    def close_position(self, position_id: int, volume: float | None = None) -> ExecutionReport:
        """Close the whole position at the next quote (partial closes are not simulated)."""
        now = self._clock.now()
        if position_id not in self._book.positions:
            return self._failed(now, f"position {position_id} not found")
        pos = self._book.positions[position_id]
        if volume is not None and abs(volume - pos.volume) > 1e-9:
            return self._failed(now, "partial close is not simulated", key=pos.key)
        q = self._arrival_quote(now)
        if q is None:
            return self._failed(now, "no quote", key=pos.key)
        if position_id not in self._book.positions:  # stopped out before the close arrived
            return self._failed(now, f"position {position_id} already closed", key=pos.key)
        mark = q.bid if pos.side is Side.BUY else q.ask
        price = mark - pos.side.sign * self._slip
        best, worst = self._extremes(pos.side, pos.best, pos.worst, mark)
        exit_ = _Exit(q.time_ms, price, q.bid, q.ask, q.mode, DealReason.EXPERT, best, worst)
        deal = self._close(pos, exit_)
        return ExecutionReport(
            status=ExecutionStatus.CLOSED,
            time=from_ms(q.time_ms),
            idempotency_key=pos.key,
            deal_id=deal.deal_id,
            position_id=position_id,
            requested_price=mark,
            fill_price=price,
            volume=pos.volume,
            commission=deal.commission,
        )

    def cancel_order(self, order_id: int) -> ExecutionReport:
        now = self._clock.now()
        q = self._arrival_quote(now)
        order = self._book.orders.get(order_id)
        if order is None:
            return self._failed(now, f"order {order_id} not found (filled or cancelled)")
        if q is not None and (reason := self._freeze_check_order(order, q)):
            return self._failed(now, reason, key=order.key)
        del self._book.orders[order_id]
        return ExecutionReport(
            status=ExecutionStatus.CANCELLED,
            time=from_ms(q.time_ms) if q else now,
            idempotency_key=order.key,
            order_id=order_id,
        )

    # ------------------------------------------------------------------ checks
    def _static_checks(self, intent: OrderIntent) -> list[str]:
        reasons = []
        if intent.symbol != self._spec.name:
            reasons.append(f"unknown symbol {intent.symbol!r}")
        if not self._spec.is_valid_volume(intent.volume):
            reasons.append(
                f"invalid volume {intent.volume} (min {self._spec.volume_min}, "
                f"max {self._spec.volume_max}, step {self._spec.volume_step})"
            )
        return reasons

    def _quote_checks(self, intent: OrderIntent, q: Quote) -> list[str]:
        if intent.order_type is OrderType.MARKET:
            return self._stops_vs_quote(intent.side, intent.stop_loss, intent.take_profit, q)
        assert intent.price is not None
        level = self._spec.points_to_price(self._spec.stops_level_points)
        p, side = intent.price, intent.side
        reasons = []
        # Pending price distance from the market (MT5 rules: limit below/above, stop beyond).
        ref = q.ask if side is Side.BUY else q.bid
        below = (intent.order_type is OrderType.LIMIT) == (side is Side.BUY)
        away = (ref - p) if below else (p - ref)
        if away <= 0 or away < level - 1e-9:
            reasons.append(f"invalid price {p}: market {ref}, stops level {level}")
        reasons += self._distance("stop_loss", (p - intent.stop_loss) * side.sign, level)
        if intent.take_profit is not None:
            reasons += self._distance("take_profit", (intent.take_profit - p) * side.sign, level)
        return reasons

    def _stops_vs_quote(self, side: Side, sl: float, tp: float | None, q: Quote) -> list[str]:
        """MT5 rule: BUY positions close at the bid (bid - SL, TP - bid >= stops level), SELL
        positions at the ask (SL - ask, ask - TP >= stops level)."""
        level = self._spec.points_to_price(self._spec.stops_level_points)
        ref = q.bid if side is Side.BUY else q.ask
        reasons = self._distance("stop_loss", (ref - sl) * side.sign, level)
        if tp is not None:
            reasons += self._distance("take_profit", (tp - ref) * side.sign, level)
        return reasons

    @staticmethod
    def _distance(name: str, distance: float, level: float) -> list[str]:
        if distance <= 1e-9 or distance < level - 1e-9:
            return [f"invalid stops: {name} {distance:.2f} from price, stops level {level:.2f}"]
        return []

    def _freeze_check_position(self, pos: _Position, q: Quote) -> list[str]:
        level = self._spec.points_to_price(self._spec.freeze_level_points)
        if level <= 0:
            return []
        ref = q.bid if pos.side is Side.BUY else q.ask
        near = [abs(ref - pos.sl) < level] + ([abs(pos.tp - ref) < level] if pos.tp else [])
        return ["frozen: stop or target within freeze level"] if any(near) else []

    def _freeze_check_order(self, order: _Order, q: Quote) -> str | None:
        level = self._spec.points_to_price(self._spec.freeze_level_points)
        ref = q.ask if order.side is Side.BUY else q.bid
        if level > 0 and abs(ref - order.price) < level:
            return "frozen: order price within freeze level"
        return None

    def _margin(self, volume: float, price: float) -> float:
        return volume * self._spec.contract_size * price / self._leverage

    def _free_margin(self, q: Quote) -> float:
        floating = sum(
            self._pnl(p.side, p.open_price, q.bid if p.side is Side.BUY else q.ask, p.volume)
            for p in self._book.positions.values()
        )
        used = sum(self._margin(p.volume, p.open_price) for p in self._book.positions.values())
        return self._balance + floating - used

    # ------------------------------------------------------------------ reports
    def _rejected(
        self,
        intent: OrderIntent,
        now: datetime,
        reasons: list[str],
        requested: float | None = None,
    ) -> ExecutionReport:
        return ExecutionReport(
            status=ExecutionStatus.REJECTED,
            time=now,
            idempotency_key=intent.idempotency_key,
            requested_price=requested,
            message="; ".join(reasons),
        )

    @staticmethod
    def _failed(now: datetime, message: str, key: str | None = None) -> ExecutionReport:
        return ExecutionReport(
            status=ExecutionStatus.REJECTED, time=now, idempotency_key=key, message=message
        )

    def _arrival_quote(self, now: datetime) -> Quote | None:
        """Quote an action requested at ``now`` executes on; processes the market up to it."""
        arrival = to_ms(now + self._costs.latency)
        if self._hours is not None and not self._hours.is_open(from_ms(arrival)):
            return None
        q = self._market.next_quote(arrival, arrival + self._max_wait_ms())
        if q is not None:
            self._advance(q.time_ms)
        return q

    def _max_wait_ms(self) -> int:
        return int(self._costs.max_quote_wait / timedelta(milliseconds=1))

    # ------------------------------------------------------------------ book keeping
    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id - 1

    def _pnl(self, side: Side, open_price: float, close_price: float, volume: float) -> float:
        return (close_price - open_price) * side.sign * self._value * volume

    def _commission(self, volume: float) -> float:
        return -round(self._costs.commission_per_lot_side * volume, MONEY_DIGITS)

    def _deal(
        self,
        *,
        order_id: int | None,
        position_id: int,
        side: Side,
        entry: DealEntry,
        volume: float,
        price: float,
        time_ms: int,
        commission: float,
        profit: float,
        key: str | None,
        reason: DealReason,
    ) -> DealRecord:
        deal = DealRecord(
            deal_id=self._new_id(),
            order_id=order_id,
            position_id=position_id,
            symbol=self._spec.name,
            side=side,
            entry=entry,
            volume=volume,
            price=price,
            time=from_ms(time_ms),
            commission=commission,
            profit=profit,
            idempotency_key=key,
            reason=reason,
        )
        self._book.deals.append(deal)
        return deal

    def _open(
        self,
        *,
        order_id: int,
        key: str | None,
        magic: int,
        side: Side,
        volume: float,
        entry_type: OrderType,
        time_ms: int,
        price: float,
        bid: float,
        ask: float,
        mode: DataMode,
        sl: float,
        tp: float | None,
        cursor_ms: int,
        cursor_index: int | None,
    ) -> _Position:
        price = round(price, self._spec.digits)
        commission = self._commission(volume)
        self._balance = round(self._balance + commission, MONEY_DIGITS)
        mark = bid if side is Side.BUY else ask
        pos = _Position(
            position_id=self._new_id(),
            key=key,
            magic=magic,
            side=side,
            volume=volume,
            entry_type=entry_type,
            open_ms=time_ms,
            open_price=price,
            entry_bid=bid,
            entry_ask=ask,
            entry_mode=mode,
            sl=sl,
            tp=tp,
            cursor_ms=cursor_ms,
            cursor_index=cursor_index,
            best=mark,
            worst=mark,
            commission=commission,
        )
        self._book.positions[pos.position_id] = pos
        self._deal(
            order_id=order_id,
            position_id=pos.position_id,
            side=side,
            entry=DealEntry.IN,
            volume=volume,
            price=price,
            time_ms=time_ms,
            commission=commission,
            profit=0.0,
            key=key,
            reason=DealReason.EXPERT,
        )
        return pos

    def _close(self, pos: _Position, exit_: _Exit) -> DealRecord:
        price = round(exit_.price, self._spec.digits)
        profit = round(self._pnl(pos.side, pos.open_price, price, pos.volume), MONEY_DIGITS)
        commission = self._commission(pos.volume)
        self._balance = round(self._balance + profit + commission, MONEY_DIGITS)
        del self._book.positions[pos.position_id]
        deal = self._deal(
            order_id=None,
            position_id=pos.position_id,
            side=pos.side.opposite,
            entry=DealEntry.OUT,
            volume=pos.volume,
            price=price,
            time_ms=exit_.time_ms,
            commission=commission,
            profit=profit,
            key=pos.key,
            reason=exit_.reason,
        )
        intrabar = exit_.intrabar if exit_.intrabar is not IntrabarRule.NONE else pos.intrabar
        self._book.trades.append(
            SimTrade(
                position_id=pos.position_id,
                idempotency_key=pos.key,
                magic=pos.magic,
                side=pos.side,
                volume=pos.volume,
                entry_type=pos.entry_type,
                entry_time=from_ms(pos.open_ms),
                entry_price=pos.open_price,
                entry_bid=pos.entry_bid,
                entry_ask=pos.entry_ask,
                entry_mode=pos.entry_mode,
                exit_time=from_ms(exit_.time_ms),
                exit_price=price,
                exit_bid=exit_.bid,
                exit_ask=exit_.ask,
                exit_mode=exit_.mode,
                exit_reason=exit_.reason,
                stop_loss=pos.sl,
                take_profit=pos.tp,
                best=exit_.best,
                worst=exit_.worst,
                commission=pos.commission + commission,
                profit=profit,
                intrabar=intrabar,
            )
        )
        return deal

    @staticmethod
    def _extremes(side: Side, best: float, worst: float, *prices: float) -> tuple[float, float]:
        if not prices:
            return best, worst
        hi, lo = max(prices), min(prices)
        if side is Side.BUY:
            return max(best, hi), min(worst, lo)
        return min(best, lo), max(worst, hi)

    # ------------------------------------------------------------------ the price path
    def _advance(self, until_ms: int) -> None:
        if until_ms <= self._processed_ms:
            return
        for order in list(self._book.orders.values()):
            self._scan_order(order, until_ms)
        for pos in list(self._book.positions.values()):
            self._scan_position(pos, until_ms)
        self._processed_ms = until_ms

    def _scan_order(self, order: _Order, until_ms: int) -> None:
        for seg in self._market.segments(order.cursor_ms, until_ms, order.cursor_index):
            if isinstance(seg, TickSegment):
                k = _first_trigger_ticks(order.side, order.order_type, order.price, seg)
                if k is not None:
                    self._fill_pending_tick(order, seg, k, until_ms)
                    return
            else:
                k = _first_trigger_bars(order.side, order.order_type, order.price, seg)
                if k is not None:
                    self._fill_pending_bar(order, seg, k, until_ms)
                    return
        order.cursor_ms, order.cursor_index = max(order.cursor_ms, until_ms), None

    def _fill_pending_tick(self, order: _Order, seg: TickSegment, k: int, until_ms: int) -> None:
        bid, ask = float(seg.bid[k]), float(seg.ask[k])
        if order.order_type is OrderType.STOP:
            ref_bid, ref_ask = bid, ask
            fill = (ask + self._slip) if order.side is Side.BUY else (bid - self._slip)
        else:  # LIMIT: at the limit price, reference quote at that level
            spread = ask - bid
            ref_bid, ref_ask = (
                (order.price - spread, order.price)
                if order.side is Side.BUY
                else (order.price, order.price + spread)
            )
            fill = order.price
        del self._book.orders[order.order_id]
        pos = self._open(
            order_id=order.order_id,
            key=order.key,
            magic=order.magic,
            side=order.side,
            volume=order.volume,
            entry_type=order.order_type,
            time_ms=int(seg.time[k]),
            price=fill,
            bid=ref_bid,
            ask=ref_ask,
            mode=DataMode.TICKS,
            sl=order.sl,
            tp=order.tp,
            cursor_ms=int(seg.time[k]),
            cursor_index=seg.start + k,
        )
        self._scan_position(pos, until_ms)

    def _fill_pending_bar(self, order: _Order, seg: BarSegment, k: int, until_ms: int) -> None:
        side, p = order.side, order.price
        spread = float(seg.spread[k])
        o_bid = float(seg.open[k])
        o_ask = o_bid + spread
        at_open = _pending_at_open(side, order.order_type, p, o_bid, o_ask)
        if order.order_type is OrderType.STOP:
            base = (max(p, o_ask) if side is Side.BUY else min(p, o_bid)) if at_open else p
            fill = base + side.sign * self._slip
        else:
            base = fill = p
        ref_bid, ref_ask = (base - spread, base) if side is Side.BUY else (base, base + spread)
        open_ms = int(seg.open_time[k])
        del self._book.orders[order.order_id]
        pos = self._open(
            order_id=order.order_id,
            key=order.key,
            magic=order.magic,
            side=side,
            volume=order.volume,
            entry_type=order.order_type,
            time_ms=open_ms,
            price=fill,
            bid=ref_bid,
            ask=ref_ask,
            mode=DataMode.BARS,
            sl=order.sl,
            tp=order.tp,
            cursor_ms=open_ms,
            cursor_index=None,
        )
        if not at_open:
            exit_ = self._entry_bar_exit(pos, seg, k)
            if exit_ is not None:
                self._close(pos, exit_)
                return
            pos.cursor_ms = int(seg.close_time[k])
            pos.best, pos.worst = self._extremes(
                side, pos.best, pos.worst, *self._bar_marks(side, seg, k)
            )
        self._scan_position(pos, until_ms)

    def _entry_bar_exit(self, pos: _Position, seg: BarSegment, k: int) -> _Exit | None:
        """Exit inside the bar of an intra-bar pending fill (BARS mode), see module docs."""
        side = pos.side
        spread = float(seg.spread[k])
        lo, hi = float(seg.low[k]), float(seg.high[k])
        close = float(seg.close[k])
        if side is Side.SELL:
            lo, hi, close = lo + spread, hi + spread, close + spread
        adverse = lo if side is Side.BUY else hi
        favourable = hi if side is Side.BUY else lo
        sl_touch = (adverse - pos.sl) * side.sign <= 0
        tp_touch = pos.tp is not None and (favourable - pos.tp) * side.sign >= 0
        if pos.entry_type is OrderType.STOP:
            sl_flag = IntrabarRule.ENTRY_BAR  # the stop may have been reached before the entry
            tp_certain = tp_touch
        else:
            sl_flag = IntrabarRule.NONE  # a limit fill precedes any further adverse move
            tp_certain = tp_touch and pos.tp is not None and (close - pos.tp) * side.sign >= 0
            if tp_touch and not tp_certain:
                pos.intrabar = IntrabarRule.ENTRY_BAR  # target may have come before the entry
        if not sl_touch and not tp_certain:
            return None
        t = int(seg.close_time[k]) - 1
        if sl_touch:
            rule = IntrabarRule.SL_TP_SAME_BAR if tp_certain else sl_flag
            return self._bar_exit(pos, pos.sl, spread, t, DealReason.STOP_LOSS, rule, stop=True)
        assert pos.tp is not None
        return self._bar_exit(pos, pos.tp, spread, t, DealReason.TAKE_PROFIT, IntrabarRule.NONE)

    def _bar_exit(
        self,
        pos: _Position,
        level: float,
        spread: float,
        time_ms: int,
        reason: DealReason,
        rule: IntrabarRule,
        *,
        stop: bool = False,
    ) -> _Exit:
        """Exit at ``level`` (an exit-side price) on a bar: stops get slippage."""
        bid, ask = (level, level + spread) if pos.side is Side.BUY else (level - spread, level)
        price = level - pos.side.sign * self._slip if stop else level
        best, worst = self._extremes(pos.side, pos.best, pos.worst, level)
        return _Exit(time_ms, price, bid, ask, DataMode.BARS, reason, best, worst, rule)

    @staticmethod
    def _bar_marks(side: Side, seg: BarSegment, k: int) -> tuple[float, float]:
        """Exit-side price range of bar ``k`` (bid for BUY, ask for SELL)."""
        add = 0.0 if side is Side.BUY else float(seg.spread[k])
        return float(seg.low[k]) + add, float(seg.high[k]) + add

    def _scan_position(self, pos: _Position, until_ms: int) -> None:
        for seg in self._market.segments(pos.cursor_ms, until_ms, pos.cursor_index):
            exit_ = (
                self._tick_exit(pos, seg)
                if isinstance(seg, TickSegment)
                else self._bars_exit(pos, seg)
            )
            if exit_ is not None:
                self._close(pos, exit_)
                return
        pos.cursor_ms, pos.cursor_index = max(pos.cursor_ms, until_ms), None

    def _tick_exit(self, pos: _Position, seg: TickSegment) -> _Exit | None:
        side = pos.side
        marks = seg.bid if side is Side.BUY else seg.ask
        hit = (marks - pos.sl) * side.sign <= 0
        if pos.tp is not None:
            hit |= (marks - pos.tp) * side.sign >= 0
        if not hit.any():
            pos.best, pos.worst = self._extremes(
                side, pos.best, pos.worst, float(marks.max()), float(marks.min())
            )
            return None
        k = int(np.argmax(hit))
        best, worst = self._extremes(
            side, pos.best, pos.worst, float(marks[: k + 1].max()), float(marks[: k + 1].min())
        )
        bid, ask, mark = float(seg.bid[k]), float(seg.ask[k]), float(marks[k])
        t = int(seg.time[k])
        if (mark - pos.sl) * side.sign <= 0:
            price = mark - side.sign * self._slip
            return _Exit(t, price, bid, ask, DataMode.TICKS, DealReason.STOP_LOSS, best, worst)
        assert pos.tp is not None
        spread = ask - bid
        ref = (pos.tp, pos.tp + spread) if side is Side.BUY else (pos.tp - spread, pos.tp)
        return _Exit(t, pos.tp, *ref, DataMode.TICKS, DealReason.TAKE_PROFIT, best, worst)

    def _bars_exit(self, pos: _Position, seg: BarSegment) -> _Exit | None:
        side = pos.side
        add = np.zeros_like(seg.spread) if side is Side.BUY else seg.spread
        lo, hi, op = seg.low + add, seg.high + add, seg.open + add
        adverse, favourable = (lo, hi) if side is Side.BUY else (hi, lo)
        sl_hit = (adverse - pos.sl) * side.sign <= 0
        tp_hit = (
            (favourable - pos.tp) * side.sign >= 0 if pos.tp is not None else np.zeros_like(sl_hit)
        )
        hit = sl_hit | tp_hit
        if not hit.any():
            pos.best, pos.worst = self._extremes(
                side, pos.best, pos.worst, float(hi.max()), float(lo.min())
            )
            return None
        k = int(np.argmax(hit))
        if k:
            pos.best, pos.worst = self._extremes(
                side, pos.best, pos.worst, float(hi[:k].max()), float(lo[:k].min())
            )
        spread = float(seg.spread[k])
        o = float(op[k])
        if (o - pos.sl) * side.sign <= 0:  # opened through the stop: gap, filled at the open
            return self._bar_exit(
                pos,
                o,
                spread,
                int(seg.open_time[k]),
                DealReason.STOP_LOSS,
                IntrabarRule.NONE,
                stop=True,
            )
        if pos.tp is not None and (o - pos.tp) * side.sign >= 0:  # opened through the target
            return self._bar_exit(
                pos,
                pos.tp,
                spread,
                int(seg.open_time[k]),
                DealReason.TAKE_PROFIT,
                IntrabarRule.NONE,
            )
        t = int(seg.close_time[k]) - 1
        if sl_hit[k]:
            rule = IntrabarRule.SL_TP_SAME_BAR if tp_hit[k] else IntrabarRule.NONE
            return self._bar_exit(pos, pos.sl, spread, t, DealReason.STOP_LOSS, rule, stop=True)
        assert pos.tp is not None
        return self._bar_exit(pos, pos.tp, spread, t, DealReason.TAKE_PROFIT, IntrabarRule.NONE)


def _first_trigger_ticks(side: Side, kind: OrderType, price: float, seg: TickSegment) -> int | None:
    """First tick at which a pending order triggers (BUY uses the ask, SELL the bid)."""
    marks = seg.ask if side is Side.BUY else seg.bid
    # STOP: price moves through the level in the order's direction; LIMIT: against it.
    direction = side.sign if kind is OrderType.STOP else -side.sign
    hit = (marks - price) * direction >= 0
    return int(np.argmax(hit)) if hit.any() else None


def _first_trigger_bars(side: Side, kind: OrderType, price: float, seg: BarSegment) -> int | None:
    add = seg.spread if side is Side.BUY else np.zeros_like(seg.spread)
    direction = side.sign if kind is OrderType.STOP else -side.sign
    reach = (seg.high + add) if direction > 0 else (seg.low + add)
    hit = (reach - price) * direction >= 0
    return int(np.argmax(hit)) if hit.any() else None


def _pending_at_open(side: Side, kind: OrderType, price: float, o_bid: float, o_ask: float) -> bool:
    mark = o_ask if side is Side.BUY else o_bid
    direction = side.sign if kind is OrderType.STOP else -side.sign
    return (mark - price) * direction >= 0
