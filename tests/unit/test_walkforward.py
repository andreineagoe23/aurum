"""Registered walk-forward selection and statistics (aurum.validation.walkforward)."""

import math
from datetime import UTC, datetime, timedelta
from statistics import NormalDist

from aurum.validation.walkforward import (
    assess_dsr,
    defined_sharpe,
    deflated_sharpe,
    max_drawdown,
    neighbours,
    required_neighbours,
    select_point,
    walk_forward,
)

FOLD = datetime(2021, 10, 1, tzinfo=UTC)
BEFORE, AFTER = FOLD - timedelta(days=1), FOLD


def test_selection_uses_prior_trades_minimum_sample_and_exact_tie_breaks() -> None:
    default, a, b, small = (50, 0.5, 1.5), (30, 0.25, 1.0), (30, 0.5, 1.0), (80, 1.0, 2.0)
    entries = {
        a: [(BEFORE, 2)] * 100 + [(AFTER, -9_999)] * 50,  # later trades never count
        b: [(BEFORE, 1), (BEFORE, 3)] * 50,  # 2.0 per trade: an exact tie with a
        default: [(BEFORE, 1)] * 100,
        small: [(BEFORE, 1_000)] * 99,  # best, but below the minimum sample
    }
    pick = select_point(entries, before=FOLD, default=default, min_trades=100)
    assert (pick.point, pick.trades, pick.net_cents, pick.eligible, pick.tied) == (
        a, 100, 200, 3, (a, b),
    )  # fmt: skip
    entries[default] = [(BEFORE, 2)] * 120  # the default joins the tie: it wins
    assert select_point(entries, before=FOLD, default=default, min_trades=100).point == default
    none = select_point(entries, before=FOLD, default=default, min_trades=1_000)
    assert (none.point, none.eligible) == (None, 0)  # insufficient sample, never a guess


def test_drawdown_and_neighbour_counts() -> None:
    assert max_drawdown([5, -3, -4, 10, -2]) == 7  # peak 5 -> -2
    assert max_drawdown([-1, -1]) == 2  # from the starting 0
    axes = [(30, 50, 80), (0.25, 0.5, 1.0), (1.0, 1.5, 2.0)]
    for point, n, need in (
        ((50, 0.5, 1.5), 6, 4), ((30, 0.5, 1.5), 5, 4), ((30, 0.25, 1.5), 4, 3),
        ((30, 0.25, 1.0), 3, 2),
    ):  # fmt: skip
        found = neighbours(point, axes)
        assert (len(found), required_neighbours(n)) == (n, need), point
        assert all(sum(x != y for x, y in zip(f, point, strict=True)) == 1 for f in found)


def test_deflated_sharpe_matches_the_registered_formula() -> None:
    returns = [0.001, -0.002, 0.003, 0.0, 0.002, -0.001, 0.004, -0.003, 0.001, 0.002]
    trials = [0.05, -0.02, 0.10, 0.01, -0.07]
    d = deflated_sharpe(returns, trials, 54)
    mean = sum(returns) / 10
    sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / 9)
    assert math.isclose(d.sr, mean / sd)
    v = sum((s - sum(trials) / 5) ** 2 for s in trials) / 4
    z = NormalDist().inv_cdf
    sr0 = math.sqrt(v) * (
        0.4227843350984671 * z(1 - 1 / 54) + 0.5772156649015329 * z(1 - 1 / (54 * math.e))
    )
    assert math.isclose(d.sr0, sr0)
    expected = NormalDist().cdf(
        (d.sr - sr0) * 3 / math.sqrt(1 - d.skew * d.sr + (d.kurtosis - 1) / 4 * d.sr**2)
    )
    assert math.isclose(d.dsr, expected)
    assert d.t == 10
    assert 0 < d.dsr < 1


def test_walk_forward_selects_on_prior_trades_only_and_never_falls_back() -> None:
    f1, f2, f3 = (datetime(y, 10, 1, tzinfo=UTC) for y in (2021, 2022, 2023))
    day = timedelta(days=1)
    default, a, b = (2, 1.5), (2, 1.0), (4, 2.0)
    entries = {
        default: [(f1 - day, 100), (f1 + day, 200), (f1 + 2 * day, 300), (f2 + 5 * day, 50)],
        # The trade at exactly f1 is not prior to fold 1; a huge fold-2 trade never selects.
        a: [(f1 - day, 100), (f1, 400), (f1 + 3 * day, 100), (f2 + day, 999_999)],
        b: [(f1 - day, 1_000)] * 2,  # best before fold 1, but below the minimum sample
    }
    (s1, t1), (s2, t2) = walk_forward(entries, [(f1, f2), (f2, f3)], default=default, min_trades=3)
    # Fold 1: no point has 3 prior trades -> no selection, no OOS trade, no fallback.
    assert (s1.point, s1.eligible, t1) == (None, 0, [])
    # Fold 2: default and a tie exactly at 200 cents per trade over 3 prior trades each.
    assert (s2.point, s2.tied, s2.trades, s2.net_cents) == (default, (a, default), 3, 600)
    assert t2 == [(f2 + 5 * day, 50)]  # the selected point's own fold-2 trades only
    sample_ok = all(len(t) >= 30 for t in (t1, t2)) and len(t1) + len(t2) >= 200
    assert not sample_ok  # gate 1: an empty fold is an insufficient sample


def test_dsr_gate_keeps_undefined_trials_in_n_and_fails_closed() -> None:
    returns = [0.001, -0.002, 0.003, 0.0, 0.002, -0.001, 0.004, -0.003, 0.001, 0.002]
    for bad in ([0.01], [0.0] * 5, [0.1] * 3, [0.01, math.nan], [0.01, math.inf, 0.0]):
        assert defined_sharpe(bad) is None, bad
    trials: dict[str, float | None] = {f"tp{i:02d}": 0.01 * ((i * 7) % 11 - 5) for i in range(54)}
    trials |= {f"orb{i:02d}": 0.004 * i - 0.02 for i in range(12)}
    trials["tp13"] = trials["orb05"] = None  # e.g. no exit in folds 1-4
    ok = assess_dsr(returns, trials, n=66, threshold=0.5)
    defined = [s for s in trials.values() if s is not None]
    assert ok.result == deflated_sharpe(returns, defined, 66)  # V over the 64 defined only
    assert (ok.n, ok.v_sample, ok.undefined) == (66, 64, ("orb05", "tp13"))
    assert ok.passed == (ok.result.dsr >= 0.5)
    strict = assess_dsr(returns, trials, n=66, threshold=0.999)
    assert (strict.passed, strict.result) == (False, ok.result)
    one_left = dict.fromkeys(trials, None) | {"tp00": 0.1}
    for case, (cand, srs, n, threshold), why in (
        ("N is not every trial", (returns, trials, 65, 0.95), "N = 65"),
        ("N below 2", (returns, {"x": 0.1}, 1, 0.95), "N = 1"),
        ("candidate SR undefined", ([0.0] * 10, trials, 66, 0.95), "candidate"),
        ("candidate non-finite", ([*returns, math.nan], trials, 66, 0.95), "candidate"),
        ("fewer than 2 defined SRs", (returns, one_left, 66, 0.95), "V is undefined: 1"),
        ("no defined SR", (returns, dict.fromkeys(trials), 66, 0.95), "V is undefined: 0"),
        ("non-finite trial SR", (returns, trials | {"tp00": math.inf}, 66, 0.95), "not finite"),
        ("invalid threshold", (returns, trials, 66, math.nan), "threshold"),
    ):
        got = assess_dsr(cand, srs, n=n, threshold=threshold)
        assert (got.passed, why in got.reason) == (False, True), (case, got.reason)
