"""Read the deals CSV written by ``mql5/AurumReference.mq5`` in the MT5 Strategy Tester.

The EA writes raw server-clock milliseconds (``DEAL_TIME_MSC``); they become UTC here, with the
same ``ServerTimeZone`` as everything else (ADR-0004), so the EA's own clock arithmetic is
checked rather than trusted. Pure Python: no MetaTrader5 import.

Exit reasons: the tester's ``DEAL_REASON_SL``/``_TP`` give STOP/TARGET; closes the EA sent
itself carry its ``ea_reason`` (TIMEOUT or FORCE_FLAT). A close with neither (the tester
closing what is left when the test ends) is END_OF_DATA.
"""

import csv
from dataclasses import dataclass
from pathlib import Path

from aurum.adapters.mt5.servertime import ServerTimeZone
from aurum.backtest.parity import ParityTrade
from aurum.core.enums import ExitReason, Side
from aurum.features.view import from_ms

COLUMNS = (
    "deal",
    "position",
    "time_msc_server",
    "type",
    "entry",
    "reason",
    "ea_reason",
    "volume",
    "price",
    "sl",
    "tp",
    "commission",
    "swap",
    "profit",
    "symbol",
)


@dataclass(frozen=True, slots=True)
class _Deal:
    position: int
    time_ms: int
    side: Side
    entry: str
    reason: str
    ea_reason: str
    volume: float
    price: float
    sl: float
    tp: float
    commission: float
    profit: float


def _exit_reason(deal: _Deal) -> ExitReason:
    if deal.reason == "SL":
        return ExitReason.STOP
    if deal.reason == "TP":
        return ExitReason.TARGET
    if deal.ea_reason:
        return ExitReason(deal.ea_reason)
    return ExitReason.END_OF_DATA


def read_tester_deals(path: Path, server_tz: ServerTimeZone) -> list[ParityTrade]:
    """Trades (one per position, in entry order) from the EA's CSV, times in UTC."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if rows and tuple(rows[0].keys()) != COLUMNS:
        raise ValueError(f"{path}: unexpected columns {list(rows[0].keys())}")
    server_ms = [int(r["time_msc_server"]) for r in rows]
    utc_ms = server_tz.to_utc_ms(server_ms).tolist() if rows else []
    deals = [
        _Deal(
            position=int(r["position"]),
            time_ms=int(t),
            side=Side(r["type"]),
            entry=r["entry"],
            reason=r["reason"],
            ea_reason=r["ea_reason"],
            volume=float(r["volume"]),
            price=float(r["price"]),
            sl=float(r["sl"]),
            tp=float(r["tp"]),
            commission=float(r["commission"]),
            profit=float(r["profit"]) + float(r["swap"]),
        )
        for r, t in zip(rows, utc_ms, strict=True)
    ]
    opened: dict[int, _Deal] = {}
    closed: dict[int, _Deal] = {}
    for d in deals:
        target = opened if d.entry == "IN" else closed if d.entry == "OUT" else None
        if target is None:
            raise ValueError(f"{path}: position {d.position} has a {d.entry} deal (not IN/OUT)")
        if d.position in target:
            raise ValueError(f"{path}: position {d.position} has two {d.entry} deals")
        target[d.position] = d
    if open_only := sorted(opened.keys() - closed.keys()):
        raise ValueError(f"{path}: positions never closed: {open_only}")
    trades = []
    for pos, entry in sorted(opened.items(), key=lambda kv: (kv[1].time_ms, kv[0])):
        out = closed[pos]
        if out.volume != entry.volume:
            raise ValueError(f"{path}: position {pos} closed partially")
        # The opening deal carries the order's stop and target (0 = none); some builds only
        # stamp them on the closing deal.
        trades.append(
            ParityTrade(
                side=entry.side,
                volume=entry.volume,
                entry_time=from_ms(entry.time_ms),
                entry_price=entry.price,
                exit_time=from_ms(out.time_ms),
                exit_price=out.price,
                exit_reason=_exit_reason(out),
                stop_loss=entry.sl or out.sl or None,
                take_profit=entry.tp or out.tp or None,
                profit=round(entry.profit + out.profit, 2),
                commission=round(-(entry.commission + out.commission), 2),
            )
        )
    return trades
