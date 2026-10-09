"""The risk-engine contract: every Signal gets a RiskDecision; only approved ones become orders.

Phase 3 ships a stand-in (``aurum.risk.fixed.FixedVolumeRisk``) so the simulator can be
exercised end to end; Phase 4 replaces it with sizing, limits and lockouts behind the same
interface, so the engine and the simulator do not change.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from aurum.core.models import (
    AccountSnapshot,
    OrderState,
    PositionState,
    RiskDecision,
    SessionState,
    Signal,
    SymbolSpec,
    Tick,
)


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Everything known at decision time ``now`` (UTC)."""

    now: datetime
    session: SessionState
    account: AccountSnapshot
    spec: SymbolSpec
    positions: Sequence[PositionState]
    """Positions owned by Aurum (magic number and "AU" key)."""
    orders: Sequence[OrderState]
    """Pending orders owned by Aurum."""
    quote: Tick | None
    """Latest quote with ``time <= now`` (None if there is none)."""


class RiskEngine(Protocol):
    def evaluate(self, signal: Signal, ctx: RiskContext) -> RiskDecision: ...
