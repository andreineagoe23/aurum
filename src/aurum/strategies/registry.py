"""Build a strategy object from its configs/strategies/<name>.yaml entry."""

from aurum.core.timeframes import Timeframe
from aurum.settings import StrategyConfig
from aurum.strategies.base import Strategy
from aurum.strategies.reference import FixedTimeReference, ReferenceParams

VARIANTS = {"m15": Timeframe.M15, "m5": Timeframe.M5}
"""A: M15 direct entry (decides at M15 closes). B: M15 setup + M5 trigger (M5 closes)."""


def build_strategy(cfg: StrategyConfig, *, variant: str, symbol: str, point: float) -> Strategy:
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
    raise ValueError(f"strategy family {cfg.family!r} is not implemented until Phase 5")
