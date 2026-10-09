"""Canonical bars vs the broker's own bars for the same UTC day and timeframe.

Reports every disagreement instead of hiding it: bars only one side has, OHLC differences (in
points), ``tick_count`` vs broker ``tick_volume``, and ``spread_min`` vs the broker's ``spread``
(the bar's minimum spread). For days built from broker M1 the M1 comparison is trivially equal;
their M5/M15/H1 comparison checks the broker's own aggregation.
"""

from dataclasses import dataclass, field

import polars as pl

PRICE_FIELDS = ("open", "high", "low", "close")


@dataclass
class BarComparison:
    canonical_bars: int = 0
    broker_bars: int = 0
    matched: int = 0
    only_canonical: int = 0
    only_broker: int = 0
    ohlc_mismatch: int = 0
    max_diff_points: dict[str, int] = field(default_factory=lambda: dict.fromkeys(PRICE_FIELDS, 0))
    tick_count_mismatch: int = 0
    spread_min_mismatch: int = 0
    worst: list[dict[str, object]] = field(default_factory=list)
    """Up to five bars with the largest OHLC difference."""

    def add(self, other: "BarComparison") -> None:
        for name in (
            "canonical_bars",
            "broker_bars",
            "matched",
            "only_canonical",
            "only_broker",
            "ohlc_mismatch",
            "tick_count_mismatch",
            "spread_min_mismatch",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for k in PRICE_FIELDS:
            self.max_diff_points[k] = max(self.max_diff_points[k], other.max_diff_points[k])
        self.worst = sorted(
            self.worst + other.worst,
            key=lambda w: -int(str(w["max_diff_points"])),
        )[:5]


def compare_bars(canonical: pl.DataFrame, broker: pl.DataFrame, *, point: float) -> BarComparison:
    """Compare one day of canonical bars with the raw broker bars (RAW_BARS) of that day."""
    out = BarComparison(canonical_bars=canonical.height, broker_bars=broker.height)
    if canonical.is_empty() and broker.is_empty():
        return out
    joined = canonical.select("open_time", *PRICE_FIELDS, "tick_count", "spread_min").join(
        broker.select(
            "open_time",
            *(pl.col(c).alias(f"b_{c}") for c in PRICE_FIELDS),
            pl.col("tick_volume").cast(pl.Int64).alias("b_ticks"),
            pl.col("spread").alias("b_spread"),
        ),
        on="open_time",
        how="full",
        coalesce=True,
    )
    both = joined.filter(pl.col("open").is_not_null() & pl.col("b_open").is_not_null())
    out.only_canonical = int(joined["b_open"].is_null().sum())
    out.only_broker = int(joined["open"].is_null().sum())
    out.matched = both.height
    if both.is_empty():
        return out
    diffs = both.with_columns(
        [
            ((pl.col(c) - pl.col(f"b_{c}")).abs() / point).round().cast(pl.Int64).alias(f"d_{c}")
            for c in PRICE_FIELDS
        ]
    ).with_columns(max_diff_points=pl.max_horizontal([f"d_{c}" for c in PRICE_FIELDS]))
    out.ohlc_mismatch = int((diffs["max_diff_points"] > 0).sum())
    out.max_diff_points = {
        c: int(diffs.select(pl.col(f"d_{c}").max()).item()) for c in PRICE_FIELDS
    }
    out.tick_count_mismatch = int((diffs["tick_count"] != diffs["b_ticks"]).sum())
    out.spread_min_mismatch = int((diffs["spread_min"] != diffs["b_spread"]).fill_null(False).sum())
    worst = diffs.filter(pl.col("max_diff_points") > 0).sort(
        ["max_diff_points", "open_time"], descending=[True, False]
    )
    out.worst = [
        {
            "open_time": r["open_time"].isoformat(),
            "max_diff_points": r["max_diff_points"],
            "canonical": [r[c] for c in PRICE_FIELDS],
            "broker": [r[f"b_{c}"] for c in PRICE_FIELDS],
        }
        for r in worst.head(5).iter_rows(named=True)
    ]
    return out
