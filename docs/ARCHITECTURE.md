# Architecture

Aurum uses a ports-and-adapters (hexagonal) design. Strategy, risk and simulator code never know whether they run in a backtest, shadow mode or demo trading. See `docs/DECISIONS/ADR-0001-ports-and-adapters.md`.

```text
               ┌─────────────── platform-independent core (Mac + Windows) ───────────────┐
MarketEvent ─► │ Feature pipeline (H1 regime │ M15 setup │ M5 trigger, as-of joins)       │
               │        ↓                                                                 │
               │ Strategy  → Signal                                                       │
               │        ↓                                                                 │
               │ Risk engine (independent, cannot be bypassed) → RiskDecision             │
               │        ↓                                                                 │
               │ OrderIntent (idempotency key) → BrokerPort                               │
               └─────────────────────────────────────────────────────────────────────────┘
                                          │
          ┌───────────────────────┬───────┴──────────────┬──────────────────────────┐
     FakeBroker (Mac)      SimBroker (backtest)     ShadowBroker              MT5Broker (Windows)
     unit tests            bid/ask tick replay      live data, no orders      real demo orders
```

## Phase 0 modules

### `aurum.core` (imports nothing from other aurum packages)

| Module | Contents |
|---|---|
| `enums.py` | `Mode`, `Side`, `OrderType`, `AccountTradeMode`, `SessionPhase`, `ExecutionStatus`, `DealEntry` |
| `timeframes.py` | `Timeframe` with `bar_open_time`, `bar_close_time`, `last_completed_bar`; `BarSpan` |
| `timeutil.py` | `UtcDatetime` pydantic type, `to_utc`. Naive datetimes are rejected. |
| `clock.py` | `SystemClock`, `ManualClock` (injectable, for tests) |
| `models.py` | Frozen pydantic v2 domain models (see `docs/DATA_DICTIONARY.md`) |
| `safety.py` | Pure live-trading guard. Order sending is allowed only in DEMO or LIVE mode. A non-demo account additionally needs `AURUM_MODE=LIVE`, the exact acknowledgement phrase, and `configs/promotion.yaml` with `live_enabled: true` and a sign-off date in the past. |
| `errors.py` | Exception types |

### `aurum.ports`

- `broker.py`: `BrokerPort` Protocol (connect, account_info, symbol_info, latest_tick, bars, ticks, positions, orders, check_order, send_order, modify_position, close_position, cancel_order, history_deals).
- `clock.py`: `ClockPort`.
- `datastore.py`: `DataStorePort` (Phase 2).

### `aurum.adapters`

- `fake.broker.FakeBroker`: in-memory, deterministic fills at bid (sells) and ask (buys).
- `mt5.loader`: `is_available()` and lazy `load_mt5()`.
- `mt5.broker.MT5Broker`: the only place `MetaTrader5` is imported, and only inside functions. Connect with retry/backoff; UTC bars, ticks, positions, orders and deals; guarded send/modify/close/cancel. Every write re-reads `account_info` and runs the safety guard first. Idempotency uses the order comment ([ADR-0005](DECISIONS/ADR-0005-idempotency-via-order-comment.md)).
- `mt5.servertime`: the only server-clock ↔ UTC conversion (New York + 7h, [ADR-0004](DECISIONS/ADR-0004-mt5-server-time.md)), plus the offset check run on every connect.
- `mt5.snapshot`: immutable symbol-spec snapshots in `<data_dir>/specs/<symbol>/`.
- `storage`: Parquet/DuckDB store (Phase 2).

### Other packages

- `aurum.settings`: pydantic-settings reading environment variables and YAML configs. Extra keys are forbidden; invalid config fails loudly.
- `aurum.sessions`:
  - `clock.py`: `SessionClock`, `DaySchedule`. Converts London wall-clock times to UTC per day with `zoneinfo`.
  - `state_machine.py`: `SessionStateMachine` emits transitions. It emits an initial transition on its first update so a restart re-triggers force-flat.
- `aurum.ledger.trial_ledger`: append-only SQLite. UPDATE and DELETE are blocked by triggers; each row has a content hash.
- `aurum.monitoring`: `logging.py` (structlog JSON with secret redaction), `doctor.py` (environment checks).
- `aurum.cli`: typer app (`aurum doctor`, placeholders for later commands).
- Later phases: `data`, `features`, `strategies`, `risk`, `execution`, `backtest`, `validation`, `apps`.

## Dependency rules

Enforced by `tests/unit/test_architecture.py`:

- `aurum.core` imports nothing from other `aurum` packages.
- `aurum.strategies` and `aurum.features` may not import adapters, execution, apps or `MetaTrader5`.
- Only `aurum.adapters.mt5` imports `MetaTrader5`, and never at module top level.

## Order flow invariant

`Signal -> RiskDecision -> OrderIntent -> BrokerPort`.

- Strategies return Signals only and cannot create orders.
- An `OrderIntent` cannot be constructed without an approved `RiskDecision` embedded in it, and must carry a `stop_loss`.
- Brokers re-check approval and run the safety guard before any write (defense in depth).

## Session phases

`CLOSED -> PRE_SESSION -> OPEN <-> NO_NEW_ENTRIES -> EXIT_ONLY -> FORCE_FLAT`, then back to `CLOSED` at local midnight.

- New entries are allowed only in `OPEN`.
- `must_be_flat` is true in `CLOSED`, `PRE_SESSION` and `FORCE_FLAT`.
- Times come from `configs/sessions.yaml` and are DST-aware.
- Exact boundaries: [ADR-0002](DECISIONS/ADR-0002-session-phases.md).

## Time conventions

- UTC everywhere internally; London time is used only to define sessions.
- Bars are keyed by open time and visible at close time (`open + timeframe`).
- Higher timeframes join lower ones with an as-of join on close time.
- Details: [ADR-0003](DECISIONS/ADR-0003-time-conventions.md). The MT5 server clock (New York + 7h) is converted inside the adapter: [ADR-0004](DECISIONS/ADR-0004-mt5-server-time.md).

## Modes

| Mode | Broker adapter | May send orders? |
|---|---|---|
| `BACKTEST` | Simulated broker (Phase 3); `FakeBroker` in unit tests | No real orders; simulated fills only |
| `SHADOW` | Shadow broker on live MT5 data | No |
| `DEMO` | `MT5Broker` on a demo account | Yes, demo account only |
| `LIVE` | `MT5Broker` | Blocked by default. Requires the full guard in `aurum.core.safety`. Out of scope for v1. |
