"""Exception hierarchy. Every Aurum error derives from ``AurumError``."""


class AurumError(Exception):
    """Base class for all Aurum errors."""


class ConfigError(AurumError):
    """A configuration file or environment variable is missing or invalid."""


class NaiveDatetimeError(AurumError, ValueError):
    """A timezone-naive datetime reached code that requires an aware one."""


class ClockWentBackwardsError(AurumError):
    """A component observed time moving backwards."""


class BrokerError(AurumError):
    """A broker adapter failed or returned something unusable."""


class BrokerDisconnectedError(BrokerError):
    """The broker connection is down; the operation was not performed."""


class NoMarketDataError(BrokerError):
    """No quote/bar is available for the requested symbol and time."""


class MT5UnavailableError(BrokerError):
    """The MetaTrader5 package cannot be imported on this machine."""


class SafetyViolationError(AurumError):
    """An action would break one of the non-negotiable safety rules."""


class LiveTradingBlockedError(SafetyViolationError):
    """Order-sending was refused by the live-trading guard."""


class LedgerError(AurumError):
    """The trial ledger rejected an operation."""


class SealedPeriodError(AurumError):
    """A research backtest would read the sealed final-test period without its one-time,
    journalled opening (docs/VALIDATION_PROTOCOL.md)."""
