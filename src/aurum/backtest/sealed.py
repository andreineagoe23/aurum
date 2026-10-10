"""Sealed final-test period guard (owner decision D1, 2026-10-10; docs/VALIDATION_PROTOCOL.md).

Applies to research backtests, i.e. every family not in
``backtest.sealed_period.exempt_families``:

* a request whose trading dates overlap the sealed period is refused;
* the loader refuses any data file dated in it (warm-up, the extra end day), before reading;
* a run ending just before the period loads data only up to the day before it.

The single way in is ``open_sealed`` with who and why, for a strategy with an approved spec
(``spec:`` set in its config). It is allowed once per strategy and variant and journalled in the
trial ledger **before** any data is read. A crash after that still counts as the opening.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from aurum.core.errors import SealedPeriodError
from aurum.ledger.trial_ledger import TrialLedger
from aurum.ports.clock import ClockPort
from aurum.settings import AppConfig


class SealedRequest(Protocol):
    @property
    def strategy(self) -> str: ...
    @property
    def variant(self) -> str: ...
    @property
    def first(self) -> date: ...
    @property
    def last(self) -> date: ...
    @property
    def open_sealed(self) -> bool: ...
    @property
    def opened_by(self) -> str: ...
    @property
    def open_reason(self) -> str: ...


@dataclass(frozen=True)
class SealedAccess:
    forbidden: tuple[date, date] | None
    """Dates the loader must refuse (None: exempt family or opened)."""
    last_data_day: date | None
    """Upper bound for loaded data (None: the loader's default, ``last + 1``)."""
    opened: bool


def resolve_access(
    cfg: AppConfig,
    request: SealedRequest,
    *,
    ledger_file: Path,
    config_hash: str,
    code_version: str | None,
    clock: ClockPort,
) -> SealedAccess:
    sealed = cfg.backtest.sealed_period
    strategy = cfg.strategies[request.strategy]
    span = f"{sealed.first}..{sealed.last}"
    if strategy.family in sealed.exempt_families:
        if request.open_sealed:
            raise SealedPeriodError(f"{strategy.family} is exempt; --open-sealed is not needed")
        return SealedAccess(forbidden=None, last_data_day=None, opened=False)
    overlaps = sealed.overlaps(request.first, request.last)
    if not request.open_sealed:
        if overlaps:
            raise SealedPeriodError(
                f"{request.strategy}: {request.first}..{request.last} overlaps the sealed "
                f"final-test period {span}; it is opened once, with --open-sealed --by --reason, "
                "only after the candidate is frozen"
            )
        before = request.last < sealed.first <= request.last + timedelta(days=1)
        return SealedAccess(
            forbidden=(sealed.first, sealed.last),
            last_data_day=sealed.first - timedelta(days=1) if before else None,
            opened=False,
        )
    if not overlaps:
        raise SealedPeriodError(
            f"--open-sealed, but {request.first}..{request.last} does not overlap {span}; "
            "refusing to spend the one-time opening"
        )
    if not strategy.spec:
        raise SealedPeriodError(
            f"{request.strategy} has no approved spec (spec: in its config); "
            "the sealed period is opened only for a frozen candidate"
        )
    with TrialLedger(ledger_file, clock=clock) as ledger:
        ledger.record_sealed_opening(
            strategy=request.strategy,
            variant=request.variant,
            opened_by=request.opened_by,
            reason=request.open_reason,
            first_day=request.first,
            last_day=request.last,
            config_hash=config_hash,
            code_version=code_version,
        )
    return SealedAccess(forbidden=None, last_data_day=None, opened=True)
