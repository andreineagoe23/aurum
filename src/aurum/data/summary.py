"""Roll per-day build results up into the resample report (per source and timeframe)."""

from collections import defaultdict
from dataclasses import asdict
from typing import Any

from aurum.data.compare import BarComparison
from aurum.data.pipeline import DayResult

_COUNTERS = (
    "raw_rows",
    "invalid",
    "out_of_order",
    "duplicates",
    "kept",
    "crossed",
    "spread_outliers",
)


def summarize_build(results: list[DayResult]) -> dict[str, Any]:
    by_key: dict[tuple[str, str], BarComparison] = defaultdict(BarComparison)
    tick_vs_broker: dict[str, BarComparison] = defaultdict(BarComparison)
    days_by_source: dict[str, int] = defaultdict(int)
    fidelity: dict[str, list[str]] = defaultdict(list)
    ticks: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(_COUNTERS, 0))
    for r in results:
        source = r.source or "none"
        days_by_source[source] += 1
        for tf, comparison in r.comparisons.items():
            by_key[(source, tf)].add(comparison)
        if r.ticks is not None:
            for name in _COUNTERS:
                ticks[r.ticks.source][name] += getattr(r.ticks, name)
        if r.tick_fidelity is not None:
            fidelity[r.tick_fidelity].append(r.day.isoformat())
        if r.tick_vs_broker is not None and r.ticks is not None:
            tick_vs_broker[r.ticks.source].add(r.tick_vs_broker)
    return {
        "days": len(results),
        "first_day": results[0].day if results else None,
        "last_day": results[-1].day if results else None,
        "days_by_source": dict(days_by_source),
        "tick_fidelity": {
            k: {"days": len(v), "first": v[0], "last": v[-1], "list": v}
            for k, v in sorted(fidelity.items())
        },
        "tick_m1_vs_broker_m1": {k: asdict(v) for k, v in tick_vs_broker.items()},
        "normalization": dict(ticks),
        "comparisons": [
            {"source": source, "timeframe": tf, **asdict(c)}
            for (source, tf), c in sorted(by_key.items())
        ],
    }
