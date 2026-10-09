"""Raw ticks -> normalized ticks (one UTC day at a time). Pure and deterministic.

Rules, in order (each one counted in ``NormalizeStats``):

1. **Invalid rows dropped:** bid or ask missing, not finite, or <= 0. They cannot be priced.
2. **Out-of-order rows counted, then stably sorted** by time. Rows with the same millisecond keep
   their received order.
3. **Consecutive exact duplicates dropped:** a row identical in every raw field to the row
   before it is a re-delivery. Identical rows that are *not* adjacent (A, B, A in one ms) are
   real quote flicker and are kept. Measured on Fusion history: 0 adjacent, 27 non-adjacent
   in 1.77M ticks.
4. **Spread** in integer points: ``round((ask - bid) / point)``.
5. **Flags, rows kept:** ``crossed`` (ask < bid) and ``spread_outlier`` (spread > factor x the
   day's median spread). Data QA reports them; the risk engine refuses to trade on them.

Stale quotes are a property of the time between ticks, so data QA measures them, not this step.
"""

from dataclasses import dataclass

import polars as pl

from aurum.data.schemas import NORMALIZED_TICKS

RAW_FIELDS = ["time", "bid", "ask", "last", "volume", "flags", "volume_real"]


@dataclass(frozen=True)
class NormalizeStats:
    source: str
    raw_rows: int
    invalid: int
    out_of_order: int
    duplicates: int
    kept: int
    crossed: int
    spread_outliers: int
    median_spread_points: float | None


def normalize_ticks(
    raw: pl.DataFrame, *, point: float, source: str, outlier_factor: float
) -> tuple[pl.DataFrame, NormalizeStats]:
    """``raw`` in RAW_TICKS layout (received order) -> NORMALIZED_TICKS frame + stats."""
    valid_price = (
        pl.col("bid").is_not_null()
        & pl.col("ask").is_not_null()
        & pl.col("bid").is_finite()
        & pl.col("ask").is_finite()
        & (pl.col("bid") > 0)
        & (pl.col("ask") > 0)
    )
    df = raw.select(RAW_FIELDS).with_row_index("_row")
    valid = df.filter(valid_price)
    out_of_order = int(
        valid.select((pl.col("time") < pl.col("time").cum_max().shift(1)).sum()).item() or 0
    )
    ordered = valid.sort(["time", "_row"])
    same_as_previous = pl.all_horizontal(
        [pl.col(c).eq_missing(pl.col(c).shift(1)) for c in RAW_FIELDS]
    )
    deduped = ordered.filter(~same_as_previous.fill_null(False))
    out = deduped.with_columns(
        spread_points=((pl.col("ask") - pl.col("bid")) / point).round().cast(pl.Int32),
    )
    median = out["spread_points"].median() if out.height else None
    median_value = float(median) if isinstance(median, int | float) else None
    threshold = (median_value or 0.0) * outlier_factor
    out = out.with_columns(
        crossed=pl.col("ask") < pl.col("bid"),
        spread_outlier=(pl.col("spread_points") > threshold)
        if median_value and median_value > 0
        else pl.lit(False),
        source=pl.lit(source),
    ).select(NORMALIZED_TICKS.names)
    stats = NormalizeStats(
        source=source,
        raw_rows=raw.height,
        invalid=df.height - valid.height,
        out_of_order=out_of_order,
        duplicates=ordered.height - deduped.height,
        kept=out.height,
        crossed=int(out["crossed"].sum()),
        spread_outliers=int(out["spread_outlier"].sum()),
        median_spread_points=median_value,
    )
    return out, stats
