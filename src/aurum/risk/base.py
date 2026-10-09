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
    DealRecord,
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
    connected: bool = True
    """The broker connection was up when the context was built."""
    m15_atr: float | None = None
    """ATR of completed M15 bars (price units), for the spread/ATR gate. None if unknown."""
    deals: Sequence[DealRecord] = ()
    """Aurum's own deals (magic and "AU" key) since ``week_start``, oldest first."""
    day_start: datetime | None = None
    """UTC start of the current London trading date."""
    week_start: datetime | None = None
    """UTC start of the Monday of the current London trading week."""
    session_start: datetime | None = None
    """UTC start of the session window ``now`` is in (consecutive-loss lockout scope)."""


class RiskEngine(Protocol):
    def evaluate(self, signal: Signal, ctx: RiskContext) -> RiskDecision: ...
