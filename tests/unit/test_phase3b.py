"""Phase 3b trigger (owner decision 2026-10-10, ADR-0008/0009)."""

import pytest

from aurum.validation.phase3b import (
    BestCaseReplayMissingError,
    IntrabarPolicy,
    IntrabarRun,
    decide_phase_3b,
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

    s = decide_phase_3b(run(WORST, -0.01, flagged=101), run(BEST, 0.004)).summary()
    assert s["worst_case_share"] == 0.0101
    assert (s["net_sign_worst"], s["net_sign_best"]) == (-1, 0)
    assert (s["verdict_worst"], s["verdict_best"]) == ("pass", "pass")
    assert (s["share_triggers"], s["sign_triggers"], s["verdict_triggers"]) == (True, True, False)


def test_phase_3b_fails_closed_without_the_best_case_replay() -> None:
    with pytest.raises(BestCaseReplayMissingError, match="1 flagged trades"):
        decide_phase_3b(run(WORST, 50.0, flagged=1), None)
    # Nothing flagged: no assumption decided anything, the worst-case run is also the best.
    clean = decide_phase_3b(run(WORST, 50.0), None)
    assert clean.best is clean.worst
    assert clean.passed
    with pytest.raises(ValueError, match="expected best"):
        decide_phase_3b(run(WORST, 50.0, flagged=1), run(WORST, 60.0))
    with pytest.raises(ValueError, match="expected worst"):
        decide_phase_3b(run(BEST, 50.0), run(BEST, 60.0))
    with pytest.raises(ValueError, match=r"not in 0\.\.10"):
        run(WORST, 0.0, trades=10, flagged=11)
