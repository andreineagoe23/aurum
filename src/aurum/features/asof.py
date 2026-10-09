"""As-of joins on bar close time (MASTER_PROMPT §2, ADR-0003).

A row of a lower timeframe (e.g. an M5 bar that closed at 10:35) may only be combined with
higher-timeframe bars that had closed by then (the H1 bar that closed at 10:00). Joining on
open time would leak the forming H1 bar; this module joins on ``close_time`` only.
"""

import polars as pl


def asof_join(base: pl.DataFrame, higher: pl.DataFrame, *, suffix: str) -> pl.DataFrame:
    """Attach to every ``base`` row the newest ``higher`` row with
    ``higher.close_time <= base.close_time``. ``higher`` columns get ``suffix`` (its close time
    becomes ``close_time{suffix}``); rows with no such bar get nulls.

    Both frames need a ``close_time`` column sorted ascending (checked).
    """
    for name, df in (("base", base), ("higher", higher)):
        if "close_time" not in df.columns:
            raise ValueError(f"{name} frame has no close_time column")
        if not df["close_time"].is_sorted():
            raise ValueError(f"{name} frame is not sorted by close_time")
    right = higher.rename({c: f"{c}{suffix}" for c in higher.columns})
    return base.join_asof(
        right,
        left_on="close_time",
        right_on=f"close_time{suffix}",
        strategy="backward",
        check_sortedness=False,
        coalesce=False,
    )
