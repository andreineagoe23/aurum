# ADR-0011: Execution safety: intent store, reconciliation, stale intents, kill switch, watchdog

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

MASTER_PROMPT §4 rules 3–6 require a broker-side stop on every trade, no duplicate orders
across restarts, reconciliation on startup and after every action, force-flat that works after
a disconnect or restart, and a kill switch. The owner added three Phase 4 notes:

- reject `OrderIntent`s older than about 60 s;
- ownership means magic 7020261 **and** the "AU" comment prefix;
- `aurum kill` writes the KILL file and flattens immediately in DEMO.

## Decision

1. **Intent store** (`aurum.execution.intents`, SQLite at `<data>/state/intents.sqlite`;
   in memory for backtests). Every intent is recorded with its signal **before** it reaches
   the broker. After a restart the engine reloads every signal, so `max_hold` deadlines and
   invalidation still apply to positions opened by the previous process.
2. **Order manager** (`aurum.execution.orders`) is the engine's only way to send:
   - It never sends an intent older than `broker.max_intent_age_seconds` (60). An order the
     broker already holds under the key is still recognised as a duplicate.
   - A connection failure during the request makes the outcome UNKNOWN. It is recorded, never
     guessed. `MT5Broker` applies the same age rule after its own duplicate lookup, as a
     second line of defence on the real-broker path.
3. **Ownership** everywhere: magic number and "AU" key prefix. `DealRecord` now carries
   `magic`, so deal history (loss limits, streaks) obeys the same rule.
4. **Reconciliation** (`aurum.execution.reconcile`) runs on the first step, after a lost
   connection, while any outcome is unresolved, and after every step that acted. The broker
   is the truth:
   - an owned position the store does not know is adopted (force-flat and kill only);
   - a position without a broker-side stop gets its intent's stop, or is closed;
   - an owned pending order nobody can explain is cancelled;
   - an unresolved intent takes the broker's state, or expires after the age limit. It is
     never resent; the strategy re-decides.
   - While any outcome is unresolved the engine opens nothing.
5. **Disconnects** inside a step are journalled and end the step. The next step reconciles
   first and then acts, including a force-flat whose cutoff passed while offline.
6. **Kill switch**: a `KILL` file in the data dir. While it exists the engine closes Aurum's
   positions, cancels its orders and opens nothing. `aurum kill` writes it and, in DEMO/LIVE
   mode, connects and flattens Aurum's items at once (manual trades untouched).
   `aurum unkill` deletes it.
7. **Watchdog**: the trading loop writes `<data>/state/<process>.heartbeat.json` atomically.
   `aurum watchdog` (OS scheduler) engages the kill switch, and flattens in DEMO, when the
   heartbeat is missing or older than `--max-age-seconds` while the trader should be running:
   from the pre-session start until 30 minutes after the cutoff.

## Consequences

- Fault-injection tests (`tests/fault/test_execution_faults.py`) cover:
  - a reply lost mid-order (no duplicate; the position is found and keeps its timeout);
  - a send that never arrived (expires, then trading resumes);
  - stale quotes;
  - restarts with and without the store;
  - a cutoff during a disconnect;
  - the kill switch;
  - an always-signalling strategy that cannot exceed the limits.
- The live loops that write heartbeats arrive in Phase 7/8. Until then `aurum watchdog` must
  not be scheduled, or it will raise MISSING alarms every trading day.
- Adopted positions have no signal, so no timeout or invalidation applies to them; force-flat
  and the kill switch still do.
