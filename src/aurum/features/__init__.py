"""Causal feature computation: indicators and multi-timeframe joins.

* ``view``: ``MarketView``, bars with ``close_time <= now`` only (property-tested).
* ``asof``: as-of multi-timeframe joins keyed on bar close time.
* ``indicators``: ATR (Phase 4 spread gate).

TODO(Phase 5): EMA, session VWAP, opening range, structure levels.
"""
