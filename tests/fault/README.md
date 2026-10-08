# Fault-injection tests

Disconnects (including mid-order), stale ticks, restart with an open position,
rejected or partial orders, and session cutoff during a failure (MASTER_PROMPT section 8).
Phase 4 onward. Uses the fake adapter with injected faults; no MT5 required.
Each test asserts that limits hold and force-flat still happens.
