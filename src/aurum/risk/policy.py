"""The Phase 4 risk engine: sizing, limits and lockouts (risk.yaml, ADR-0010).

Every check runs on every signal and every failure is listed, so a rejection explains itself.
A signal is approved only if all of these hold at decision time ``now``:

* the session phase allows entries and ``force_flat_at - now >= min_minutes_before_cutoff``;
* no news blackout (``aurum.sessions.news``);
* the broker is connected and the latest quote is at most ``stale_quote_seconds`` old;
* spread <= ``max_spread_points`` and <= ``max_spread_atr_fraction`` x M15 ATR (both must be
  known: an uncalibrated cap or a missing ATR rejects);
* no Aurum position or pending order (one position at a time);
* fewer than ``max_trades_per_day`` entries today (London trading date);
* the new trade's risk fits what is left of the daily and of the weekly loss budget
  (prospective, owner decision 2026-10-10)::

      limit         = daily/weekly_loss_limit_pct x balance at the start of the day/week
      loss to date  = -(Aurum's closed P&L incl. commission + open P&L of Aurum's positions)
      open risk     = sum over Aurum's positions of the further loss if each stopped out now
                      (``open_risk``)
      remaining     = limit - loss to date - open risk
      approve only if new trade risk (the sizing below) <= remaining

  The start balance is the current balance minus Aurum's closed P&L since then. A loss to date
  at or above the limit is reported as such;
* fewer than ``consecutive_loss_cooldown`` losing trades in a row within the current session
  window (the lockout lasts for the rest of that window);
* the stop is beyond the reference price (the signal's entry price, else the executable side
  of the quote);
* the sized volume is at least ``volume_min`` and its margin fits ``margin_free``.

Sizing (owner decision 2026-10-09: costs count against the budget)::

    budget        = equity x risk_per_trade_pct / 100
    cost per lot  = stop distance x value per price unit per lot
                    + 2 x commission per lot per side
                    + stop slippage points x point x value per price unit per lot
    lots          = floor(budget / cost per lot, volume_step), capped at volume_max

so the loss when the stop fills at the configured slippage, commission included, never exceeds
``budget`` (property-tested). Entry slippage is not budgeted.

Daily/weekly state comes from the broker's deal history, never from local memory, so a restart
cannot reset a limit.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from aurum.core.enums import DealEntry
from aurum.core.models import DealRecord, PositionState, RiskDecision, Signal, SymbolSpec, Tick
from aurum.risk.base import RiskContext
from aurum.sessions.news import NewsBlackout
from aurum.settings import RiskConfig


def size_volume(
    *,
    budget: float,
    stop_distance: float,
    spec: SymbolSpec,
    commission_per_lot_side: float,
    stop_slippage_points: float,
) -> tuple[float, float]:
    """Largest tradable volume whose stop-out loss (incl. costs) fits ``budget``.

    Returns ``(lots, cost_per_lot)``; ``lots`` is a multiple of ``volume_step`` capped at
    ``volume_max`` and may be below ``volume_min`` (the caller rejects that).
    """
    value = spec.money_per_price_unit_per_lot
    per_lot = (
        stop_distance * value
        + 2 * commission_per_lot_side
        + spec.points_to_price(stop_slippage_points) * value
    )
    if per_lot <= 0 or budget <= 0:
        return 0.0, per_lot
    step = spec.volume_step
    decimals = max(0, -math.floor(math.log10(step)))
    steps = min(math.floor(budget / per_lot / step), math.floor(spec.volume_max / step + 1e-9))
    while steps > 0 and round(steps * step, decimals) * per_lot > budget:
        steps -= 1  # float rounding must never push the risk above the budget
    return round(max(steps, 0) * step, decimals), per_lot


def open_risk(
    positions: Sequence[PositionState],
    quote: Tick | None,
    *,
    now: datetime,
    stale_after_seconds: float,
    spec: SymbolSpec,
    commission_per_lot_side: float,
    stop_slippage_points: float,
) -> tuple[float | None, list[str]]:
    """Further loss (money) if every position stopped out now, beyond its open P&L.

    Per position: ``volume x (d x value per price unit per lot + exit commission per lot +
    stop slippage x point x value)``, with ``d = max(0, (exit price - stop) x side sign)`` and
    the exit price the bid for a BUY, the ask for a SELL. Entry commission is already booked in
    the deal history. Returns ``(None, problems)`` when it can't be known: a position without a
    stop (an incident) or no fresh quote. Callers must then refuse new risk.
    """
    if not positions:
        return 0.0, []
    problems = [
        f"open risk unknown: position {p.position_id} has no stop (incident)"
        for p in positions
        if p.stop_loss is None
    ]
    if quote is None:
        problems.append("open risk unknown: no quote")
    elif (now - quote.time).total_seconds() > stale_after_seconds:
        problems.append("open risk unknown: stale quote")
    if problems:
        return None, problems
    assert quote is not None
    value = spec.money_per_price_unit_per_lot
    slippage = spec.points_to_price(stop_slippage_points) * value
    total = 0.0
    for p in positions:
        assert p.stop_loss is not None
        d = max(0.0, (quote.exit_price_for(p.side) - p.stop_loss) * p.side.sign)
        total += p.volume * (d * value + commission_per_lot_side + slippage)
    return total, []


def _net(deals: Sequence[DealRecord]) -> float:
    return sum(d.profit + d.commission for d in deals)


class PolicyRiskEngine:
    def __init__(
        self,
        config: RiskConfig,
        *,
        commission_per_lot_side: float,
        stop_slippage_points: float,
        news: NewsBlackout,
    ) -> None:
        self._cfg = config
        self._commission = commission_per_lot_side
        self._slippage = stop_slippage_points
        self._news = news

    def evaluate(self, signal: Signal, ctx: RiskContext) -> RiskDecision:
        cfg = self._cfg
        now = ctx.now
        reasons: list[str] = []

        # --- when: session, cutoff, news
        if not ctx.session.entries_allowed:
            reasons.append(f"session phase {ctx.session.phase}: no new entries")
        if ctx.session.force_flat_at is not None:
            left = (ctx.session.force_flat_at - now).total_seconds() / 60
            if left < cfg.min_minutes_before_cutoff:
                reasons.append(
                    f"{left:.0f} min before cutoff < {cfg.min_minutes_before_cutoff} min required"
                )
        if (news := self._news.reason_at(now)) is not None:
            reasons.append(news)

        # --- market: connection, quote, spread
        quote = ctx.quote
        if not ctx.connected:
            reasons.append("broker disconnected")
        if quote is None:
            reasons.append("no quote")
        else:
            age = (now - quote.time).total_seconds()
            if age > cfg.stale_quote_seconds:
                reasons.append(f"stale quote: {age:.1f} s old > {cfg.stale_quote_seconds} s")
            spread_points = ctx.spec.price_to_points(quote.spread)
            if cfg.max_spread_points is None:
                reasons.append("max_spread_points is not calibrated")
            elif spread_points > cfg.max_spread_points:
                reasons.append(f"spread {spread_points:.0f} points > {cfg.max_spread_points}")
            if ctx.m15_atr is None or ctx.m15_atr <= 0:
                reasons.append("M15 ATR unavailable")
            elif quote.spread > cfg.max_spread_atr_fraction * ctx.m15_atr:
                reasons.append(
                    f"spread {quote.spread:.2f} > {cfg.max_spread_atr_fraction} x M15 ATR "
                    f"{ctx.m15_atr:.2f}"
                )

        # --- exposure
        if ctx.positions or ctx.orders:
            reasons.append("one position at a time: Aurum already has exposure")

        # --- stop and size
        reference = signal.entry_price
        if reference is None and quote is not None:
            reference = quote.price_for(signal.side)
        lots = cost_per_lot = 0.0
        distance = None
        if reference is None:
            reasons.append("no reference price to measure the stop distance")
        elif (reference - signal.stop_loss) * signal.side.sign <= 0:
            reasons.append(f"stop {signal.stop_loss} is not beyond reference price {reference}")
        else:
            distance = abs(reference - signal.stop_loss)
            budget = ctx.account.equity * cfg.risk_per_trade_pct / 100
            lots, cost_per_lot = size_volume(
                budget=budget,
                stop_distance=distance,
                spec=ctx.spec,
                commission_per_lot_side=self._commission,
                stop_slippage_points=self._slippage,
            )
            if lots < ctx.spec.volume_min - 1e-12:
                reasons.append(
                    f"sized volume {lots} < volume_min {ctx.spec.volume_min} "
                    f"(budget {budget:.2f}, {cost_per_lot:.2f} per lot)"
                )
            else:
                margin = lots * ctx.spec.contract_size * reference / ctx.account.leverage
                if margin > ctx.account.margin_free:
                    reasons.append(
                        f"margin {margin:.2f} > free margin {ctx.account.margin_free:.2f}"
                    )

        # --- account limits (after sizing: the loss budget must cover the new trade's risk)
        new_risk = lots * cost_per_lot if lots >= ctx.spec.volume_min - 1e-12 else None
        reasons.extend(self._limits(ctx, new_risk))

        if reasons:
            return RiskDecision(
                decision_id=f"rd-{signal.key}",
                signal_key=signal.key,
                approved=False,
                volume=0.0,
                reasons=tuple(reasons),
                decided_at=now,
            )
        assert distance is not None
        return RiskDecision(
            decision_id=f"rd-{signal.key}",
            signal_key=signal.key,
            decided_at=now,
            approved=True,
            volume=lots,
            stop_distance_points=ctx.spec.price_to_points(distance),
            risk_amount=round(lots * cost_per_lot, 2),
            reasons=(
                f"{lots} lots: {cfg.risk_per_trade_pct}% of equity {ctx.account.equity:.2f} / "
                f"{cost_per_lot:.2f} per lot (stop {ctx.spec.price_to_points(distance):.0f} "
                f"points + commission + {self._slippage:g} points slippage)",
            ),
        )

    def _limits(self, ctx: RiskContext, new_risk: float | None) -> list[str]:
        """Loss budgets, trades per day and the losing streak. ``new_risk`` is the sized
        trade's risk (money), or None when sizing already failed."""
        cfg = self._cfg
        open_pnl = sum(p.unrealized_pnl for p in ctx.positions)
        at_risk, reasons = open_risk(
            ctx.positions,
            ctx.quote,
            now=ctx.now,
            stale_after_seconds=cfg.stale_quote_seconds,
            spec=ctx.spec,
            commission_per_lot_side=self._commission,
            stop_slippage_points=self._slippage,
        )
        for label, start, pct in (
            ("daily", ctx.day_start, cfg.daily_loss_limit_pct),
            ("weekly", ctx.week_start, cfg.weekly_loss_limit_pct),
        ):
            if start is None:
                reasons.append(f"{label} loss limit: period start unknown")
                continue
            closed = _net([d for d in ctx.deals if d.time >= start])
            start_balance = ctx.account.balance - closed
            loss = -(closed + open_pnl)
            limit = start_balance * pct / 100
            if loss >= limit:
                reasons.append(f"{label} loss {loss:.2f} >= limit {limit:.2f} ({pct}%)")
            elif at_risk is not None and new_risk is not None:
                remaining = limit - loss - at_risk
                if new_risk > remaining + 1e-9:
                    reasons.append(
                        f"{label} budget: limit {limit:.2f} ({pct}%) - loss to date {loss:.2f} "
                        f"- open risk {at_risk:.2f} = remaining {remaining:.2f} "
                        f"< new trade risk {new_risk:.2f}"
                    )
        if ctx.day_start is not None:
            entries = sum(
                1 for d in ctx.deals if d.entry is DealEntry.IN and d.time >= ctx.day_start
            )
            if entries >= cfg.max_trades_per_day:
                reasons.append(f"{entries} trades today >= max {cfg.max_trades_per_day}")
        if ctx.session_start is not None:
            streak = losing_streak(ctx.deals, since=ctx.session_start)
            if streak >= cfg.consecutive_loss_cooldown:
                reasons.append(f"{streak} consecutive losses this session: locked until it ends")
        return reasons


def losing_streak(deals: Sequence[DealRecord], *, since: datetime) -> int:
    """Losing trades in a row (net of commission), newest first, among positions closed at or
    after ``since``."""
    by_position: dict[int, list[DealRecord]] = defaultdict(list)
    for d in deals:
        by_position[d.position_id].append(d)
    closed = sorted(
        (max(d.time for d in ds if d.entry is DealEntry.OUT), _net(ds))
        for ds in by_position.values()
        if any(d.entry is DealEntry.OUT for d in ds)
    )
    streak = 0
    for close_time, net in reversed(closed):
        if close_time < since or net >= 0:
            break
        streak += 1
    return streak
