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


class ContextMismatchError(LedgerError):
    """A grid batch or retry would continue under another frozen evaluation context."""


class SealedPeriodError(AurumError):
    """A research backtest would read the sealed final-test period without its one-time,
    journalled opening (docs/VALIDATION_PROTOCOL.md)."""


class EvaluationFailedError(AurumError):
    """A backtest attempt failed after it was recorded; the ledger keeps its stage and error."""

    def __init__(self, attempt_id: str, stage: str, cause: BaseException) -> None:
        super().__init__(f"attempt {attempt_id} failed at {stage}: {type(cause).__name__}: {cause}")
        self.attempt_id = attempt_id
        self.stage = stage


class ArtifactsPendingError(AurumError):
    """A trial and its success are recorded, but the run's files were not published; they stay
    staged for ``aurum backtest recover`` (the evaluation is never run again)."""

    def __init__(self, attempt_id: str, trial_id: str, run_id: str, cause: BaseException) -> None:
        super().__init__(
            f"attempt {attempt_id} recorded trial {trial_id}, but run {run_id} was not "
            f"published ({type(cause).__name__}: {cause}); run `aurum backtest recover`"
        )
        self.attempt_id = attempt_id
        self.trial_id = trial_id
        self.run_id = run_id
