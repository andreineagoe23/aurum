"""Phase 3b trigger: does a candidate's result depend on the intra-bar assumptions?

On bar-only days the simulator resolves unknowable intra-bar orderings against the strategy and
flags each trade it decided (ADR-0009 §5: ``sl_tp_same_bar``, ``entry_bar``, ``close_bar``,
``cancel_bar``). Phase 5 replays every reported candidate twice on the same data, parameters,
costs and risk configuration, changing only the intra-bar policy (``worst``, the default, and
``best``), and links the two runs in the trial ledger. The best case is a **replay**, never an
edit of the worst-case trades: a different ordering can remove a trade (``cancel_bar``), move
its exit, or change which later signals the risk engine accepts.

Owner decision 2026-10-10 (ADR-0008, ADR-0009)::

    phase_3b_required = share_worst > 1%                   (strictly: exactly 1% does not)
                     or sign(net_worst) != sign(net_best)  (cents; 0.00 is its own sign)
                     or verdict_worst != verdict_best

``share_worst`` = flagged trades / total closed trades of the worst-case run. A candidate for
which Phase 3b is required has not passed, whatever its verdicts; there is no override. With
flagged trades and no best-case replay the decision refuses to be made (fail closed).
"""

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import Any

PHASE_3B_SHARE = Fraction(1, 100)
"""Worst-case share above which Phase 3b is required (exactly 1% does not trigger)."""

MONEY_DIGITS = 2


class IntrabarPolicy(StrEnum):
    """How a replay resolves unknowable intra-bar orderings (Phase 5 reporting contract)."""

    WORST = "worst"
    BEST = "best"


class BestCaseReplayMissingError(ValueError):
    """Flagged trades exist but no best-case replay was supplied: the report cannot be made."""


@dataclass(frozen=True, slots=True)
class IntrabarRun:
    """Headline of one replay of a candidate under one intra-bar policy."""

    run_id: str
    policy: IntrabarPolicy
    trades: int
    """Total closed trades (the share's denominator)."""
    worst_case_trades: int
    """Trades flagged by an intra-bar rule (``worst_case`` in the trades table)."""
    net_pnl: float
    """Account currency, after every cost."""
    passed: bool
    """The candidate gate's verdict on this run."""

    def __post_init__(self) -> None:
        if not 0 <= self.worst_case_trades <= self.trades:
            raise ValueError(
                f"{self.run_id}: worst_case_trades {self.worst_case_trades} not in 0..{self.trades}"
            )


def net_sign(net_pnl: float) -> int:
    """-1, 0 or +1 of the net P&L rounded to cents (0.00 is its own sign)."""
    cents = round(net_pnl, MONEY_DIGITS)
    return (cents > 0) - (cents < 0)


@dataclass(frozen=True, slots=True)
class Phase3bDecision:
    worst: IntrabarRun
    best: IntrabarRun
    """The best-case replay, or the worst-case run itself when no trade was flagged (then no
    assumption decided anything and both policies give the same run)."""

    @property
    def worst_case_share(self) -> Fraction:
        return Fraction(self.worst.worst_case_trades, self.worst.trades or 1)

    @property
    def share_triggers(self) -> bool:
        return self.worst_case_share > PHASE_3B_SHARE

    @property
    def sign_triggers(self) -> bool:
        return net_sign(self.worst.net_pnl) != net_sign(self.best.net_pnl)

    @property
    def verdict_triggers(self) -> bool:
        return self.worst.passed != self.best.passed

    @property
    def phase_3b_required(self) -> bool:
        return self.share_triggers or self.sign_triggers or self.verdict_triggers

    @property
    def passed(self) -> bool:
        """The candidate passes only on its worst-case verdict and without Phase 3b."""
        return self.worst.passed and not self.phase_3b_required

    def summary(self) -> dict[str, Any]:
        """The fields every Phase 5 candidate report must show."""
        return {
            "worst_run_id": self.worst.run_id,
            "best_run_id": self.best.run_id,
            "trades": self.worst.trades,
            "worst_case_trades": self.worst.worst_case_trades,
            "worst_case_share": float(self.worst_case_share),
            "net_pnl_worst": round(self.worst.net_pnl, MONEY_DIGITS),
            "net_pnl_best": round(self.best.net_pnl, MONEY_DIGITS),
            "net_sign_worst": net_sign(self.worst.net_pnl),
            "net_sign_best": net_sign(self.best.net_pnl),
            "verdict_worst": "pass" if self.worst.passed else "fail",
            "verdict_best": "pass" if self.best.passed else "fail",
            "share_triggers": self.share_triggers,
            "sign_triggers": self.sign_triggers,
            "verdict_triggers": self.verdict_triggers,
            "phase_3b_required": self.phase_3b_required,
            "passed": self.passed,
        }


def decide_phase_3b(worst: IntrabarRun, best: IntrabarRun | None) -> Phase3bDecision:
    """Apply the trigger to a candidate's worst-case run and its linked best-case replay."""
    if worst.policy is not IntrabarPolicy.WORST:
        raise ValueError(f"{worst.run_id} is a {worst.policy}-case run, expected worst")
    if best is None:
        if worst.worst_case_trades:
            raise BestCaseReplayMissingError(
                f"{worst.run_id}: {worst.worst_case_trades} flagged trades and no best-case "
                "replay; the candidate cannot be reported"
            )
        return Phase3bDecision(worst, worst)
    if best.policy is not IntrabarPolicy.BEST:
        raise ValueError(f"{best.run_id} is a {best.policy}-case run, expected best")
    return Phase3bDecision(worst, best)
