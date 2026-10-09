"""A small stateful stand-in for the ``MetaTrader5`` package (runs on any OS).

Behaves like the real terminal where Aurum depends on it (measured on Fusion Markets demo in
Phase 1): timestamps and query bounds are server wall clock (New York + 7h), comments longer
than 29 chars are refused with ``None`` + ``(-2, ...)``, a position's ticket equals the ticket of
the order that opened it, closing orders get their own ticket, and the comment is kept on the
order, position and deals.

Knobs for fault tests: ``initialize_results``, ``lose_next_reply`` (execute but return None),
``reject_next`` (retcode), ``drop_stops`` (fill without SL), ``quote_age``, ``connected``.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import numpy as np

from aurum.adapters.mt5.frames import RATE_DTYPE, TICK_DTYPE
from aurum.adapters.mt5.servertime import ServerTimeZone

# Constant values copied from MetaTrader5 5.0.6231.
ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2
TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_H1 = 1, 5, 15, 16385
COPY_TICKS_ALL = -1
TRADE_ACTION_DEAL, TRADE_ACTION_PENDING, TRADE_ACTION_SLTP = 1, 5, 6
TRADE_ACTION_MODIFY, TRADE_ACTION_REMOVE = 7, 8
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT = 2, 3
ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP = 4, 5
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
ORDER_TIME_GTC = 0
ORDER_STATE_PLACED, ORDER_STATE_CANCELED, ORDER_STATE_FILLED = 1, 2, 4
POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
DEAL_TYPE_BUY, DEAL_TYPE_SELL, DEAL_TYPE_BALANCE = 0, 1, 2
DEAL_ENTRY_IN, DEAL_ENTRY_OUT = 0, 1
TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL = 10008, 10009, 10010
TRADE_RETCODE_REQUOTE, TRADE_RETCODE_INVALID_STOPS = 10004, 10016

MAX_COMMENT = 29
TICK_DEFAULTS = {"last": 0.0, "volume": 0, "flags": 6, "volume_real": 0.0}


def _structured(rows: list[dict[str, Any]], dtype: np.dtype) -> np.ndarray:
    """Rows (dicts) as the numpy structured array the real terminal returns. Tick rows may omit
    ``time`` (derived from ``time_msc``) and the always-zero CFD fields."""
    out = np.zeros(len(rows), dtype=dtype)
    for i, row in enumerate(rows):
        full = {**TICK_DEFAULTS, **row} if dtype is TICK_DTYPE else dict(row)
        if dtype is TICK_DTYPE:
            full.setdefault("time", full["time_msc"] // 1000)
        out[i] = tuple(full[name] for name in dtype.names)
    return out


@dataclass
class FakeMT5:
    now: Callable[[], datetime]
    """True UTC "now" (the test clock)."""
    trade_mode: int = ACCOUNT_TRADE_MODE_DEMO
    currency: str = "USD"
    bid: float = 4142.71
    ask: float = 4142.76
    quote_age: timedelta = timedelta(0)
    server_tz: ServerTimeZone = field(default_factory=ServerTimeZone)
    commission_per_lot: float = 1.7
    initialize_results: list[bool] = field(default_factory=list)
    lose_next_reply: bool = False
    reject_next: int | None = None
    drop_stops: bool = False
    connected: bool = True
    fail_next_copies: int = 0
    """The next N copy_* calls fail like a broken IPC link (None + error)."""

    rates: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    tick_rows: list[dict[str, Any]] = field(default_factory=list)
    positions: dict[int, SimpleNamespace] = field(default_factory=dict)
    pending: dict[int, SimpleNamespace] = field(default_factory=dict)
    history_orders: list[SimpleNamespace] = field(default_factory=list)
    deals: list[SimpleNamespace] = field(default_factory=list)
    sent: list[dict[str, Any]] = field(default_factory=list)
    initialize_calls: list[dict[str, Any]] = field(default_factory=list)
    shutdown_calls: int = 0
    error: tuple[int, str] = (1, "Success")
    _next_ticket: int = 1000
    _initialized: bool = False

    def __post_init__(self) -> None:
        for name, value in globals().items():
            if name.isupper() and isinstance(value, int):
                setattr(self, name, value)

    # ------------------------------------------------------------------ clock helpers
    def server_s(self, utc: datetime) -> int:
        return int(self.server_tz.to_server_seconds(utc))

    def _server_ms(self) -> int:
        return int(self.server_tz.to_server_seconds(self.now()) * 1000)

    def _ticket(self) -> int:
        self._next_ticket += 1
        return self._next_ticket

    # ------------------------------------------------------------------ connection
    def initialize(self, **kwargs: Any) -> bool:
        self.initialize_calls.append(kwargs)
        ok = self.initialize_results.pop(0) if self.initialize_results else True
        self._initialized = ok
        self.error = (1, "Success") if ok else (-10005, "IPC timeout")
        return ok

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self._initialized = False

    def last_error(self) -> tuple[int, str]:
        return self.error

    def terminal_info(self) -> SimpleNamespace | None:
        if not self._initialized:
            return None
        return SimpleNamespace(connected=self.connected, trade_allowed=True)

    def account_info(self) -> SimpleNamespace | None:
        if not self._initialized:
            self.error = (-10004, "No IPC connection")
            return None
        return SimpleNamespace(
            login=504366,
            server="FusionMarkets-Demo",
            currency=self.currency,
            trade_mode=self.trade_mode,
            balance=1000.0,
            equity=1000.0,
            margin=0.0,
            margin_free=1000.0,
            leverage=500,
        )

    # ------------------------------------------------------------------ symbols
    def _symbol(self) -> SimpleNamespace:
        values = {
            "name": "XAUUSD",
            "path": "Commodities\\XAUUSD",
            "description": "Gold vs US Dollar",
            "digits": 2,
            "point": 0.01,
            "trade_tick_size": 0.01,
            "trade_tick_value": 1.0,
            "trade_contract_size": 100.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "trade_stops_level": 0,
            "trade_freeze_level": 0,
            "currency_profit": "USD",
            "currency_margin": "XAU",
            "filling_mode": 3,
        }
        ns = SimpleNamespace(**values)
        ns._asdict = lambda: dict(values)
        return ns

    def symbols_get(self) -> tuple[SimpleNamespace, ...]:
        return (self._symbol(),)

    def symbol_info(self, name: str) -> SimpleNamespace | None:
        return self._symbol() if name == "XAUUSD" else None

    def symbol_select(self, name: str, enable: bool) -> bool:
        return name == "XAUUSD"

    def symbol_info_tick(self, name: str) -> SimpleNamespace | None:
        if name != "XAUUSD" or not self._initialized:
            return None
        ms = self._server_ms() - int(self.quote_age.total_seconds() * 1000)
        return SimpleNamespace(
            time=ms // 1000,
            time_msc=ms,
            bid=self.bid,
            ask=self.ask,
            last=0.0,
            volume=0,
            volume_real=0.0,
        )

    # ------------------------------------------------------------------ data
    def _ipc_ok(self) -> bool:
        if self.fail_next_copies > 0:
            self.fail_next_copies -= 1
            self.error = (-10001, "IPC send failed")
            return False
        if not self._initialized:
            self.error = (-10004, "No IPC connection")
            return False
        return True

    def copy_rates_range(self, symbol: str, tf: int, frm: int, to: int) -> np.ndarray | None:
        if not self._ipc_ok():
            return None
        rows = [r for r in self.rates.get(tf, []) if frm <= r["time"] <= to]
        return _structured(rows, RATE_DTYPE)

    def copy_ticks_range(self, symbol: str, frm: int, to: int, flags: int) -> np.ndarray | None:
        if not self._ipc_ok():
            return None
        hi = min(to * 1000, self._server_ms())  # a terminal never has ticks from the future
        rows = [t for t in self.tick_rows if frm * 1000 <= t["time_msc"] <= hi]
        return _structured(rows, TICK_DTYPE)

    # ------------------------------------------------------------------ state
    def positions_get(
        self, symbol: str | None = None, ticket: int | None = None
    ) -> tuple[SimpleNamespace, ...]:
        return tuple(
            p
            for p in self.positions.values()
            if (symbol is None or p.symbol == symbol) and (ticket is None or p.ticket == ticket)
        )

    def orders_get(self, symbol: str | None = None) -> tuple[SimpleNamespace, ...]:
        return tuple(o for o in self.pending.values() if symbol is None or o.symbol == symbol)

    def history_orders_get(self, frm: int, to: int) -> tuple[SimpleNamespace, ...]:
        return tuple(o for o in self.history_orders if frm <= o.time_setup <= to)

    def history_deals_get(
        self,
        frm: int | None = None,
        to: int | None = None,
        *,
        ticket: int | None = None,
        position: int | None = None,
    ) -> tuple[SimpleNamespace, ...]:
        if ticket is not None:
            return tuple(d for d in self.deals if d.ticket == ticket)
        if position is not None:
            return tuple(d for d in self.deals if d.position_id == position)
        assert frm is not None
        assert to is not None
        return tuple(d for d in self.deals if frm <= d.time <= to)

    # ------------------------------------------------------------------ trading
    def order_check(self, request: dict[str, Any]) -> SimpleNamespace | None:
        if len(request.get("comment", "")) > MAX_COMMENT:
            self.error = (-2, 'Invalid "comment" argument')
            return None
        return SimpleNamespace(retcode=0, comment="Done", margin=6.26)

    def order_send(self, request: dict[str, Any]) -> SimpleNamespace | None:
        if len(request.get("comment", "")) > MAX_COMMENT:
            self.error = (-2, 'Invalid "comment" argument')
            return None
        self.sent.append(dict(request))
        if self.reject_next is not None:
            code, self.reject_next = self.reject_next, None
            return SimpleNamespace(retcode=code, comment="Rejected", order=0, deal=0, price=0.0)
        result = self._execute(request)
        if self.lose_next_reply:
            self.lose_next_reply = False
            self.error = (-10001, "IPC send failed")
            return None
        return result

    def _record(self, kind: str, **values: Any) -> SimpleNamespace:
        return SimpleNamespace(kind=kind, **values)

    def _execute(self, req: dict[str, Any]) -> SimpleNamespace:
        action = req["action"]
        now_s, now_ms = self._server_ms() // 1000, self._server_ms()
        done = TRADE_RETCODE_DONE
        if action == TRADE_ACTION_DEAL and "position" not in req:
            ticket = self._ticket()
            buy = req["type"] == ORDER_TYPE_BUY
            price = self.ask if buy else self.bid
            comment = req.get("comment", "")
            sl = 0.0 if self.drop_stops else req["sl"]
            self.positions[ticket] = SimpleNamespace(
                ticket=ticket,
                identifier=ticket,
                symbol=req["symbol"],
                type=POSITION_TYPE_BUY if buy else POSITION_TYPE_SELL,
                volume=req["volume"],
                price_open=price,
                time=now_s,
                time_msc=now_ms,
                sl=sl,
                tp=req["tp"],
                comment=comment,
                magic=req["magic"],
                profit=0.0,
            )
            self._history_order(ticket, req, ticket, now_s)
            deal = self._deal(ticket, ticket, req, price, DEAL_ENTRY_IN, now_s, now_ms)
            return SimpleNamespace(
                retcode=done,
                comment="Request executed",
                order=ticket,
                deal=deal,
                price=price,
                volume=req["volume"],
            )
        if action == TRADE_ACTION_DEAL:
            pos = self.positions[req["position"]]
            order = self._ticket()
            price = self.bid if pos.type == POSITION_TYPE_BUY else self.ask
            self._history_order(order, req, pos.ticket, now_s)
            deal = self._deal(order, pos.ticket, req, price, DEAL_ENTRY_OUT, now_s, now_ms)
            remaining = round(pos.volume - req["volume"], 8)
            if remaining > 0:
                pos.volume = remaining
            else:
                del self.positions[pos.ticket]
            return SimpleNamespace(
                retcode=done,
                comment="Request executed",
                order=order,
                deal=deal,
                price=price,
                volume=req["volume"],
            )
        if action == TRADE_ACTION_SLTP:
            pos = self.positions[req["position"]]
            pos.sl, pos.tp = req["sl"], req["tp"]
            return SimpleNamespace(retcode=done, comment="Request executed", order=0, deal=0)
        if action == TRADE_ACTION_PENDING:
            ticket = self._ticket()
            self.pending[ticket] = SimpleNamespace(
                ticket=ticket,
                symbol=req["symbol"],
                type=req["type"],
                volume_current=req["volume"],
                price_open=req["price"],
                sl=req["sl"],
                tp=req["tp"],
                time_setup=now_s,
                time_setup_msc=now_ms,
                comment=req["comment"],
                magic=req["magic"],
            )
            return SimpleNamespace(
                retcode=TRADE_RETCODE_PLACED,
                comment="Request executed",
                order=ticket,
                deal=0,
                price=req["price"],
                volume=req["volume"],
            )
        if action == TRADE_ACTION_REMOVE:
            o = self.pending.pop(req["order"])
            self.history_orders.append(
                SimpleNamespace(
                    ticket=o.ticket,
                    comment=o.comment,
                    position_id=0,
                    time_setup=o.time_setup,
                    state=ORDER_STATE_CANCELED,
                )
            )
            return SimpleNamespace(retcode=done, comment="Request executed", order=o.ticket, deal=0)
        raise AssertionError(f"unexpected action {action}")

    def _history_order(self, ticket: int, req: dict[str, Any], position: int, now_s: int) -> None:
        self.history_orders.append(
            SimpleNamespace(
                ticket=ticket,
                comment=req.get("comment", ""),
                position_id=position,
                time_setup=now_s,
                state=ORDER_STATE_FILLED,
            )
        )

    def _deal(
        self,
        order: int,
        position: int,
        req: dict[str, Any],
        price: float,
        entry: int,
        now_s: int,
        now_ms: int,
    ) -> int:
        ticket = self._ticket()
        self.deals.append(
            SimpleNamespace(
                ticket=ticket,
                order=order,
                position_id=position,
                symbol=req["symbol"],
                type=DEAL_TYPE_BUY if req["type"] == ORDER_TYPE_BUY else DEAL_TYPE_SELL,
                entry=entry,
                volume=req["volume"],
                price=price,
                time=now_s,
                time_msc=now_ms,
                commission=-round(self.commission_per_lot * req["volume"], 2),
                fee=0.0,
                swap=0.0,
                profit=0.0,
                comment=req.get("comment", ""),
                magic=req.get("magic", 0),
            )
        )
        return ticket
