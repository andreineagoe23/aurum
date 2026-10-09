"""MT5Broker: BrokerPort over a locally running MetaTrader 5 terminal (Windows VM).

Rules this adapter keeps:

* **Time.** The MetaTrader5 API speaks server wall-clock time (Fusion: New York + 7h). Every
  timestamp is converted to UTC here, and every query bound from UTC, via ``ServerTimeZone``
  (ADR-0004). Nothing outside this package ever sees a server timestamp.
* **Writes.** Every write (send/modify/close/cancel) first re-reads ``account_info()`` from the
  terminal (never cached) and runs ``aurum.core.safety.assert_order_permitted``.
* **Idempotency.** The intent's idempotency key is the MT5 order comment, which Fusion keeps on
  orders, positions and deals (ADR-0005). Before sending, open positions, pending orders and
  order history are searched for the key; a hit returns ``duplicate=True`` and sends nothing.
  If the terminal's reply is lost, the same search decides what happened.
* **Stops.** Market orders carry SL/TP on the request itself. If a fill ever shows no stop at the
  broker, the position is closed at once (MASTER_PROMPT §4 rule 3).

The MetaTrader5 module is loaded lazily, or injected (unit tests use a fake module).
"""

import time
from collections.abc import Callable
from datetime import datetime, timedelta
from types import ModuleType
from typing import Any

import polars as pl
import structlog

from aurum.adapters.mt5.frames import rates_to_frame, ticks_to_frame
from aurum.adapters.mt5.loader import load_mt5
from aurum.adapters.mt5.servertime import (
    OffsetCheck,
    OffsetMeasurement,
    ServerTimeZone,
    check_offset,
)
from aurum.core.clock import SystemClock
from aurum.core.enums import (
    AccountTradeMode,
    DealEntry,
    DealReason,
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
)
from aurum.core.safety import assert_order_permitted
from aurum.core.timeframes import Timeframe
from aurum.core.timeutil import to_utc
from aurum.ports.clock import ClockPort
from aurum.settings import AUTO, Settings

log = structlog.get_logger(__name__)

# SYMBOL_FILLING_* flags in symbol_info().filling_mode (not exported by the Python package).
_SYMBOL_FILLING_FOK = 1
_SYMBOL_FILLING_IOC = 2
_IDEMPOTENCY_LOOKBACK = timedelta(hours=1)
"""Order history is searched from ``intent.created_at`` minus this for an existing key."""


class MT5Broker:
    def __init__(
        self,
        settings: Settings,
        *,
        mt5_module: ModuleType | Any | None = None,
        clock: ClockPort | None = None,
        connect_attempts: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._broker_cfg = settings.config.broker
        self._mt5_module = mt5_module
        self._clock = clock or SystemClock()
        self._connect_attempts = max(1, connect_attempts)
        self._backoff = backoff_seconds
        self._sleep = sleep
        st = self._broker_cfg.server_time
        self.server_tz = ServerTimeZone(timezone=st.timezone, shift_hours=st.shift_hours)
        self.server_time_check: OffsetMeasurement | None = None
        self._connected = False
        self._selected: set[str] = set()

    @property
    def _mt5(self) -> Any:
        if self._mt5_module is None:
            self._mt5_module = load_mt5()
        return self._mt5_module

    # ------------------------------------------------------------------ connection
    def connect(self) -> None:
        """Attach to the running terminal (retrying with exponential backoff), then verify the
        account currency expectation and the server-time rule against a live quote."""
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
        last_error: object = None
        for attempt in range(1, self._connect_attempts + 1):
            # Never log kwargs: it contains the password.
            log.info("mt5.connect", attempt=attempt, login=env.mt5_login, server=env.mt5_server)
            if self._mt5.initialize(**kwargs):
                break
            last_error = self._mt5.last_error()
            log.warning("mt5.connect_failed", attempt=attempt, error=str(last_error))
            self._mt5.shutdown()
            if attempt < self._connect_attempts:
                self._sleep(self._backoff * 2 ** (attempt - 1))
        else:
            raise BrokerError(
                f"MT5 initialize failed after {self._connect_attempts} attempts: {last_error}"
            )
        self._connected = True
        self._selected.clear()
        try:
            self._check_account_currency()
            self.server_time_check = self.verify_server_time(self.resolve_symbol())
        except Exception:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        if self._mt5_module is not None:
            self._mt5.shutdown()
        self._connected = False
        log.info("mt5.disconnect")

    def reconnect(self) -> None:
        self.disconnect()
        self.connect()

    def is_connected(self) -> bool:
        if not self._connected:
            return False
        info = self._mt5.terminal_info()
        return bool(info is not None and info.connected)

    def _check_account_currency(self) -> None:
        expected = self._broker_cfg.account_currency
        actual = self.account_info().currency
        if expected != AUTO and actual != expected:
            raise BrokerError(
                f"account currency is {actual} but configs/broker.yaml expects {expected}"
            )

    def verify_server_time(self, symbol: str) -> OffsetMeasurement:
        """Check the configured server-clock rule against the newest quote (ADR-0004).

        Raises ``BrokerError`` on MISMATCH: no timestamp from this server can be trusted then.
        """
        self._select(symbol)
        raw = self._mt5.symbol_info_tick(symbol)
        if raw is None:
            raise NoMarketDataError(f"no quote for {symbol}: {self._mt5.last_error()}")
        m = check_offset(self.server_tz, raw.time_msc / 1000, self._clock.now())
        log.info(
            "mt5.server_time",
            symbol=symbol,
            status=m.status,
            expected_offset_s=m.expected.total_seconds(),
            measured_offset_s=round(m.measured.total_seconds(), 1),
        )
        if m.status is OffsetCheck.MISMATCH:
            raise BrokerError(
                f"MT5 server clock is {m.measured} ahead of UTC but configs/broker.yaml "
                f"server_time implies {m.expected}; fix the rule (ADR-0004) before using data"
            )
        return m

    def _require(self, value: Any, what: str) -> Any:
        """Turn a ``None`` from the MetaTrader5 API into the right exception."""
        if value is not None:
            return value
        error = self._mt5.last_error()
        info = self._mt5.terminal_info()
        if info is None or not info.connected:
            raise BrokerDisconnectedError(f"MT5 {what} failed (terminal disconnected): {error}")
        raise BrokerError(f"MT5 {what} failed: {error}")

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

    # ------------------------------------------------------------------ instrument
    def find_symbols(self, term: str) -> list[str]:
        """Names of symbols whose name, path or description contains ``term`` (any case)."""
        needle = term.casefold()
        symbols = self._require(self._mt5.symbols_get(), "symbols_get")
        return sorted(
            s.name
            for s in symbols
            if needle in s.name.casefold()
            or needle in s.path.casefold()
            or needle in s.description.casefold()
        )

    def resolve_symbol(self) -> str:
        """The first of ``broker.symbol_candidates`` this terminal knows (never hard-coded)."""
        for candidate in self._broker_cfg.symbol_candidates:
            if self._mt5.symbol_info(candidate) is not None:
                return candidate
        raise BrokerError(
            f"none of {self._broker_cfg.symbol_candidates} exist on this server; "
            "run `aurum mt5 symbols --find gold` and update configs/broker.yaml"
        )

    def _select(self, symbol: str) -> None:
        """Make sure ``symbol`` is in Market Watch (required for ticks and trading)."""
        if symbol in self._selected:
            return
        if not self._mt5.symbol_select(symbol, True):
            raise BrokerError(f"cannot select {symbol}: {self._mt5.last_error()}")
        self._selected.add(symbol)

    def symbol_info(self, symbol: str) -> SymbolSpec:
        self._select(symbol)
        s = self._require(self._mt5.symbol_info(symbol), f"symbol_info({symbol})")
        return SymbolSpec(
            name=s.name,
            digits=s.digits,
            point=s.point,
            tick_size=s.trade_tick_size,
            tick_value=s.trade_tick_value,
            contract_size=s.trade_contract_size,
            volume_min=s.volume_min,
            volume_max=s.volume_max,
            volume_step=s.volume_step,
            stops_level_points=s.trade_stops_level,
            freeze_level_points=s.trade_freeze_level,
            currency_profit=s.currency_profit,
            currency_margin=s.currency_margin,
            captured_at=self._clock.now(),
        )

    def symbol_snapshot(self, symbol: str) -> dict[str, Any]:
        """Every field of MT5 ``symbol_info`` as plain data, for ``data/specs/`` snapshots."""
        self._select(symbol)
        s = self._require(self._mt5.symbol_info(symbol), f"symbol_info({symbol})")
        return dict(s._asdict())

    # ------------------------------------------------------------------ market data
    def _utc(self, server_seconds: float) -> datetime:
        return self.server_tz.to_utc(server_seconds)

    def _server_bound(self, utc: datetime) -> int:
        return int(self.server_tz.to_server_seconds(utc))

    def latest_tick(self, symbol: str) -> Tick:
        self._select(symbol)
        raw = self._mt5.symbol_info_tick(symbol)
        if raw is None:
            self._require(None, f"symbol_info_tick({symbol})")
        return Tick(
            symbol=symbol,
            time=self._utc(raw.time_msc / 1000),
            bid=raw.bid,
            ask=raw.ask,
            last=raw.last or None,
            volume=raw.volume_real or raw.volume,
        )

    def bars(self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime) -> list[Bar]:
        """Completed bars with ``start <= open_time < end`` (UTC). MT5 bars are bid prices."""
        start, end = to_utc(start), to_utc(end)
        self._select(symbol)
        tf = getattr(self._mt5, timeframe.mt5_constant_name)
        rates = self._mt5.copy_rates_range(
            symbol, tf, self._server_bound(start), self._server_bound(end)
        )
        rates = self._require(rates, f"copy_rates_range({symbol}, {timeframe})")
        now = self._clock.now()
        bars = []
        for r in rates:
            bar = Bar(
                symbol=symbol,
                timeframe=timeframe,
                open_time=self._utc(int(r["time"])),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                tick_volume=int(r["tick_volume"]),
                spread_points=int(r["spread"]),
                real_volume=float(r["real_volume"]),
            )
            if start <= bar.open_time < end and bar.close_time <= now:
                bars.append(bar)
        return bars

    def ticks(self, symbol: str, start: datetime, end: datetime) -> list[Tick]:
        """Ticks with ``start <= time < end`` (UTC, millisecond precision), oldest first.

        Rows without a positive bid and ask cannot be a ``Tick`` and are skipped (counted in the
        log). Phase 2 stores raw rows separately, so nothing is lost for data QA.
        """
        start, end = to_utc(start), to_utc(end)
        self._select(symbol)
        raw = self._mt5.copy_ticks_range(
            symbol,
            self._server_bound(start),
            self._server_bound(end) + 1,
            self._mt5.COPY_TICKS_ALL,
        )
        raw = self._require(raw, f"copy_ticks_range({symbol})")
        now = self._clock.now()
        ticks, skipped = [], 0
        for r in raw:
            if r["bid"] <= 0 or r["ask"] <= 0:
                skipped += 1
                continue
            t = self._utc(int(r["time_msc"]) / 1000)
            if start <= t < end and t <= now:
                ticks.append(
                    Tick(
                        symbol=symbol,
                        time=t,
                        bid=float(r["bid"]),
                        ask=float(r["ask"]),
                        last=float(r["last"]) or None,
                        volume=float(r["volume_real"]) or float(r["volume"]),
                    )
                )
        if skipped:
            log.warning("mt5.ticks_skipped", symbol=symbol, skipped=skipped, kept=len(ticks))
        return ticks

    def tick_frame(self, symbol: str, start: datetime, end: datetime) -> pl.DataFrame:
        """Raw ticks with ``start <= time < end`` (UTC, ms) as a ``RAW_TICKS`` frame: every row
        the terminal returns (including invalid ones), in its order. For storage and QA."""
        start, end = to_utc(start), to_utc(end)
        self._select(symbol)
        raw = self._mt5.copy_ticks_range(
            symbol,
            self._server_bound(start),
            self._server_bound(end) + 1,
            self._mt5.COPY_TICKS_ALL,
        )
        frame = ticks_to_frame(self._require(raw, f"copy_ticks_range({symbol})"), self.server_tz)
        return frame.filter((pl.col("time") >= start) & (pl.col("time") < end))

    def rate_frame(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> pl.DataFrame:
        """Raw completed bars with ``start <= open_time < end`` (UTC) as a ``RAW_BARS`` frame."""
        start, end = to_utc(start), to_utc(end)
        self._select(symbol)
        tf = getattr(self._mt5, timeframe.mt5_constant_name)
        rates = self._mt5.copy_rates_range(
            symbol, tf, self._server_bound(start), self._server_bound(end)
        )
        frame = rates_to_frame(
            self._require(rates, f"copy_rates_range({symbol}, {timeframe})"), self.server_tz
        )
        last_open = self._clock.now() - timeframe.duration  # close_time <= now
        return frame.filter(
            (pl.col("open_time") >= start)
            & (pl.col("open_time") < end)
            & (pl.col("open_time") <= last_open)
        )

    # ------------------------------------------------------------------ state
    @staticmethod
    def _key(comment: str) -> str | None:
        return comment if comment.startswith("AU") else None

    def _position(self, p: Any) -> PositionState:
        return PositionState(
            position_id=p.ticket,
            symbol=p.symbol,
            side=Side.BUY if p.type == self._mt5.POSITION_TYPE_BUY else Side.SELL,
            volume=p.volume,
            open_price=p.price_open,
            open_time=self._utc(p.time_msc / 1000),
            stop_loss=p.sl or None,
            take_profit=p.tp or None,
            idempotency_key=self._key(p.comment),
            magic=p.magic,
            unrealized_pnl=p.profit,
        )

    def positions(self, symbol: str | None = None) -> list[PositionState]:
        raw = self._mt5.positions_get(symbol=symbol) if symbol else self._mt5.positions_get()
        return [self._position(p) for p in self._require(raw, "positions_get")]

    def _order_kind(self, raw_type: int) -> tuple[Side, OrderType] | None:
        m = self._mt5
        return {
            m.ORDER_TYPE_BUY: (Side.BUY, OrderType.MARKET),
            m.ORDER_TYPE_SELL: (Side.SELL, OrderType.MARKET),
            m.ORDER_TYPE_BUY_LIMIT: (Side.BUY, OrderType.LIMIT),
            m.ORDER_TYPE_SELL_LIMIT: (Side.SELL, OrderType.LIMIT),
            m.ORDER_TYPE_BUY_STOP: (Side.BUY, OrderType.STOP),
            m.ORDER_TYPE_SELL_STOP: (Side.SELL, OrderType.STOP),
        }.get(raw_type)

    def orders(self, symbol: str | None = None) -> list[OrderState]:
        raw = self._mt5.orders_get(symbol=symbol) if symbol else self._mt5.orders_get()
        out = []
        for o in self._require(raw, "orders_get"):
            kind = self._order_kind(o.type)
            if kind is None:  # stop-limit / close-by: never placed by Aurum
                log.warning("mt5.order_type_unmapped", order=o.ticket, type=o.type)
                continue
            out.append(
                OrderState(
                    order_id=o.ticket,
                    symbol=o.symbol,
                    side=kind[0],
                    order_type=kind[1],
                    volume=o.volume_current,
                    price=o.price_open,
                    stop_loss=o.sl or None,
                    take_profit=o.tp or None,
                    created_at=self._utc(o.time_setup_msc / 1000),
                    idempotency_key=self._key(o.comment),
                    magic=o.magic,
                )
            )
        return out

    def _deal(self, d: Any) -> DealRecord | None:
        m = self._mt5
        if d.type not in (m.DEAL_TYPE_BUY, m.DEAL_TYPE_SELL):
            return None  # balance, credit, charges...: not fills
        return DealRecord(
            deal_id=d.ticket,
            order_id=d.order or None,
            position_id=d.position_id,
            symbol=d.symbol,
            side=Side.BUY if d.type == m.DEAL_TYPE_BUY else Side.SELL,
            entry=DealEntry.IN if d.entry == m.DEAL_ENTRY_IN else DealEntry.OUT,
            volume=d.volume,
            price=d.price,
            time=self._utc(d.time_msc / 1000),
            commission=d.commission + d.fee,
            profit=d.profit + d.swap,
            idempotency_key=self._key(d.comment),
            reason=self._deal_reason(d.reason),
            magic=d.magic,
        )

    def _deal_reason(self, code: int) -> DealReason:
        m = self._mt5
        by_code = {
            m.DEAL_REASON_EXPERT: DealReason.EXPERT,
            m.DEAL_REASON_CLIENT: DealReason.MANUAL,
            m.DEAL_REASON_MOBILE: DealReason.MANUAL,
            m.DEAL_REASON_WEB: DealReason.MANUAL,
            m.DEAL_REASON_SL: DealReason.STOP_LOSS,
            m.DEAL_REASON_TP: DealReason.TAKE_PROFIT,
            m.DEAL_REASON_SO: DealReason.STOP_OUT,
        }
        return by_code.get(code, DealReason.OTHER)

    def history_deals(self, start: datetime, end: datetime) -> list[DealRecord]:
        """Fills with ``start <= time < end`` (UTC). Non-trade deals (balance etc.) are omitted."""
        start, end = to_utc(start), to_utc(end)
        raw = self._mt5.history_deals_get(self._server_bound(start), self._server_bound(end) + 1)
        deals = (self._deal(d) for d in self._require(raw, "history_deals_get"))
        return [d for d in deals if d is not None and start <= d.time < end]

    def position_deals(self, position_id: int) -> list[DealRecord]:
        """Every fill of one position (open and close), oldest first."""
        raw = self._require(self._mt5.history_deals_get(position=position_id), "history_deals")
        deals = [d for d in (self._deal(x) for x in raw) if d is not None]
        return sorted(deals, key=lambda d: (d.time, d.deal_id))

    # ------------------------------------------------------------------ idempotency
    def find_by_key(self, key: str, since: datetime) -> ExecutionReport | None:
        """What the broker already holds for ``key``: an open position, a pending order, or an
        opening order in history since ``since`` (filled, cancelled or expired). None if nothing.

        Closing orders reuse the position's comment, so history orders count only when they
        opened something: MT5 gives a position the ticket of the order that opened it.
        """
        now = self._clock.now()
        for p in self._require(self._mt5.positions_get(), "positions_get"):
            if p.comment == key:
                return self._found(key, now, order_id=p.ticket, position_id=p.ticket)
        for o in self._require(self._mt5.orders_get(), "orders_get"):
            if o.comment == key:
                return self._found(key, now, order_id=o.ticket, status=ExecutionStatus.PLACED)
        history = self._mt5.history_orders_get(
            self._server_bound(since), self._server_bound(now + timedelta(minutes=1))
        )
        for o in self._require(history, "history_orders_get"):
            if o.comment == key and o.position_id in (0, o.ticket):
                return self._found(key, now, order_id=o.ticket, position_id=o.position_id or None)
        return None

    @staticmethod
    def _found(
        key: str,
        now: datetime,
        *,
        order_id: int,
        position_id: int | None = None,
        status: ExecutionStatus = ExecutionStatus.FILLED,
    ) -> ExecutionReport:
        return ExecutionReport(
            status=status,
            time=now,
            idempotency_key=key,
            order_id=order_id,
            position_id=position_id,
            message="already at broker",
        )

    # ------------------------------------------------------------------ requests
    def _filling(self, symbol: str) -> int:
        s = self._require(self._mt5.symbol_info(symbol), f"symbol_info({symbol})")
        if s.filling_mode & _SYMBOL_FILLING_IOC:
            return int(self._mt5.ORDER_FILLING_IOC)
        if s.filling_mode & _SYMBOL_FILLING_FOK:
            return int(self._mt5.ORDER_FILLING_FOK)
        return int(self._mt5.ORDER_FILLING_RETURN)

    def _order_request(self, intent: OrderIntent, spec: SymbolSpec) -> dict[str, Any]:
        m = self._mt5
        req: dict[str, Any] = {
            "symbol": intent.symbol,
            "volume": intent.volume,
            "sl": spec.round_price(intent.stop_loss),
            "tp": spec.round_price(intent.take_profit) if intent.take_profit else 0.0,
            "magic": self._broker_cfg.magic_number,
            "comment": intent.idempotency_key,
            "type_time": m.ORDER_TIME_GTC,
        }
        buy = intent.side is Side.BUY
        if intent.order_type is OrderType.MARKET:
            tick = self.latest_tick(intent.symbol)
            req.update(
                action=m.TRADE_ACTION_DEAL,
                type=m.ORDER_TYPE_BUY if buy else m.ORDER_TYPE_SELL,
                price=tick.price_for(intent.side),
                deviation=self._broker_cfg.max_deviation_points,
                type_filling=self._filling(intent.symbol),
            )
        else:
            assert intent.price is not None  # guaranteed by OrderIntent validation
            limit = intent.order_type is OrderType.LIMIT
            req.update(
                action=m.TRADE_ACTION_PENDING,
                type=(
                    (m.ORDER_TYPE_BUY_LIMIT if buy else m.ORDER_TYPE_SELL_LIMIT)
                    if limit
                    else (m.ORDER_TYPE_BUY_STOP if buy else m.ORDER_TYPE_SELL_STOP)
                ),
                price=spec.round_price(intent.price),
                type_filling=m.ORDER_FILLING_RETURN,
            )
        return req

    def _ok(self, retcode: int, *extra: int) -> bool:
        m = self._mt5
        return retcode in (m.TRADE_RETCODE_DONE, m.TRADE_RETCODE_DONE_PARTIAL, *extra)

    def _rejected(
        self, intent_key: str | None, retcode: int | None, message: str
    ) -> ExecutionReport:
        log.warning("mt5.rejected", key=intent_key, retcode=retcode, message=message)
        return ExecutionReport(
            status=ExecutionStatus.REJECTED,
            time=self._clock.now(),
            idempotency_key=intent_key,
            retcode=retcode,
            message=message,
        )

    def _fill(self, deal_ticket: int) -> DealRecord | None:
        if not deal_ticket:
            return None
        raw = self._mt5.history_deals_get(ticket=deal_ticket)
        deals = [d for d in (self._deal(x) for x in raw or ()) if d is not None]
        return deals[0] if deals else None

    # ------------------------------------------------------------------ trading (guarded)
    def check_order(self, intent: OrderIntent) -> OrderCheckResult:
        """Broker-side pre-trade check (``order_check``). Sends nothing."""
        spec = self.symbol_info(intent.symbol)
        if not spec.is_valid_volume(intent.volume):
            return OrderCheckResult(ok=False, reasons=(f"invalid volume {intent.volume}",))
        result = self._mt5.order_check(self._order_request(intent, spec))
        if result is None:
            return OrderCheckResult(ok=False, reasons=(str(self._mt5.last_error()),))
        ok = result.retcode in (0, self._mt5.TRADE_RETCODE_DONE)
        return OrderCheckResult(
            ok=ok,
            reasons=() if ok else (f"retcode {result.retcode}: {result.comment}",),
            margin_required=result.margin,
        )

    def send_order(self, intent: OrderIntent) -> ExecutionReport:
        if not intent.risk_decision.approved:
            raise SafetyViolationError("refusing order without an approved RiskDecision")
        self._ensure_write_permitted()
        key = intent.idempotency_key
        since = intent.created_at - _IDEMPOTENCY_LOOKBACK
        existing = self.find_by_key(key, since)
        if existing is not None:
            log.info("mt5.duplicate", key=key, order=existing.order_id)
            return existing.model_copy(update={"duplicate": True})
        age = self._clock.now() - intent.created_at
        if age.total_seconds() > self._broker_cfg.max_intent_age_seconds:
            return self._rejected(
                key,
                None,
                f"stale intent: {age.total_seconds():.1f} s old > "
                f"{self._broker_cfg.max_intent_age_seconds:.0f} s, not sent",
            )

        spec = self.symbol_info(intent.symbol)
        if not spec.is_valid_volume(intent.volume):
            return self._rejected(key, None, f"invalid volume {intent.volume} for {spec.name}")
        request = self._order_request(intent, spec)
        log.info(
            "mt5.send",
            key=key,
            symbol=intent.symbol,
            side=intent.side,
            type=intent.order_type,
            volume=intent.volume,
            price=request["price"],
            sl=request["sl"],
            tp=request["tp"],
        )
        result = self._mt5.order_send(request)
        if result is None:
            # Reply lost or request refused locally: the broker's state decides which.
            error = self._mt5.last_error()
            found = self.find_by_key(key, since)
            if found is not None:
                log.warning("mt5.send_reply_lost", key=key, order=found.order_id)
                return found
            return self._rejected(key, None, f"order_send failed: {error}")
        if not self._ok(result.retcode, self._mt5.TRADE_RETCODE_PLACED):
            return self._rejected(key, result.retcode, result.comment)

        if intent.order_type is not OrderType.MARKET:
            report = ExecutionReport(
                status=ExecutionStatus.PLACED,
                time=self._clock.now(),
                idempotency_key=key,
                order_id=result.order,
                requested_price=request["price"],
                volume=result.volume,
                retcode=result.retcode,
            )
            log.info("mt5.placed", key=key, order=result.order)
            return report

        deal = self._fill(result.deal)
        position_id = deal.position_id if deal else result.order
        report = ExecutionReport(
            status=ExecutionStatus.FILLED,
            time=self._clock.now(),
            idempotency_key=key,
            order_id=result.order,
            deal_id=result.deal or None,
            position_id=position_id,
            requested_price=request["price"],
            fill_price=deal.price if deal else result.price,
            volume=deal.volume if deal else result.volume,
            retcode=result.retcode,
            commission=deal.commission if deal else 0.0,
        )
        log.info(
            "mt5.filled",
            key=key,
            order=result.order,
            deal=result.deal,
            position=position_id,
            requested=request["price"],
            fill=report.fill_price,
            commission=report.commission,
        )
        self._enforce_stop(position_id)
        return report

    def _enforce_stop(self, position_id: int) -> None:
        """Reconcile after a fill: a position without a broker-side stop is closed at once."""
        raw = self._mt5.positions_get(ticket=position_id)
        if not raw:
            return  # already closed (e.g. stopped out instantly)
        if raw[0].sl:
            return
        log.critical("mt5.position_without_stop", position=position_id)
        self.close_position(position_id)
        raise SafetyViolationError(f"position {position_id} had no broker-side stop; closed it")

    def _open_position(self, position_id: int) -> Any:
        raw = self._require(self._mt5.positions_get(ticket=position_id), "positions_get")
        if not raw:
            raise BrokerError(f"no open position {position_id}")
        return raw[0]

    def modify_position(
        self, position_id: int, stop_loss: float, take_profit: float | None
    ) -> ExecutionReport:
        self._ensure_write_permitted()
        p = self._open_position(position_id)
        spec = self.symbol_info(p.symbol)
        request = {
            "action": self._mt5.TRADE_ACTION_SLTP,
            "symbol": p.symbol,
            "position": position_id,
            "sl": spec.round_price(stop_loss),
            "tp": spec.round_price(take_profit) if take_profit else 0.0,
            "magic": self._broker_cfg.magic_number,
        }
        log.info("mt5.modify", position=position_id, sl=request["sl"], tp=request["tp"])
        result = self._mt5.order_send(request)
        key = self._key(p.comment)
        if result is None:
            return self._rejected(key, None, f"modify failed: {self._mt5.last_error()}")
        if not self._ok(result.retcode):
            return self._rejected(key, result.retcode, result.comment)
        return ExecutionReport(
            status=ExecutionStatus.MODIFIED,
            time=self._clock.now(),
            idempotency_key=key,
            position_id=position_id,
            retcode=result.retcode,
        )

    def close_position(self, position_id: int, volume: float | None = None) -> ExecutionReport:
        self._ensure_write_permitted()
        p = self._open_position(position_id)
        qty = p.volume if volume is None else volume
        spec = self.symbol_info(p.symbol)
        if not (0 < qty <= p.volume + 1e-12 and spec.is_valid_volume(qty)):
            raise BrokerError(f"cannot close {qty} of {p.volume} lots")
        side = Side.BUY if p.type == self._mt5.POSITION_TYPE_BUY else Side.SELL
        tick = self.latest_tick(p.symbol)
        m = self._mt5
        request = {
            "action": m.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "position": position_id,
            "volume": qty,
            "type": m.ORDER_TYPE_SELL if side is Side.BUY else m.ORDER_TYPE_BUY,
            "price": tick.exit_price_for(side),
            "deviation": self._broker_cfg.max_deviation_points,
            "magic": self._broker_cfg.magic_number,
            "comment": p.comment,
            "type_time": m.ORDER_TIME_GTC,
            "type_filling": self._filling(p.symbol),
        }
        key = self._key(p.comment)
        log.info("mt5.close", position=position_id, volume=qty, price=request["price"])
        result = self._mt5.order_send(request)
        if result is None:
            return self._rejected(key, None, f"close failed: {self._mt5.last_error()}")
        if not self._ok(result.retcode):
            return self._rejected(key, result.retcode, result.comment)
        deal = self._fill(result.deal)
        report = ExecutionReport(
            status=ExecutionStatus.CLOSED,
            time=self._clock.now(),
            idempotency_key=key,
            order_id=result.order,
            deal_id=result.deal or None,
            position_id=position_id,
            requested_price=request["price"],
            fill_price=deal.price if deal else result.price,
            volume=deal.volume if deal else qty,
            retcode=result.retcode,
            commission=deal.commission if deal else 0.0,
        )
        log.info("mt5.closed", position=position_id, fill=report.fill_price, deal=result.deal)
        return report

    def cancel_order(self, order_id: int) -> ExecutionReport:
        self._ensure_write_permitted()
        request = {"action": self._mt5.TRADE_ACTION_REMOVE, "order": order_id}
        log.info("mt5.cancel", order=order_id)
        result = self._mt5.order_send(request)
        if result is None:
            return self._rejected(None, None, f"cancel failed: {self._mt5.last_error()}")
        if not self._ok(result.retcode):
            return self._rejected(None, result.retcode, result.comment)
        return ExecutionReport(
            status=ExecutionStatus.CANCELLED,
            time=self._clock.now(),
            order_id=order_id,
            retcode=result.retcode,
        )
