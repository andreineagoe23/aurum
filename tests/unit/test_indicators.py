import numpy as np
import pytest

from aurum.features.indicators import atr, true_range


def arr(*xs: float) -> np.ndarray:  # type: ignore[type-arg]
    return np.array(xs, dtype=np.float64)


def test_true_range_uses_the_previous_close_on_gaps() -> None:
    # Bar 2 gaps up from close 10 to a 12-13 range: TR 13 - 10 = 3. Bar 3: 12.0-12.5 after a
    # 12.5 close: TR 0.5.
    tr = true_range(arr(11, 13, 12.5), arr(9, 12, 12.0), arr(10, 12.5, 12.2))
    assert tr.tolist() == pytest.approx([3.0, 0.5])


def test_atr_is_wilder_smoothed_and_needs_period_plus_one_bars() -> None:
    high, low, close = arr(11, 12, 13, 14), arr(9, 10, 11, 12), arr(10, 11, 12, 13)
    assert atr(high, low, close, 3) == pytest.approx(2.0)  # every TR is 2
    assert atr(high[:3], low[:3], close[:3], 3) is None
    high2, low2, close2 = arr(11, 12, 13, 14, 20), arr(9, 10, 11, 12, 12), arr(10, 11, 12, 13, 19)
    assert atr(high2, low2, close2, 3) == pytest.approx((2.0 * 2 + 8.0) / 3)
    with pytest.raises(ValueError, match="period"):
        atr(high, low, close, 0)
