"""Phase 3b trigger (owner decision 2026-10-10, ADR-0008/0009)."""

import math
from decimal import Decimal

import pytest

from aurum.validation.phase3b import (
    BestCaseReplayMissingError,
    IntrabarPolicy,
    IntrabarRun,
    Phase3bDecision,
    decide_phase_3b,
    net_sign,
    to_cents,
)


def run(
    policy: IntrabarPolicy,
    net: float,
    *,
    passed: bool = True,
    trades: int = 10_000,
    flagged: int = 0,
) -> IntrabarRun:
    return IntrabarRun(f"{policy}-run", policy, trades, flagged, net, passed)


WORST, BEST = IntrabarPolicy.WORST, IntrabarPolicy.BEST


def test_phase_3b_trigger() -> None:
    cases = [
        # name, worst run, best run, required
        ("nothing triggers", run(WORST, 50.0, flagged=50), run(BEST, 80.0), False),
        ("no flag, replay supplied", run(WORST, 50.0), run(BEST, 50.0), False),
        ("share exactly 1.00%", run(WORST, 50.0, flagged=100), run(BEST, 90.0), False),
        ("share 1.01%", run(WORST, 50.0, flagged=101), run(BEST, 90.0), True),
        ("sign flips", run(WORST, -0.01, flagged=1), run(BEST, 0.01), True),
        ("0.00 vs negative", run(WORST, -0.01, flagged=1), run(BEST, 0.004), True),
        ("both round to 0.00", run(WORST, -0.004, flagged=1), run(BEST, 0.004), False),
        ("verdict flips", run(WORST, 50.0, passed=False, flagged=1), run(BEST, 60.0), True),
    ]
    for name, worst, best, required in cases:
        decision = decide_phase_3b(worst, best)
        assert decision.phase_3b_required is required, name
        # No override: a candidate needing Phase 3b never passes, whatever its verdicts.
        assert decision.passed is (worst.passed and not required), name
        summary = decision.summary()
        assert summary["phase_3b_required"] is required, name
        assert (summary["worst_run_id"], summary["best_run_id"]) == ("worst-run", "best-run")
        # A supplied replay is never the reused worst run; it was needed iff a trade was flagged.
        assert summary["best_run_reused"] is False, name
        assert summary["best_case_replay_required"] is (worst.worst_case_trades > 0), name

    s = decide_phase_3b(run(WORST, -0.01, flagged=101), run(BEST, 0.004)).summary()
    assert s["worst_case_share"] == 0.0101
    assert (s["net_pnl_worst"], s["net_pnl_best"]) == (-0.01, 0.0)
    assert str(s["net_pnl_best"]) == "0.0"
    assert (s["net_sign_worst"], s["net_sign_best"]) == (-1, 0)
    assert (s["verdict_worst"], s["verdict_best"]) == ("pass", "pass")
    assert (s["share_triggers"], s["sign_triggers"], s["verdict_triggers"]) == (True, True, False)


def test_net_pnl_is_quantized_to_cents_half_up() -> None:
    below = math.nextafter(0.005, 0.0)  # 0.004999999999999999, just under half a cent
    cases = [
        # net P&L, cents, sign. Binary round(x, 2) gives 2.67, 0.12 and 1.0 for the first three.
        (2.675, "2.68", 1),
        (0.125, "0.13", 1),
        (1.005, "1.01", 1),
        (0.005, "0.01", 1),
        (-0.005, "-0.01", -1),
        (-2.675, "-2.68", -1),
        (below, "0.00", 0),
        (-below, "0.00", 0),  # never -0.00
        (2.6749999, "2.67", 1),
        (-2.6749999, "-2.67", -1),
    ]
    for net, cents, sign in cases:
        assert str(to_cents(net)) == cents, net
        assert to_cents(net) == Decimal(cents), net
        assert net_sign(net) == sign, net
        s = decide_phase_3b(run(WORST, net), None).summary()
        assert (str(s["net_pnl_worst"]), s["net_sign_worst"]) == (str(float(cents)), sign), net


def test_phase_3b_fails_closed_without_the_best_case_replay() -> None:
    with pytest.raises(BestCaseReplayMissingError, match="1 flagged trades"):
        decide_phase_3b(run(WORST, 50.0, flagged=1), None)
    # Nothing flagged: no assumption decided anything, the worst-case run is also the best.
    clean = decide_phase_3b(run(WORST, 50.0), None)
    assert clean.best is clean.worst
    assert clean.passed
    s = clean.summary()
    assert (s["best_case_replay_required"], s["best_run_reused"]) == (False, True)
    flagged = run(WORST, 50.0, flagged=1)
    with pytest.raises(ValueError, match="stands in for the best case"):
        Phase3bDecision(flagged, flagged, best_run_reused=True)
    with pytest.raises(ValueError, match="expected best"):
        decide_phase_3b(run(WORST, 50.0, flagged=1), run(WORST, 60.0))
    with pytest.raises(ValueError, match="expected worst"):
        decide_phase_3b(run(BEST, 50.0), run(BEST, 60.0))
    with pytest.raises(ValueError, match=r"not in 0\.\.10"):
        run(WORST, 0.0, trades=10, flagged=11)
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match=r"worst-run: net_pnl .* is not a finite amount"):
            run(WORST, bad)
