"""The strategy contract: Signals only, from completed bars only.

A strategy is called by ``aurum.execution.engine.Engine`` at every close of its
``decision_timeframe`` while entries are allowed, and for every open position it owns. It sees
the market only through a ``MarketView`` (bars with ``close_time <= now``) and can never create
orders: the risk engine decides, the broker executes. The same object runs unchanged in
BACKTEST, SHADOW and DEMO.
"""

from typing import Protocol

from aurum.core.models import PositionState, Signal
from aurum.core.timeframes import Timeframe
from aurum.features.view import MarketView


class Strategy(Protocol):
    @property
    def strategy_id(self) -> str:
        """Stable identifier, also the ``Signal.strategy_id``."""
        ...

    @property
    def decision_timeframe(self) -> Timeframe:
        """Entries are considered only at closes of this timeframe (M15 or M5 variant)."""
        ...

    def on_bar(self, view: MarketView) -> Signal | None:
        """A new entry proposal at ``view.now``, or None. ``Signal.time`` must be ``view.now``."""
        ...

    def invalidation(self, view: MarketView, position: PositionState, signal: Signal) -> str | None:
        """Why the setup behind ``signal`` no longer holds (objective terms), or None to keep
        the position. Stops, targets, ``max_hold`` and force-flat are handled elsewhere."""
        ...
