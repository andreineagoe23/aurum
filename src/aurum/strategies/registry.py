"""Build a strategy object from its configs/strategies/<name>.yaml entry."""

from collections.abc import Callable
from datetime import datetime

from aurum.core.timeframes import Timeframe
from aurum.settings import StrategyConfig
from aurum.strategies.base import Strategy
from aurum.strategies.reference import FixedTimeReference, ReferenceParams
from aurum.strategies.trend_pullback import TrendPullback, TrendPullbackParams

VARIANTS = {"m15": Timeframe.M15, "m5": Timeframe.M5}
"""A: M15 direct entry (decides at M15 closes). B: M15 setup + M5 trigger (M5 closes)."""


def build_strategy(
    cfg: StrategyConfig,
    *,
    variant: str,
    symbol: str,
    point: float,
    entries_allowed: Callable[[datetime], bool] | None = None,
    stops_level_points: int = 0,
) -> Strategy:
    """``entries_allowed(t)``: the session calendar's decision-time rule (research strategies
    whose setups look back at earlier decision times need it)."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; expected one of {sorted(VARIANTS)}")
    if variant not in cfg.variants:
        raise ValueError(f"strategy {cfg.name} has no variant {variant!r} ({cfg.variants})")
    if cfg.family == "reference":
        return FixedTimeReference(
            ReferenceParams.model_validate(cfg.params),
            symbol=symbol,
            point=point,
            decision_timeframe=VARIANTS[variant],
            strategy_id=cfg.name,
        )
    if cfg.family == "trend_pullback":
        if entries_allowed is None:
            raise ValueError("trend_pullback needs the session calendar (entries_allowed)")
        return TrendPullback(
            TrendPullbackParams.model_validate(cfg.params),
            symbol=symbol,
            point=point,
            decision_timeframe=VARIANTS[variant],
            entries_allowed=entries_allowed,
            stops_level_points=stops_level_points,
            strategy_id=cfg.name,
        )
    raise ValueError(
        f"strategy family {cfg.family!r} is not implemented until its spec is approved"
    )
