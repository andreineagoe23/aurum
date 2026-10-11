"""Build a strategy object from its configs/strategies/<name>.yaml entry."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from aurum.core.timeframes import Timeframe
from aurum.settings import StrategyConfig
from aurum.strategies import opening_range_breakout, trend_pullback
from aurum.strategies.base import Strategy
from aurum.strategies.opening_range_breakout import (
    OpeningRangeBreakout,
    OpeningRangeBreakoutParams,
)
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
    session_at: Callable[[datetime], str | None] | None = None,
    blackout: Callable[[datetime], str | None] | None = None,
    stops_level_points: int = 0,
) -> Strategy:
    """``entries_allowed(t)``: the session calendar's decision-time rule (research strategies
    whose setups look back at earlier decision times need it). ``session_at(t)``: the
    calendar's session at ``t`` when entries are allowed there, else None. ``blackout(t)``:
    the risk engine's news blackout reason at ``t`` (``NewsBlackout.reason_at``)."""
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
    if cfg.family == "opening_range_breakout":
        if session_at is None or blackout is None:
            raise ValueError(
                "opening_range_breakout needs the session calendar (session_at) and the news "
                "blackout (blackout)"
            )
        return OpeningRangeBreakout(
            OpeningRangeBreakoutParams.model_validate(cfg.params),
            symbol=symbol,
            point=point,
            decision_timeframe=VARIANTS[variant],
            session_at=session_at,
            blackout=blackout,
            stops_level_points=stops_level_points,
            strategy_id=cfg.name,
        )
    raise ValueError(
        f"strategy family {cfg.family!r} is not implemented until its spec is approved"
    )


def declared_grid(cfg: StrategyConfig) -> list[dict[str, Any]]:
    """The strategy's pre-registered search grid (from its approved spec), in a fixed order."""
    if cfg.family == "trend_pullback":
        return list(trend_pullback.declared_grid())
    if cfg.family == "opening_range_breakout":
        return list(opening_range_breakout.declared_grid())
    raise ValueError(f"strategy {cfg.name} declares no search grid")
