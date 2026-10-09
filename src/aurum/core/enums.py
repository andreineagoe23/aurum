"""Shared enumerations."""

from enum import StrEnum


class Mode(StrEnum):
    """Run mode. Only DEMO and LIVE may ever send orders (see ``aurum.core.safety``)."""

    BACKTEST = "BACKTEST"
    SHADOW = "SHADOW"
    DEMO = "DEMO"
    LIVE = "LIVE"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        """+1 for BUY, -1 for SELL (direction of profit per unit price move up)."""
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class AccountTradeMode(StrEnum):
    """Mirror of MT5 ``ACCOUNT_TRADE_MODE_*``. Anything not DEMO is treated as real money."""

    DEMO = "DEMO"
    CONTEST = "CONTEST"
    REAL = "REAL"


class SessionPhase(StrEnum):
    """Phase of the trading day. See ``aurum.sessions.clock`` for exact boundaries.

    CLOSED -> PRE_SESSION -> OPEN <-> NO_NEW_ENTRIES -> EXIT_ONLY -> FORCE_FLAT -> CLOSED
    """

    CLOSED = "CLOSED"
    PRE_SESSION = "PRE_SESSION"
    OPEN = "OPEN"
    NO_NEW_ENTRIES = "NO_NEW_ENTRIES"
    EXIT_ONLY = "EXIT_ONLY"
    FORCE_FLAT = "FORCE_FLAT"

    @property
    def entries_allowed(self) -> bool:
        """New positions may only be opened in OPEN (risk checks still apply)."""
        return self is SessionPhase.OPEN

    @property
    def must_be_flat(self) -> bool:
        """Any exposure seen in this phase must be closed/cancelled immediately."""
        return self in _MUST_BE_FLAT


_MUST_BE_FLAT = frozenset({SessionPhase.CLOSED, SessionPhase.PRE_SESSION, SessionPhase.FORCE_FLAT})


class ExecutionStatus(StrEnum):
    FILLED = "FILLED"
    PLACED = "PLACED"
    MODIFIED = "MODIFIED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    """The connection failed during the request: the broker's state decides (reconciliation)."""


class DealEntry(StrEnum):
    """Whether a deal opened (IN) or closed (OUT) exposure."""

    IN = "IN"
    OUT = "OUT"


class DealReason(StrEnum):
    """Why the broker executed a deal (mirror of MT5 ``DEAL_REASON_*``)."""

    EXPERT = "EXPERT"
    """Requested through the API (Aurum's own orders and closes)."""
    MANUAL = "MANUAL"
    """Desktop terminal, mobile or web."""
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_OUT = "STOP_OUT"
    OTHER = "OTHER"


class ExitReason(StrEnum):
    """Why a trade ended. STOP/TARGET are broker-side; the others are Aurum's own closes."""

    STOP = "STOP"
    TARGET = "TARGET"
    INVALIDATION = "INVALIDATION"
    """The strategy reported that the setup no longer holds."""
    TIMEOUT = "TIMEOUT"
    """The signal's ``max_hold`` elapsed."""
    FORCE_FLAT = "FORCE_FLAT"
    """Session cutoff (or market close minus buffer) reached with the position open."""
    KILL_SWITCH = "KILL_SWITCH"
    """The kill switch was engaged (``aurum kill`` or a KILL file in the data directory)."""
    END_OF_DATA = "END_OF_DATA"
    """A backtest ended with the position still open (should not happen with force-flat)."""
