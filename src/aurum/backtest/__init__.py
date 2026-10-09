"""Event-driven simulator with no future access (Phase 3).

* ``market``: historical quotes per day, ticks on exact days and M1 bars otherwise (ADR-0007).
* ``broker``: ``SimBroker`` implements ``BrokerPort`` (next-quote fills, costs, stops level,
  worst-case intra-bar rule, rejection hooks).
* ``runner``: chronological wake-ups driving the shared ``Engine``.
* ``accounting``: per-trade cost attribution, equity curve and summary.
* ``pipeline`` / ``storage``: ``aurum backtest run``, run manifest, trial ledger, outputs.
* ``parity``: comparison with the MT5 Strategy Tester (``aurum backtest parity``).
"""
