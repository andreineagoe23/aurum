"""Indicators over completed bars (price units). Callers pass arrays from a ``MarketView``, so
only bars with ``close_time <= now`` ever reach these functions."""

import math

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


def true_range(high: FloatArray, low: FloatArray, close: FloatArray) -> FloatArray:
    """True range per bar from the second bar on: max(high, prev close) - min(low, prev close)."""
    prev = close[:-1]
    return np.maximum(high[1:], prev) - np.minimum(low[1:], prev)


def atr(high: FloatArray, low: FloatArray, close: FloatArray, period: int) -> float | None:
    """Wilder's average true range of the newest bar, or None with fewer than ``period + 1``
    bars. Seeded with the mean of the first ``period`` true ranges, then
    ``atr = (atr x (period - 1) + tr) / period``."""
    if period < 1:
        raise ValueError("period must be >= 1")
    if len(close) < period + 1:
        return None
    tr = true_range(high, low, close)
    value = float(tr[:period].mean())
    for x in tr[period:]:
        value = (value * (period - 1) + float(x)) / period
    return value


def ema_last(values: FloatArray, period: int) -> float | None:
    """EMA of the newest value: seeded with the simple mean (``math.fsum``) of the first
    ``period`` values, then ``e = e + 2/(period+1) x (x - e)`` for each later value, in order.
    None with fewer than ``period`` values. Callers choose the window (e.g. the trend_pullback
    spec's 500 bars), which makes the value independent of how much history is loaded."""
    if period < 1:
        raise ValueError("period must be >= 1")
    if len(values) < period:
        return None
    xs = values.tolist()
    alpha = 2 / (period + 1)
    e = math.fsum(xs[:period]) / period
    for x in xs[period:]:
        e = e + alpha * (x - e)
    return e
