# Architecture

Code map for working in the repo: what each package does, how the main flows run, and which
tests guard what. Decisions and their reasons are in `docs/DECISIONS/` (ADRs); dataset schemas
in `docs/DATA_DICTIONARY.md`; current state in `docs/STATUS.md`.

Ports and adapters ([ADR-0001](DECISIONS/ADR-0001-ports-and-adapters.md)): strategy, risk and
execution code never know whether they run in a backtest, shadow mode or demo trading.

```text
 history (Parquet)                    live MT5 terminal (Windows VM)
        │                                       │
  backtest.loading ─► HistoricalMarket    adapters.mt5.MT5Broker
        │                 │                     │
        │            SimBroker ─────────────────┤  (both implement ports.broker.BrokerPort)
        ▼                 ▼                     ▼
  features.view.MarketView (bars closed by now) ─► Strategy ─► Signal
                                                        │
                       risk (PolicyRiskEngine) ─► RiskDecision
                                                        │
                       execution.Engine ─► OrderManager ─► OrderIntent ─► BrokerPort
```

## Packages (`src/aurum/`)

| Package | Modules → responsibility |
|---|---|
| `core` (imports nothing else from aurum) | `models` frozen pydantic domain models (`Tick`, `Bar`, `Signal`, `RiskDecision`, `OrderIntent`, `SymbolSpec`, `DealRecord`…); `enums` (`Mode`, `SessionPhase`, `ExitReason`, `DealReason`…); `timeframes` (`Timeframe`, `last_completed_bar`); `timeutil` (`UtcDatetime`, `to_utc`); `clock` (`SystemClock`, `ManualClock`); `safety` live-trading guard; `errors` |
| `ports` | Protocols: `BrokerPort`, `ClockPort`, `DataStorePort`, `MarketDataSource` (what ingestion needs from a broker) |
| `adapters.mt5` | The only `MetaTrader5` user, lazily: `broker.MT5Broker` (guarded writes, idempotent send, UTC reads), `servertime` (server clock ↔ UTC, ADR-0004), `frames` (raw arrays → polars), `snapshot` (symbol specs), `tester` (Strategy Tester deals CSV), `loader` |
| `adapters.fake` | `FakeBroker`: in-memory deterministic broker for unit tests |
| `adapters.storage` | `layout.DataLayout`/`RunLayout` (every path), `parquet` (atomic, deterministic writes), `live_ticks` (collector chunks, journal, heartbeat), `specs` (`pinned_spec`: the one snapshot backtests use, by name and sha256) |
| `data` | Phase 2 pipeline: `backfill`, `normalize`, `resample` (ticks → M1 → M5/M15/H1), `pipeline` (canonical bars, tick fidelity ADR-0007), `compare`, `qa`, `market_hours`, `spreads`, `hashes`, `depth`, `schemas` (all column definitions), `reports`, `summary` |
| `sessions` | `clock.SessionClock` (London wall clock → UTC phases, ADR-0002), `calendar.TradingCalendar` (phases + market closures and early closes), `state_machine` (emits on first update → restart flattens), `news` (blackout windows) |
| `features` | `view` (`BarStore`/`MarketView`: completed bars only), `asof` (joins on close time), `indicators` (ATR, windowed EMA) |
| `strategies` | `base` contract (Signals only; `warmup_bars`, `diagnostics`), `reference.FixedTimeReference` (parity/null strategy), `trend_pullback.TrendPullback` (Phase 5 family 1, spec 0.2.0, rejected; fixed rules as constants, only the declared grid as params), `opening_range_breakout.OpeningRangeBreakout` (family 2, spec 0.1.0 frozen; stateless walk of each range window, per-range-date `outcomes()` and status counts), `registry` (config → strategy, declared grids) |
| `risk` | `base` (`RiskEngine`, `RiskContext`), `policy.PolicyRiskEngine` (sizing + every gate, ADR-0010), `fixed.FixedVolumeRisk` (tester parity only) |
| `execution` | `engine.Engine` (the per-step decision loop for every mode), `orders.OrderManager` (only send path), `intents` (SQLite intent store), `reconcile`, `killswitch` (ADR-0011) |
| `backtest` | `market.HistoricalMarket` (ticks or bars per day), `broker.SimBroker` (fills, intra-bar rules, ADR-0009), `runner` (wake-up schedule), `pipeline` (`prepare`: validation + frozen evaluation context → attempt → staged run → trial and success in one transaction → publish; failures kept with their stage; `recover_artifacts`), `grid` (a strategy's declared search grid as one batch frozen to its context; points with a trial never rerun, explicit linked retries), `accounting` (cost attribution, equity, metrics), `parity`, `storage`, `loading` (`plan_market` lists every input file and refuses sealed ones before reading; `read_market`), `sealed` (sealed final-test guard and its one-time journalled opening) |
| `ledger` | `trial_ledger.TrialLedger`: append-only SQLite, content-hashed trial rows; `attempts` (with their evaluation context) + `attempt_outcomes` (every evaluation, failed or unfinished included, retries linked); `grid_batches` (context frozen per batch); one trial per batch point (unique index); `sealed_openings` (once per strategy and variant) and `parity_accesses` journals |
| `monitoring` | `logging` (structlog JSON, secret redaction), `doctor`, `watchdog` (heartbeat → kill switch) |
| `apps` | `collector` (live ticks, Windows); `shadow`, `trader` are Phase 7/8 placeholders |
| `validation` | `phase3b` (Phase 3b trigger on a candidate's worst/best intra-bar runs, ADR-0009 §9); `walkforward` (registered section 16 statistics: prior-only `select_point` and anchored `walk_forward`, closed-trade drawdown, neighbours, `deflated_sharpe`, and `assess_dsr`, gate 12 failing closed when the DSR is undefined) |
| `settings.py` | `.env` + `configs/*.yaml` → validated `Settings`; safety caps live in validators |
| `cli.py` | `aurum` Typer app (below) |

## Dependency rules

Enforced statically by `tests/unit/test_architecture.py`:

- `aurum.core` imports nothing from other `aurum` packages.
- `strategies` and `features` may not import adapters, execution, apps or `ports.broker`.
- `risk` may not import adapters, apps or strategies; `execution` may not import adapters, apps
  or backtest; `sessions` may not import adapters, execution or apps; `backtest` may not import
  `adapters.mt5` or apps.
- Only `aurum.adapters.mt5` imports `MetaTrader5`, never at module top level.

## Main flows

**Engine step** (`execution/engine.py`, same code in every mode), at `now = view.now`:
session update → reconcile (startup, after disconnect, while an outcome is unresolved, after
any action) → kill switch → flat phases close everything (FORCE_FLAT) → outside OPEN cancel
pending entries → per position TIMEOUT / INVALIDATION → in OPEN at a decision-timeframe close:
strategy → risk → `OrderManager.submit`. A disconnect ends the step; the next step retries.

**Backtest** (`aurum backtest run` → `backtest/pipeline.py`): `prepare` validates the request,
resolves sealed access and plans every input file (warm-up included: the strategy's `warmup_bars` and the engine's `HISTORY_BARS`, merged by maximum per timeframe) into the frozen evaluation
context → ledger attempt → read the Phase 2 store → `SimBroker` + `Engine` + risk engine →
`runner.run_backtest` wakes at M5 closes, phase boundaries and `max_hold` deadlines; at each wake
the broker first processes quotes before `t`, then the engine decides on bars closed by `t` →
`accounting` → files staged under `reports/runs/.staging/<run_id>/` → trial + success outcome in
one transaction → renamed to `reports/runs/<run_id>/`. A failed publish leaves the trial and the
staged files (`aurum backtest recover`). Reruns must be byte-identical (`aurum backtest compare`).

**Data** (Phase 2): `aurum data backfill` (raw ticks/bars, immutable) and `aurum data collect`
(live ticks) → `aurum data resample` (normalized ticks, canonical bars; ticks used only on days
whose tick-built M1 equals the broker's) → `aurum data qa --strict` (0 unexplained gaps) →
`aurum data hashes --compare` (determinism gate).

**Order safety chain**: `Signal` → `RiskDecision(approved)` → `OrderIntent.from_signal` (needs
an approved decision for that signal, volume ≤ approved, mandatory stop, key `"AU" + signal.key`)
→ `OrderManager` (records first, refuses intents older than 60 s, lost reply = UNKNOWN, never
resent) → broker re-checks approval and runs `core.safety` on a fresh `account_info()`.

## CLI

`aurum doctor` · `mt5 ping|symbols|bars` · `data depth|backfill|collect|qa|resample|spreads|hashes`
· `backtest run|grid|recover|report|compare|parity` · `ledger list` · `kill` / `unkill` / `watchdog` ·
placeholders (exit 2): `validate walkforward`, `shadow run`, `trade demo`.

## Tests (`tests/`)

| Where | What it guards |
|---|---|
| `unit/test_architecture.py` | Import boundaries above |
| `unit/test_live_guard.py`, `unit/test_mt5_adapter.py` (guard section) | Live trading blocked; every write re-checks a fresh account |
| `unit/test_models.py` | `OrderIntent` invariants (approved decision, volume, stop, key) |
| `unit/test_policy_risk.py`, `property/test_risk_properties.py` | Sizing and every risk gate; risk after rounding ≤ budget |
| `unit/test_execution_core.py`, `unit/test_engine.py` | Order manager, reconciliation, engine behaviour |
| `fault/test_execution_faults.py` | Lost replies, disconnects, restarts, cutoff during disconnect, kill switch (uses `fault/faulty.py::FaultyBroker`) |
| `fault/test_collector.py` | Collector restarts, IPC failures, gaps, no lost or duplicate ticks |
| `replay/test_golden.py` + `replay/scenarios.py` | Exact trades and P&L through the full backtest stack; every bug becomes a scenario |
| `property/test_no_lookahead.py` | Strategies only see bars closed by now; as-of joins on close time |
| `property/test_session_properties.py`, `…timeframe…`, `…servertime…` | DST and time rules swept over years against independent oracles |
| `unit/test_sim_broker.py` | Fill prices, intra-bar worst-case rules, rejections |
| `unit/test_trend_pullback.py` | trend_pullback 0.2.0 rules on exact synthetic bars: equality boundaries, gaps, stop bounds and rounding, M5 window/expiry/replacement/one use/restart, H1 invalidation |
| `unit/test_opening_range_breakout.py` | opening_range_breakout 0.1.0 with the real calendar and blackout: anchors and eligible times per DST regime, early closes, equality boundaries, stop bounds and rounding, blackout instants, data gaps, one use, restart, diagnostic deduplication, the 12 declared points, the spec trace |
| `unit/test_walkforward.py` | Prior-only selection, exact ties, no fallback when no point is eligible; drawdown and neighbours; the DSR formula; gate 12 keeps undefined trial SRs in N and out of V and fails closed (undefined candidate SR or V, fewer than 2 defined SRs, non-finite inputs) |
| `unit/test_backtest_cli.py` | Backtest end to end on a synthetic store; sealed-period refusal and one-time opening; warm-up by bar count, merged with the engine's M15 history by maximum; grid batches frozen to their evaluation context; recorded trials recovered, never re-evaluated; empty periods leave no ledger row |
| `integration/test_mt5_smoke.py` | Real terminal (Windows only; orders need `AURUM_MT5_ORDER_TESTS=1`) |

Helpers: `factories.py` (models), `fake_mt5.py` (fake `MetaTrader5` module), `simdata.py`
(hand-built tapes and markets), `sim_store.py` (a small on-disk Phase 2 store).
