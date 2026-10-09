"""Phase 3 stand-in risk engine: a fixed volume, one position at a time.

Exists only so the simulator, the reference strategy and the MT5 Strategy Tester parity check
run end to end (a fixed lot size is also what the parity EA trades). It is NOT the Phase 4
risk engine: no equity-based sizing, no loss limits, no lockouts, no spread or staleness
checks. Phase 4 replaces it behind ``aurum.risk.base.RiskEngine``.
"""

from aurum.core.models import RiskDecision, Signal
from aurum.risk.base import RiskContext


class FixedVolumeRisk:
    def __init__(self, volume: float, commission_per_lot_side: float = 0.0) -> None:
        if volume <= 0:
            raise ValueError("volume must be positive")
        self.volume = volume
        self._commission = commission_per_lot_side

    def evaluate(self, signal: Signal, ctx: RiskContext) -> RiskDecision:
        reasons = []
        if not ctx.session.entries_allowed:
            reasons.append(f"session phase {ctx.session.phase}: no new entries")
        if ctx.positions or ctx.orders:
            reasons.append("one position at a time: Aurum already has exposure")
        if not ctx.spec.is_valid_volume(self.volume):
            reasons.append(f"volume {self.volume} not tradable for {ctx.spec.name}")
        reference = signal.entry_price
        if reference is None and ctx.quote is not None:
            reference = ctx.quote.price_for(signal.side)
        if reference is None:
            reasons.append("no quote to measure the stop distance")
        elif (reference - signal.stop_loss) * signal.side.sign <= 0:
            reasons.append(f"stop {signal.stop_loss} is not beyond reference price {reference}")
        if reasons:
            return RiskDecision(
                decision_id=f"rd-{signal.key}",
                signal_key=signal.key,
                approved=False,
                volume=0.0,
                reasons=tuple(reasons),
                decided_at=ctx.now,
            )
        assert reference is not None
        distance = abs(reference - signal.stop_loss)
        risk = (
            distance * ctx.spec.money_per_price_unit_per_lot * self.volume
            + 2 * self._commission * self.volume
        )
        return RiskDecision(
            decision_id=f"rd-{signal.key}",
            signal_key=signal.key,
            approved=True,
            volume=self.volume,
            stop_distance_points=ctx.spec.price_to_points(distance),
            risk_amount=round(risk, 2),
            reasons=(f"fixed volume {self.volume} lots (Phase 3 stand-in)",),
            decided_at=ctx.now,
        )
