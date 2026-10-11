"""Registered walk-forward selection and its statistics (Phase 5 strategy specs, section 16).

Pure functions over already-recorded trades; nothing here runs a simulation.

* ``select_point``: the grid point with the highest net expectancy per trade over the trades
  **entered before** a fold starts, among points with at least ``min_trades`` such trades. Ties
  (compared exactly, in integer cents) go to the default point, then to the lexicographically
  smallest point. No eligible point -> None ("insufficient sample").
* ``max_drawdown``: closed-trade drawdown, trades ordered by exit time, cumulative P&L from 0.
* ``neighbours``: the points one grid step away on exactly one parameter.
* ``walk_forward``: the anchored, prior-only fold selections and their OOS trades.
* ``deflated_sharpe``: Bailey & Lopez de Prado (2014) with the spec's daily-return definition;
  ``assess_dsr`` is gate 12 around it, failing closed whenever the DSR is undefined
  (``defined_sharpe``).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import NormalDist

EULER_GAMMA = 0.5772156649015329

Point = tuple[float, ...]


def cents(pnl: float) -> int:
    """Booked P&L in integer cents (trade P&L is booked to the cent)."""
    return round(pnl * 100)


@dataclass(frozen=True)
class Selection:
    point: Point | None
    """None: no point had ``min_trades`` trades before the fold (insufficient sample)."""
    trades: int
    net_cents: int
    eligible: int
    """How many points had enough trades."""
    tied: tuple[Point, ...]
    """Every eligible point sharing the best expectancy (the winner included)."""


def select_point(
    entries: Mapping[Point, Sequence[tuple[datetime, int]]],
    *,
    before: datetime,
    default: Point,
    min_trades: int,
) -> Selection:
    """``entries[point]`` = (entry decision time, net P&L in cents) of each of its trades."""
    stats: dict[Point, tuple[int, int]] = {}
    for point, trades in entries.items():
        prior = [c for t, c in trades if t < before]
        if len(prior) >= min_trades:
            stats[point] = (sum(prior), len(prior))
    if not stats:
        return Selection(None, 0, 0, 0, ())

    def better(a: tuple[int, int], b: tuple[int, int]) -> int:
        # sign of (a_sum / a_n - b_sum / b_n), exactly
        diff = a[0] * b[1] - b[0] * a[1]
        return (diff > 0) - (diff < 0)

    best = next(iter(stats.values()))
    for s in stats.values():
        if better(s, best) > 0:
            best = s
    tied = sorted(p for p, s in stats.items() if better(s, best) == 0)
    winner = default if default in tied else tied[0]
    total, n = stats[winner]
    return Selection(winner, n, total, len(stats), tuple(tied))


Trade = tuple[datetime, int]
"""(entry decision time, net P&L in cents)."""


def walk_forward(
    entries: Mapping[Point, Sequence[Trade]],
    folds: Sequence[tuple[datetime, datetime]],
    *,
    default: Point,
    min_trades: int,
) -> list[tuple[Selection, list[Trade]]]:
    """Anchored walk-forward. Fold ``[start, end)`` uses the point ``select_point`` picks from
    trades entered before ``start`` only, and contributes that point's trades entered in the
    fold, from its own record (no rescaling). No eligible point -> no trades for that fold
    (insufficient sample, never a fallback)."""
    out: list[tuple[Selection, list[Trade]]] = []
    for start, end in folds:
        s = select_point(entries, before=start, default=default, min_trades=min_trades)
        trades = [] if s.point is None else [t for t in entries[s.point] if start <= t[0] < end]
        out.append((s, trades))
    return out


def max_drawdown(pnl_by_exit: Sequence[float]) -> float:
    """Largest fall from a running peak of the cumulative P&L (starting at 0), in the same
    units as the input; the trades must already be ordered by exit time."""
    peak = equity = worst = 0.0
    for pnl in pnl_by_exit:
        equity += pnl
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def neighbours(point: Point, axes: Sequence[Sequence[float]]) -> list[Point]:
    """Grid points one step away from ``point`` on exactly one axis."""
    out: list[Point] = []
    for i, axis in enumerate(axes):
        k = list(axis).index(point[i])
        for j in (k - 1, k + 1):
            if 0 <= j < len(axis):
                out.append((*point[:i], axis[j], *point[i + 1 :]))
    return out


def required_neighbours(n: int) -> int:
    """At least ceil(2/3 x n) of n neighbours must pass."""
    return math.ceil(2 * n / 3)


def sharpe(returns: Sequence[float]) -> float:
    """Per-period Sharpe: mean / sample standard deviation (ddof 1)."""
    n = len(returns)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    return mean / math.sqrt(var)


def moments(returns: Sequence[float]) -> tuple[float, float]:
    """Skewness and non-excess kurtosis (population moments)."""
    n = len(returns)
    mean = sum(returns) / n
    m2 = sum((r - mean) ** 2 for r in returns) / n
    m3 = sum((r - mean) ** 3 for r in returns) / n
    m4 = sum((r - mean) ** 4 for r in returns) / n
    return m3 / m2**1.5, m4 / m2**2


@dataclass(frozen=True)
class Deflated:
    sr: float
    sr0: float
    t: int
    skew: float
    kurtosis: float
    variance: float
    n: int
    dsr: float


def defined_sharpe(returns: Sequence[float]) -> float | None:
    """``sharpe`` where it is defined, else None: fewer than 2 values, a non-finite value or a
    result, or zero sample variance."""
    if len(returns) < 2 or not all(math.isfinite(r) for r in returns):
        return None
    if max(returns) == min(returns):
        return None
    try:
        sr = sharpe(returns)
    except (ZeroDivisionError, ValueError, OverflowError):
        return None
    return sr if math.isfinite(sr) else None


@dataclass(frozen=True)
class DsrAssessment:
    """Gate 12 (spec section 16): fails closed whenever the DSR is undefined."""

    passed: bool
    reason: str
    """Why it failed or is undefined ("" when it passed)."""
    n: int
    """Trials counted in N (defined and undefined)."""
    v_sample: int
    """Trials with a defined SR: the sample V is computed over."""
    undefined: tuple[str, ...]
    """Trials whose SR is undefined: counted in N, left out of V."""
    result: Deflated | None
    """None when the DSR is undefined."""


def assess_dsr(
    candidate: Sequence[float],
    trial_srs: Mapping[str, float | None],
    *,
    n: int,
    threshold: float = 0.95,
) -> DsrAssessment:
    """Gate 12 on the candidate's daily returns against every trial of the search
    (``trial_srs``: trial id -> its daily SR, None where undefined; ``n`` must equal their
    number). Undefined trial SRs stay in N, are listed, and are left out of V. An undefined
    candidate SR, fewer than 2 defined trial SRs (no sample V), any non-finite input or an
    undefined result fails; nothing is substituted."""
    undefined = tuple(sorted(k for k, s in trial_srs.items() if s is None))
    defined = [s for s in trial_srs.values() if s is not None]

    def fail(reason: str, result: Deflated | None = None) -> DsrAssessment:
        return DsrAssessment(False, reason, n, len(defined), undefined, result)

    if n != len(trial_srs) or n < 2:
        return fail(f"N = {n} must equal the {len(trial_srs)} trials listed and be at least 2")
    if not (math.isfinite(threshold) and 0 < threshold < 1):
        return fail(f"threshold {threshold} is not in (0, 1)")
    if not all(math.isfinite(s) for s in defined):
        return fail("a trial SR is not finite")
    if len(defined) < 2:
        return fail(f"V is undefined: {len(defined)} defined trial SR(s), at least 2 needed")
    if defined_sharpe(candidate) is None:
        return fail("the candidate's SR is undefined")
    try:
        result = deflated_sharpe(candidate, defined, n)
    except (ZeroDivisionError, ValueError, OverflowError) as exc:
        return fail(f"the DSR is undefined ({type(exc).__name__}: {exc})")
    if not all(math.isfinite(x) for x in (result.sr0, result.dsr, result.skew, result.kurtosis)):
        return fail("the DSR is undefined (non-finite result)", result)
    if result.dsr < threshold:
        return fail(f"DSR {result.dsr:.4f} < {threshold}", result)
    return DsrAssessment(True, "", n, len(defined), undefined, result)


def deflated_sharpe(returns: Sequence[float], trial_srs: Sequence[float], n: int) -> Deflated:
    """DSR of ``returns`` against ``n`` trials whose per-period Sharpe ratios are
    ``trial_srs`` (their sample variance, ddof 1, is V)."""
    sr = sharpe(returns)
    g3, g4 = moments(returns)
    m = sum(trial_srs) / len(trial_srs)
    v = sum((s - m) ** 2 for s in trial_srs) / (len(trial_srs) - 1)
    z = NormalDist().inv_cdf
    sr0 = math.sqrt(v) * ((1 - EULER_GAMMA) * z(1 - 1 / n) + EULER_GAMMA * z(1 - 1 / (n * math.e)))
    t = len(returns)
    dsr = NormalDist().cdf(
        (sr - sr0) * math.sqrt(t - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr**2)
    )
    return Deflated(sr, sr0, t, g3, g4, v, n, dsr)
