"""Registered walk-forward selection and statistics (aurum.validation.walkforward)."""

import math
from datetime import UTC, datetime, timedelta
from statistics import NormalDist

from aurum.validation.walkforward import (
    deflated_sharpe,
    max_drawdown,
    neighbours,
    required_neighbours,
    select_point,
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
