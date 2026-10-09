"""Parity check: Aurum's simulated trades vs the MT5 Strategy Tester's (Phase 3 exit gate).

Both sides are reduced to ``ParityTrade`` (UTC times, prices in USD/oz, money in the account
currency) and paired by **UTC entry date and side**: the reference strategy opens at most one
BUY and one SELL per day, so a pair is the same decision on both sides even when fills differ.

Default tolerances (proposed in reports/phase_3.md, the owner may tighten them):

* fill times within 1 s (tester and stored ticks both carry milliseconds);
* entry/exit prices, stop and target within 1 point;
* the same exit reason;
* booked profit and commission within 0.02 (both round each deal to cents).

Anything outside them is a difference to explain, never something to tune away.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import polars as pl

from aurum.core.enums import ExitReason, Side


@dataclass(frozen=True, slots=True)
class ParityTrade:
    side: Side
    volume: float
    """Lots."""
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: ExitReason
    stop_loss: float | None
    take_profit: float | None
    profit: float
    """Booked price P&L of both deals, before commission (account currency)."""
    commission: float
    """Commission of both deals as a positive cost (account currency)."""

    @property
    def key(self) -> tuple[date, Side]:
        return self.entry_time.date(), self.side


@dataclass(frozen=True, slots=True)
class Tolerance:
    time: timedelta = timedelta(seconds=1)
    price_points: float = 1.0
    money: float = 0.02


@dataclass(frozen=True, slots=True)
class FieldDiff:
    name: str
    aurum: Any
    tester: Any
    difference: str
    """Human-readable size of the difference (seconds, points or money)."""


@dataclass(frozen=True, slots=True)
class ParityRow:
    key: tuple[date, Side]
    aurum: ParityTrade | None
    tester: ParityTrade | None
    diffs: tuple[FieldDiff, ...] = ()

    @property
    def ok(self) -> bool:
        return self.aurum is not None and self.tester is not None and not self.diffs


@dataclass(frozen=True)
class ParityReport:
    rows: list[ParityRow]
    tolerance: Tolerance
    point: float
    tester_outside_window: int = 0
    """Tester trades entered outside the run's dates (ignored, e.g. an inclusive end date)."""

    @property
    def matched(self) -> int:
        return sum(1 for r in self.rows if r.aurum is not None and r.tester is not None)

    @property
    def within_tolerance(self) -> int:
        return sum(1 for r in self.rows if r.ok)

    @property
    def only_aurum(self) -> list[ParityRow]:
        return [r for r in self.rows if r.tester is None]

    @property
    def only_tester(self) -> list[ParityRow]:
        return [r for r in self.rows if r.aurum is None]

    @property
    def differing(self) -> list[ParityRow]:
        return [r for r in self.rows if not r.ok]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.rows)

    def summary(self) -> dict[str, Any]:
        both = [r for r in self.rows if r.aurum is not None and r.tester is not None]
        aurum_pnl = sum(r.aurum.profit - r.aurum.commission for r in both if r.aurum)
        tester_pnl = sum(r.tester.profit - r.tester.commission for r in both if r.tester)
        return {
            "aurum_trades": sum(1 for r in self.rows if r.aurum is not None),
            "tester_trades": sum(1 for r in self.rows if r.tester is not None),
            "matched": self.matched,
            "within_tolerance": self.within_tolerance,
            "only_aurum": len(self.only_aurum),
            "only_tester": len(self.only_tester),
            "tester_outside_window": self.tester_outside_window,
            "matched_net_pnl_aurum": round(aurum_pnl, 2),
            "matched_net_pnl_tester": round(tester_pnl, 2),
            "tolerance_seconds": self.tolerance.time.total_seconds(),
            "tolerance_points": self.tolerance.price_points,
            "tolerance_money": self.tolerance.money,
        }

    def frame(self) -> pl.DataFrame:
        """One row per paired decision, with every out-of-tolerance field spelled out."""
        return pl.DataFrame(
            [
                {
                    "entry_date": r.key[0],
                    "side": str(r.key[1]),
                    "status": (
                        "only_aurum"
                        if r.tester is None
                        else "only_tester"
                        if r.aurum is None
                        else "ok"
                        if r.ok
                        else "differs"
                    ),
                    "aurum_entry": r.aurum.entry_time if r.aurum else None,
                    "tester_entry": r.tester.entry_time if r.tester else None,
                    "aurum_exit_reason": str(r.aurum.exit_reason) if r.aurum else None,
                    "tester_exit_reason": str(r.tester.exit_reason) if r.tester else None,
                    "aurum_net": (
                        round(r.aurum.profit - r.aurum.commission, 2) if r.aurum else None
                    ),
                    "tester_net": (
                        round(r.tester.profit - r.tester.commission, 2) if r.tester else None
                    ),
                    "diffs": "; ".join(
                        f"{d.name}: aurum {d.aurum} vs tester {d.tester} ({d.difference})"
                        for d in r.diffs
                    ),
                }
                for r in self.rows
            ],
            schema={
                "entry_date": pl.Date,
                "side": pl.String,
                "status": pl.String,
                "aurum_entry": pl.Datetime("ms", "UTC"),
                "tester_entry": pl.Datetime("ms", "UTC"),
                "aurum_exit_reason": pl.String,
                "tester_exit_reason": pl.String,
                "aurum_net": pl.Float64,
                "tester_net": pl.Float64,
                "diffs": pl.String,
            },
        )


@dataclass
class _Differ:
    tol: Tolerance
    point: float
    found: list[FieldDiff] = field(default_factory=list)

    def time(self, name: str, a: datetime, b: datetime) -> None:
        gap = abs(a - b)
        if gap > self.tol.time:
            self.found.append(FieldDiff(name, a, b, f"{gap.total_seconds():.3f} s"))

    def price(self, name: str, a: float | None, b: float | None) -> None:
        if a is None or b is None:
            if a != b:
                self.found.append(FieldDiff(name, a, b, "missing on one side"))
            return
        points = abs(a - b) / self.point
        if points > self.tol.price_points + 1e-9:
            self.found.append(FieldDiff(name, a, b, f"{points:.1f} points"))

    def money(self, name: str, a: float, b: float) -> None:
        if abs(a - b) > self.tol.money + 1e-9:
            self.found.append(FieldDiff(name, a, b, f"{a - b:+.2f}"))

    def same(self, name: str, a: object, b: object) -> None:
        if a != b:
            self.found.append(FieldDiff(name, a, b, "different"))


def _diffs(a: ParityTrade, b: ParityTrade, tol: Tolerance, point: float) -> tuple[FieldDiff, ...]:
    d = _Differ(tol, point)
    d.same("volume", a.volume, b.volume)
    d.time("entry_time", a.entry_time, b.entry_time)
    d.price("entry_price", a.entry_price, b.entry_price)
    d.price("stop_loss", a.stop_loss, b.stop_loss)
    d.price("take_profit", a.take_profit, b.take_profit)
    d.same("exit_reason", a.exit_reason, b.exit_reason)
    d.time("exit_time", a.exit_time, b.exit_time)
    d.price("exit_price", a.exit_price, b.exit_price)
    d.money("profit", a.profit, b.profit)
    d.money("commission", a.commission, b.commission)
    return tuple(d.found)


def _by_key(trades: Sequence[ParityTrade], side_name: str) -> dict[tuple[date, Side], ParityTrade]:
    out: dict[tuple[date, Side], ParityTrade] = {}
    for t in trades:
        if t.key in out:
            raise ValueError(f"{side_name}: two {t.side} trades entered on {t.key[0]}")
        out[t.key] = t
    return out


def compare(
    aurum: Sequence[ParityTrade],
    tester: Sequence[ParityTrade],
    *,
    point: float,
    tolerance: Tolerance | None = None,
    window: tuple[date, date] | None = None,
) -> ParityReport:
    """Pair trades by (UTC entry date, side) and list every field outside ``tolerance``.

    ``window`` (first, last UTC entry date, inclusive): tester trades outside it are counted
    and ignored, so a tester period one day longer than the run is not a difference.
    """
    tol = tolerance or Tolerance()
    inside = [t for t in tester if window is None or window[0] <= t.entry_time.date() <= window[1]]
    a, t = _by_key(aurum, "aurum"), _by_key(inside, "tester")
    rows = []
    for key in sorted(a.keys() | t.keys(), key=lambda k: (k[0], str(k[1]))):
        x, y = a.get(key), t.get(key)
        diffs = _diffs(x, y, tol, point) if x is not None and y is not None else ()
        rows.append(ParityRow(key, x, y, diffs))
    return ParityReport(
        rows=rows, tolerance=tol, point=point, tester_outside_window=len(tester) - len(inside)
    )


def from_trades_frame(trades: pl.DataFrame) -> list[ParityTrade]:
    """Aurum's side, from a run's trades.parquet (``BACKTEST_TRADES``)."""
    return [
        ParityTrade(
            side=Side(r["side"]),
            volume=r["volume"],
            entry_time=r["entry_time"],
            entry_price=r["entry_price"],
            exit_time=r["exit_time"],
            exit_price=r["exit_price"],
            exit_reason=ExitReason(r["exit_reason"]),
            stop_loss=r["stop_loss"],
            take_profit=r["take_profit"],
            profit=round(r["net_pnl"] + r["commission"], 2),
            commission=r["commission"],
        )
        for r in trades.iter_rows(named=True)
    ]
