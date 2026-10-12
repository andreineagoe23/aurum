"""Sealed final-test period guard (owner decision D1, 2026-10-10; docs/VALIDATION_PROTOCOL.md).

No backtest reads data dated inside ``backtest.sealed_period``:

* a request whose trading dates overlap it is refused;
* the loader refuses, before reading, any file dated inside it (warm-up, the extra end day);
* a run ending just before it loads data only up to the day before it.

There are exactly two journalled ways in. Both are recorded in the trial ledger **before** any
data is read, so a crash still counts.

* **Opening** (``open_sealed`` with who and why): for a research strategy with an explicit
  ``status: frozen`` and a spec path, once per strategy and variant (``sealed_openings``,
  UNIQUE). G-B checks eligibility before snapshot/data reads and again before journalling.
* **Parity** (``parity``): the MT5 Strategy Tester comparison of the one strategy and variant
  named in ``sealed_period.parity_exemption``, with trading dates inside its window, in the
  tester context (fixed volume, zero latency, zero slippage) and with no calendar warm-up. The
  loader still refuses every sealed date outside the window. Each access is journalled in
  ``parity_accesses``, apart from research trials and openings. It never spends an opening.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from aurum.core.errors import SealedPeriodError
from aurum.ledger.trial_ledger import TrialLedger
from aurum.ports.clock import ClockPort
from aurum.settings import AppConfig, StrategyConfig

DAY = timedelta(days=1)


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
    def risk(self) -> str: ...
    @property
    def latency_ms(self) -> int | None: ...
    @property
    def slippage_points(self) -> float | None: ...
    @property
    def open_sealed(self) -> bool: ...
    @property
    def opened_by(self) -> str: ...
    @property
    def open_reason(self) -> str: ...
    @property
    def parity(self) -> bool: ...


@dataclass(frozen=True)
class SealedAccess:
    forbidden: tuple[tuple[date, date], ...]
    """Date ranges the loader must refuse (empty only after a research opening)."""
    last_data_day: date | None
    """Upper bound for loaded data (None: the loader's default, ``last + 1``)."""
    warmup_days: int | None
    """Override of ``backtest.warmup_days`` (0 on the parity path), or None."""
    opened: bool
    parity: bool


def _last_day_outside(last: date, forbidden: tuple[tuple[date, date], ...]) -> date | None:
    """``last`` instead of ``last + 1`` when the extra end day would be a forbidden date."""
    nxt = last + DAY
    return last if any(lo <= nxt <= hi for lo, hi in forbidden) else None


def check_opening_status(strategy: StrategyConfig) -> None:
    """G-B: refuse ineligible research openings without reading data or opening a ledger.

    This is not approval to run a backtest and does not change the separate parity path.
    Called both at pipeline preflight (before the pinned snapshot) and by resolve_access.
    """
    if strategy.family == "reference":
        raise SealedPeriodError(
            "the sealed period is opened only for research strategies; the reference "
            "strategy uses --parity inside its approved window"
        )
    if strategy.status != "frozen":
        raise SealedPeriodError(
            f"{strategy.name} has status {strategy.status!r}; "
            "--open-sealed requires status: frozen, not merely a spec path or enabled flag"
        )
    if not strategy.spec:
        raise SealedPeriodError(
            f"{strategy.name} has no approved spec (spec: in its config); "
            "the sealed period is opened only for a frozen candidate"
        )


def resolve_access(
    cfg: AppConfig,
    request: SealedRequest,
    *,
    ledger_file: Path,
    config_hash: str,
    code_version: str | None,
    clock: ClockPort,
) -> SealedAccess:
    if request.last < request.first:  # before anything is journalled
        raise ValueError(f"empty period {request.first}..{request.last}")
    sealed = cfg.backtest.sealed_period
    span = f"{sealed.first}..{sealed.last}"
    whole = ((sealed.first, sealed.last),)
    asked = f"{request.strategy} {request.variant} {request.first}..{request.last}"
    if request.parity:
        return _parity(cfg, request, asked, ledger_file, config_hash, code_version, clock)
    overlaps = sealed.overlaps(request.first, request.last)
    if not request.open_sealed:
        if overlaps:
            raise SealedPeriodError(
                f"{asked} overlaps the sealed final-test period {span}; it is opened once, "
                "with --open-sealed --by --reason, only after the candidate is frozen "
                "(the MT5 parity check uses --parity)"
            )
        return SealedAccess(
            forbidden=whole,
            last_data_day=_last_day_outside(request.last, whole),
            warmup_days=None,
            opened=False,
            parity=False,
        )
    check_opening_status(cfg.strategies[request.strategy])
    if not overlaps:
        raise SealedPeriodError(
            f"--open-sealed, but {asked} does not overlap {span}; "
            "refusing to spend the one-time opening"
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
    return SealedAccess(
        forbidden=(), last_data_day=None, warmup_days=None, opened=True, parity=False
    )


def _parity(
    cfg: AppConfig,
    request: SealedRequest,
    asked: str,
    ledger_file: Path,
    config_hash: str,
    code_version: str | None,
    clock: ClockPort,
) -> SealedAccess:
    sealed = cfg.backtest.sealed_period
    ex = sealed.parity_exemption
    if request.open_sealed:
        raise SealedPeriodError("--parity and --open-sealed are separate paths; use one")
    if ex is None:
        raise SealedPeriodError("no parity_exemption is configured in backtest.yaml")
    if (request.strategy, request.variant) != (ex.strategy, ex.variant):
        raise SealedPeriodError(
            f"--parity is only for {ex.strategy} {ex.variant}, not "
            f"{request.strategy} {request.variant}"
        )
    if not (ex.first <= request.first and request.last <= ex.last):
        raise SealedPeriodError(
            f"--parity dates {request.first}..{request.last} are outside the approved parity "
            f"window {ex.first}..{ex.last}"
        )
    if (request.risk, request.latency_ms, request.slippage_points) != ("fixed", 0, 0):
        raise SealedPeriodError(
            "--parity runs only in the tester context: --risk fixed --latency-ms 0 "
            "--slippage-points 0"
        )
    forbidden = tuple(
        (lo, hi)
        for lo, hi in ((sealed.first, ex.first - DAY), (ex.last + DAY, sealed.last))
        if lo <= hi
    )
    with TrialLedger(ledger_file, clock=clock) as ledger:
        ledger.record_parity_access(
            strategy=request.strategy,
            variant=request.variant,
            first_day=request.first,
            last_day=request.last,
            config_hash=config_hash,
            code_version=code_version,
        )
    return SealedAccess(
        forbidden=forbidden,
        last_data_day=_last_day_outside(request.last, forbidden),
        warmup_days=0,
        opened=False,
        parity=True,
    )
