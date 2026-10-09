"""Risk engine: the only path from a Signal to an approved order.

* ``base``: the ``RiskEngine`` contract and the ``RiskContext`` it decides on.
* ``policy``: ``PolicyRiskEngine`` (Phase 4): sizing with costs, loss limits, lockouts, spread,
  stale-data, news and cutoff gates (ADR-0010). Used by every mode.
* ``fixed``: ``FixedVolumeRisk``, a fixed lot size kept only for the MT5 Strategy Tester parity
  check (the EA trades a fixed volume).

The kill switch and watchdog live in ``aurum.execution.killswitch`` and
``aurum.monitoring.watchdog``.
"""
