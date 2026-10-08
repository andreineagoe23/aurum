# Project Aurum — Master Build Prompt

> Source of truth for how Aurum is built. Claude re-reads this at the start of every session.
> (Original note: "Copy everything below the line into Claude in Cursor as the first message of the
> project. Then save the same text as docs/MASTER_PROMPT.md inside the repo so Claude can always
> re-read it.")

---

## 0. Who you are and what we are building

You are a senior Python engineer and quantitative-trading systems architect. You are pairing with me
(an experienced full-stack developer) to build **Aurum**, a personal research and automated trading
platform for intraday XAUUSD (gold) on MetaTrader 5.

Aurum is not a "magic AI bot". It is an engineering and research platform designed to:

- Collect clean, replayable gold market data.
- Test simple, explainable intraday strategies honestly (realistic costs, no look-ahead, walk-forward).
- Kill weak ideas early.
- Promote only surviving strategies through shadow mode → demo trading → (much later, optional) a
  tiny live pilot.

Profitability is an empirical question. Your job is to build the system so that it tells the truth,
not to make backtests look good.

**No real money.** Everything runs on a Fusion Markets MT5 Zero demo account until every phase gate
passes. The code must make live trading physically impossible by default (see §4).

## 1. Development environment (important — read carefully)

| Where | What runs there |
|---|---|
| MacBook Air M4 (macOS) | Cursor IDE, Git, all code editing, unit tests, backtests, research notebooks, strategy logic, risk engine, simulator. Python 3.12 managed with uv. |
| Windows 11 ARM64 VM (VMware Fusion on the Mac) | MetaTrader 5 terminal (Fusion Markets), Python 3.12 x64 (emulated), the official MetaTrader5 Python package, the data collector, shadow runner and demo trader. |

Key facts:

- The official `MetaTrader5` Python package is Windows-only and talks to a locally running MT5
  terminal. It cannot be imported on macOS.
- Therefore ~80% of the codebase must be platform-independent and fully runnable/testable on the
  Mac. Only one adapter module (`aurum/adapters/mt5/`) imports MetaTrader5, and it must be imported
  lazily so the package still imports on macOS.
- Declare the dependency with an environment marker:
  `MetaTrader5>=5.0.45; sys_platform == "win32"`
- The repo lives on the Mac at `~/code/aurum` and is either:
  - shared into the VM via VMware Fusion shared folders (appears as `Z:\aurum` or
    `\\vmware-host\Shared Folders\aurum`), or
  - accessed via Cursor Remote-SSH into the VM (OpenSSH Server enabled on Windows), or
  - synced via Git (push on Mac, pull on Windows).
- Virtual environments must never live in the shared folder. On Windows use `C:\venvs\aurum`; on Mac
  use the local `.venv` (git-ignored).
- Data files (`data/`) can be large: on Windows default to `C:\aurum-data\`, on Mac default to
  `./data/`. Make the path configurable via `AURUM_DATA_DIR`.
- All timestamps internally are timezone-aware UTC. Sessions are defined in Europe/London and
  converted with `zoneinfo` (DST-aware). MT5 returns bar/tick times in UTC seconds; never use naive
  datetimes.

Provide scripts for both sides:

- `scripts/mac_setup.sh` — installs uv, creates `.venv`, installs dev deps, runs tests.
- `scripts/windows_setup.ps1` — creates `C:\venvs\aurum` with x64 Python 3.12, installs package +
  MetaTrader5, verifies `import MetaTrader5`, runs `aurum mt5 ping`.
- `docs/VM_SETUP.md` — step-by-step: shared folder, Python x64 install on Windows ARM, MT5 install,
  enabling "Algo Trading", OpenSSH + Cursor Remote-SSH, firewall notes.

## 2. Trading mandate (frozen for version 1)

| Item | Rule |
|---|---|
| Broker / account | Fusion Markets, MT5 Zero account type (raw spread + commission). Demo only. |
| Instrument | XAUUSD (discover the broker's exact symbol name at runtime; do not hard-code). |
| Style | Intraday only. No overnight, no weekend exposure. Every position and pending order is closed/cancelled before the daily force-flat cutoff. |
| Timeframe roles | H1 = regime and directional context. M15 = setup and invalidation. M5 = entry trigger. Ticks/M1 = execution realism only. Each timeframe has exactly ONE responsibility; they are not "three votes". |
| Candles | Only completed candles are used. A decision at 10:35 sees the H1 bar that closed at 10:00, not the forming one. Higher timeframes are joined to lower ones with an as-of join on close time. |
| Sessions (initial) | London morning and London–New York overlap, researched separately. Exact windows in `configs/sessions.yaml`. |
| Exposure | One XAUUSD position at a time. |
| Exits | Broker-side stop loss on every trade + target, setup invalidation, max holding time, or force-flat cutoff — whichever comes first. |
| Costs | Commission on Zero: $4.50 per 1.0 lot round trip ($2.25/side) — keep configurable. Variable spread from real bid/ask. Slippage model configurable. |
| Model policy | Deterministic rules first. ML only later as a meta-filter on validated setups. No deep learning / RL in v1. |

Default provisional risk limits for demo (safety controls, not optimised — in `configs/risk.yaml`):

```yaml
risk_per_trade_pct: 0.25        # of equity
max_open_positions: 1
max_trades_per_day: 3
daily_loss_limit_pct: 1.0
weekly_loss_limit_pct: 2.5
consecutive_loss_cooldown: 2    # losses → lockout for rest of session
max_spread_points: <calibrate from collected data>
max_spread_atr_fraction: 0.15   # spread vs M15 ATR
min_minutes_before_cutoff: 45   # no new entries after this
stale_quote_seconds: 10
```

Default sessions (Europe/London local time, in `configs/sessions.yaml`, editable):

```yaml
timezone: Europe/London
sessions:
  london_morning:   { start: "07:00", end: "11:00" }
  ldn_ny_overlap:   { start: "13:00", end: "16:30" }
no_new_entries_after: "19:30"
force_flat_at: "20:00"
trading_days: [MON, TUE, WED, THU, FRI]
```

## 3. Architecture

Ports-and-adapters (hexagonal). The strategy, risk and simulator code never know whether they are
running in a backtest, shadow mode or demo trading.

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

`BrokerPort` (Protocol) — at minimum: `connect()`, `account_info()`, `symbol_info(symbol)`,
`latest_tick(symbol)`, `bars(symbol, timeframe, start, end)`, `ticks(symbol, start, end)`,
`positions()`, `orders()`, `check_order(intent)`, `send_order(intent)`, `modify_position(...)`,
`close_position(...)`, `cancel_order(...)`, `history_deals(start, end)`.

Shared domain models (Pydantic v2, frozen where possible): `Tick`, `Bar`, `MarketEvent`,
`SessionState`, `Signal`, `OrderIntent`, `RiskDecision`, `ExecutionReport`, `PositionState`,
`AccountSnapshot`, `SymbolSpec`, `TrialRecord`.

Trading modes (enum `Mode`): `BACKTEST`, `SHADOW`, `DEMO`, `LIVE`.

## 4. Non-negotiable safety rules (enforce in code + tests)

1. **Live trading is blocked by default.** `MT5Broker.send_order` must verify
   `account_info().trade_mode == ACCOUNT_TRADE_MODE_DEMO`. If the account is not demo, refuse unless
   all of these are true: `AURUM_MODE=LIVE`, `AURUM_LIVE_ACK="I accept the risk of real money"`, and
   `configs/promotion.yaml` has `live_enabled: true` with a signed-off date. Write tests proving the
   guard.
2. The risk engine is a separate module; strategies return Signals only and cannot create orders
   directly. Test that no code path sends an order without a `RiskDecision(approved=True)`.
3. Every accepted trade has a broker-side stop loss attached at submission.
4. Every `OrderIntent` has an idempotency key; restarts must never duplicate orders (reconcile
   positions/orders/deals on startup and after every action).
5. Force-flat at cutoff works even after disconnect/restart.
6. Kill switch: a file `KILL` in the data dir or `aurum kill` command immediately cancels pending
   orders, closes positions, and locks trading until manually cleared.
7. Never use a completed H1/M15/M5 value before its real close timestamp. Property-test this.
8. Never fill a historical trade at a price before the trigger; buys fill at ask, sells at bid.
9. Record every backtest / parameter trial in the trial ledger. Never report only the best.
10. Never tune against the sealed final test period.
11. Never auto-promote a model or strategy into trading.
12. Secrets (`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`) only in `.env` (git-ignored); provide
    `.env.example`. Never log passwords.

## 5. Tech stack

- Python 3.12, packaging via uv + `pyproject.toml` (src layout, package name `aurum`).
- MetaTrader5 (Windows only, env marker).
- polars (primary) + pandas (where MT5/libraries need it), numpy.
- pyarrow / Parquet for storage, duckdb for analytics queries.
- pydantic v2 + pydantic-settings for config and domain models; YAML configs.
- typer for the CLI (`aurum ...`), rich for terminal output.
- structlog for JSON structured logs.
- pytest, hypothesis (property tests), pytest-cov.
- ruff (lint + format), `mypy --strict` on `aurum/core`, `aurum/risk`, `aurum/backtest`.
- pre-commit hooks.
- Later phases only: scikit-learn, lightgbm, optuna (small, logged searches), mlflow or SQLite trial
  registry, fastapi + a small local dashboard (Next.js or Streamlit) — do not add these in Sprint 1.

## 6. Repository layout

```text
aurum/
├── pyproject.toml
├── README.md
├── CLAUDE.md                      # condensed rules for you (Claude) — create it
├── .cursor/rules/aurum.mdc        # same core rules as Cursor project rules
├── .env.example
├── .gitignore                     # .venv, .env, data/, reports/*, models/*, *.parquet
├── .pre-commit-config.yaml
├── configs/
│   ├── broker.yaml                # fusion_markets_zero: commission, symbol aliases
│   ├── sessions.yaml
│   ├── risk.yaml
│   ├── promotion.yaml             # live_enabled: false
│   └── strategies/
│       ├── trend_pullback.yaml
│       ├── opening_range_breakout.yaml
│       └── vwap_reversion.yaml
├── docs/
│   ├── MASTER_PROMPT.md           # this document
│   ├── PROJECT_CHARTER.md
│   ├── VM_SETUP.md
│   ├── ARCHITECTURE.md
│   ├── DATA_DICTIONARY.md
│   ├── STRATEGY_SPEC_TEMPLATE.md
│   ├── VALIDATION_PROTOCOL.md
│   └── DECISIONS/                 # ADR-0001-*.md, one per architectural decision
├── src/aurum/
│   ├── __init__.py
│   ├── cli.py                     # typer app entrypoint
│   ├── settings.py                # pydantic-settings, loads .env + YAML
│   ├── core/                      # domain models, enums, clock, timeframes, errors
│   ├── ports/                     # BrokerPort, DataStorePort, ClockPort protocols
│   ├── adapters/
│   │   ├── mt5/                   # ONLY place importing MetaTrader5 (lazy import)
│   │   ├── fake/                  # FakeBroker for Mac tests
│   │   └── storage/               # Parquet/DuckDB store
│   ├── data/                      # ingestion, normalisation, resampling, QA checks
│   ├── sessions/                  # session clock + state machine
│   ├── features/                  # indicators, as-of multi-timeframe joins
│   ├── strategies/                # base class + strategy families
│   ├── risk/                      # sizing, limits, lockouts, kill switch
│   ├── execution/                 # order manager, reconciliation, idempotency
│   ├── backtest/                  # event-driven tick/bar simulator, cost models
│   ├── validation/                # walk-forward, purging/embargo, metrics, DSR
│   ├── ledger/                    # trial ledger (SQLite)
│   ├── monitoring/                # health, heartbeats, reports
│   └── apps/
│       ├── collector.py           # Windows: live tick/bar collector
│       ├── shadow.py              # Windows: live decisions, no orders
│       └── trader.py              # Windows: demo trading loop
├── tests/
│   ├── unit/
│   ├── property/
│   ├── replay/                    # golden fixtures → exact trades/P&L
│   ├── integration/               # marked @pytest.mark.mt5 — Windows only, skipped on Mac
│   └── fault/                     # disconnect, stale ticks, restart, cutoff during failure
├── notebooks/                     # research only, never imported by src
├── scripts/
│   ├── mac_setup.sh
│   └── windows_setup.ps1
├── data/                          # git-ignored
├── reports/                       # git-ignored except .gitkeep
└── models/{challenger,champion,archive}/   # git-ignored except .gitkeep
```

CLI commands to expose progressively:

```text
aurum doctor                     # env check: python arch, OS, MT5 importable?, config valid
aurum mt5 ping                   # connect, print terminal/account (demo?) info
aurum mt5 symbols --find gold    # discover XAUUSD alias + spec snapshot
aurum data backfill --tf M1,M5,M15,H1 --ticks --from 2023-01-01
aurum data collect               # live append ticks (Windows)
aurum data qa                    # quality report
aurum data resample              # canonical bars from ticks/M1
aurum backtest run <strategy> --variant m15|m5 --from --to
aurum validate walkforward <strategy>
aurum ledger list
aurum shadow run
aurum trade demo
aurum kill / aurum unkill
```

## 7. Phased development plan

Work one phase at a time. At the end of each phase: all tests green on Mac (and integration tests on
Windows where relevant), docs updated, an ADR written for any important decision, and a short phase
report in `reports/phase_<n>.md` stating whether the exit gate passed. **Stop and ask me before
starting the next phase.**

### Phase 0 — Charter, scaffold and controls (Mac only)

- Create the full repo layout above, `pyproject.toml`, tooling (ruff, mypy, pytest, pre-commit),
  `CLAUDE.md`, `.cursor/rules/aurum.mdc`.
- Write `PROJECT_CHARTER.md` from §2, `STRATEGY_SPEC_TEMPLATE.md` (hypothesis, H1 context, M15
  setup, M5 trigger, entry, stop, target, timeout, invalidation, cost assumptions, expected trade
  count), config YAMLs with validation via Pydantic.
- Domain models, `Mode` enum, `Timeframe` enum with bar-close logic, UTC clock abstraction
  (injectable for tests).
- `BrokerPort` protocol + `FakeBroker` implementation.
- Session clock + state machine: CLOSED → PRE_SESSION → OPEN → NO_NEW_ENTRIES → EXIT_ONLY →
  FORCE_FLAT, DST-correct (test the March and October UK clock changes).
- Trial ledger (SQLite) with immutable trial IDs.
- `aurum doctor`.

**Exit gate:** `uv run pytest` green on Mac; `aurum doctor` works; any decision at any timestamp can
be described without subjective words ("strong trend", "clean entry").

### Phase 1 — MT5 connectivity spike (Windows VM)

- `adapters/mt5/` implementing `BrokerPort` with lazy import, reconnect/backoff, and the demo-only
  guard from §4.
- `aurum mt5 ping`, `aurum mt5 symbols --find gold` (persist `symbol_info` snapshot to
  `data/specs/`), fetch M5/M15/H1 bars and ticks in UTC.
- Demo smoke test (`tests/integration`, marked `mt5`): `order_check` → open minimum volume → modify
  SL/TP → close → reconcile deals.
- Reconnect test: 20 connect/disconnect cycles with no duplicate orders.

**Exit gate:** 20 clean reconnect cycles, successful demo open/modify/close, full reconciliation,
structured logs.

### Phase 2 — Data foundation

- Backfill maximum available ticks + M1/M5/M15/H1 in bounded chunks; idempotent.
- Live collector appending ticks while MT5 is connected (Windows), with heartbeat.
- Immutable raw Parquet partitioned by symbol/date: `raw_ticks/`, `raw_broker_bars/`,
  `normalized_ticks/`, `canonical_bars/{M1,M5,M15,H1}/`, `features/`, `quality_reports/`.
- Normalisation: dedupe, out-of-order, bid/ask/spread, negative or extreme spreads, stale quotes.
- Deterministic resampling ticks → M1 → M5/M15/H1; compare with broker bars and report
  disagreements.
- Gap and session-integrity checks; spread statistics by session (median, p90, p99) — use these to
  calibrate `max_spread_points`.
- `DATA_DICTIONARY.md`.

**Exit gate:** re-running ingestion + aggregation yields identical file hashes; no unexplained gaps
inside trading sessions.

### Phase 3 — Event-driven simulator (Mac)

- Chronological replay with no future access (the simulator only hands strategies data up to "now").
- Buys at ask, sells at bid; market, stop and limit orders only as needed.
- Variable spread, volume step/min/max, minimum stop distance (`trade_stops_level`), commission,
  configurable slippage + latency, rejection hooks.
- Intra-bar ordering: when SL and TP are both touched inside one bar, resolve using ticks; if ticks
  are unavailable, assume the worst case (stop first) and flag it.
- Session states including force-flat.
- Golden replay fixtures: fixed tick input → exact expected trades and P&L.

**Exit gate:** all synthetic edge cases behave as specified; a basic strategy replay matches the MT5
Strategy Tester ("Every tick based on real ticks") within an agreed tolerance.

### Phase 4 — Risk and execution core

- Position sizing from equity × risk % ÷ (stop distance × tick value), rounded down to volume step;
  reject if below min volume.
- Max risk/trade, one-position policy, daily/weekly loss limits, max trades/day, consecutive-loss
  lockout.
- Reject on: stale data, disconnection, abnormal spread, insufficient margin, too close to cutoff.
- Idempotent order manager + reconciliation after every action and on startup.
- Kill switch + watchdog.
- Fault-injection suite: disconnect mid-order, stale ticks, restart with open position, cutoff
  during disconnect.

**Exit gate:** tests prove strategies cannot bypass any limit; force-flat works after simulated
disconnect/restart; property test: risk after rounding never exceeds the ceiling.

### Phase 5 — Baseline strategy lab

Implement exactly three families, each in two variants (A: M15 direct entry, B: M15 setup + M5
trigger), everything else identical:

| Family | H1 context | M15 setup | M5 trigger |
|---|---|---|---|
| Trend pullback | Close above/below rising/falling slow EMA; volatility regime OK | Pullback into trend zone without closing past structural invalidation | Completed M5 close beyond micro-structure / engulfing |
| Opening-range breakout | Regime + no high-impact news window | Session opening range defined; breakout | Break-and-retest or close confirmation |
| VWAP / structure reversion | Non-trending regime | Deviation from session VWAP into structural level | Rejection candle |

- Each strategy gets a filled-in spec from the template before code.
- Broad, economically justified parameter ranges; small grids; every run logged in the ledger.
- Report gross vs net (spread, commission, slippage) attribution.

**Exit gate:** at least one family shows positive net expectancy across multiple chronological
out-of-sample folds, not dependent on one month, one direction or one parameter point. If none
passes: stop, write up findings, revise hypotheses — do not add ML.

### Phase 6 — Honest validation

- Seal the newest substantial period as a final test (store its date range in config; tooling must
  refuse to evaluate on it unless `--open-sealed` is passed, and log that it was opened).
- Anchored/rolling walk-forward; parameters fitted only in training windows; concatenate only
  out-of-sample trades.
- Purging + embargo for any labels spanning split boundaries.
- Stress: spread ×1.5/×2, extra slippage, missed fills, latency, earlier cutoff.
- Parameter-neighbourhood heatmaps (prefer plateaus).
- Metrics: net expectancy, profit factor, max drawdown, time under water, Sharpe, Sortino,
  return/drawdown, trade count, holding-time distribution, MAE/MFE, splits by
  year/month/session/direction/regime, trial count, Deflated Sharpe Ratio, probability of backtest
  overfitting.
- HTML/Markdown report per strategy in `reports/`.

**Exit gate:** survives conservative costs, stable neighbourhood, no single-period dependence,
passes the pre-registered sealed-test threshold (opened once).

### Phase 7 — Shadow mode (Windows VM)

- Full system live during sessions, generating signals and hypothetical fills against live bid/ask —
  no orders.
- Log every feature vector, decision, rejection and hypothetical fill.
- Daily reconciliation report: live bars vs MT5 chart, latency, quote staleness, predicted vs
  executable entry, backtest-vs-shadow divergence.

**Exit gate:** several weeks of stable operation with zero causality, session, duplicate or
data-integrity defects, across more than one volatility regime.

### Phase 8 — Demo execution (Windows VM)

- Enable demo orders at minimum volume. Freeze strategy changes per evaluation block.
- Store requested price, executable quote, ack, fill, modify and close timestamps; calibrate
  slippage/rejection models.
- Run a parallel shadow portfolio for comparison.

**Exit gate:** meaningful sample of demo trades, no unresolved incidents, execution divergence
within predeclared tolerance, demo expectancy statistically compatible with the conservative
backtest.

### Phase 9 — ML meta-filter (only if Phase 5–8 produced a validated strategy)

- Label: for each deterministic setup, did target hit before stop/timeout, after costs?
- Features: H1 regime, M15 structure, M5 trigger quality, session, time remaining, spread,
  volatility, setup distance, recent path.
- Models: regularised logistic regression → LightGBM. Calibrate probabilities; trade only if
  expected value > costs + margin.
- Fit transforms inside folds only; purged/embargoed walk-forward; compare to unfiltered baseline on
  identical setups.
- Risk and execution stay outside the model.

**Exit gate:** improves multiple untouched folds and the sealed comparison, stays calibrated in
shadow/demo, survives cost stress — and the gain is not just "fewer trades".

### Phase 10 — Strategy combination

- Correlation/overlap between validated strategies; deterministic conflict resolution with one
  position.
- Equal-risk allocation first; champion/challenger registry; manual promotion and one-click
  rollback; scheduled retraining only.

**Exit gate:** combination beats the best single component on drawdown/stability without hiding a
weak strategy.

### Phase 11 — Optional tiny live pilot (disabled)

Out of scope for now. Do not build anything that enables it beyond the guard in §4.

## 8. Testing pyramid (applies to all phases)

- **Unit:** sizing, resampling, indicators, session clock, labels, state transitions.
- **Property (hypothesis):** no feature uses a timestamp later than the decision time; risk after
  rounding ≤ ceiling; resampling is deterministic.
- **Golden replay:** fixed fixtures → fixed trades/P&L.
- **Integration** (`@pytest.mark.mt5`, Windows only): data, orders, modification, reconciliation.
  Auto-skip when `sys.platform != "win32"` or MT5 unavailable.
- **Fault injection:** disconnect, stale ticks, restart, rejected/partial orders, cutoff during
  failure.
- **Parity:** offline, shadow and demo feature vectors match for the same timestamp.
- **Regression:** every bug becomes a permanent fixture.

Target: ≥90% coverage on core, risk, sessions, backtest, execution.

## 9. How you (Claude) should work

- Read `docs/MASTER_PROMPT.md` and `CLAUDE.md` at the start of every session.
- Work in small, reviewable steps; one logical change per commit with conventional commit messages
  (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`).
- Write tests alongside (or before) the code. Never mark something done with failing tests.
- Type hints everywhere; docstrings on public functions explaining units (points vs price vs USD vs
  lots) and time semantics (open time vs close time, UTC).
- Prefer simple, explicit code over clever abstractions. No premature frameworks.
- When something is ambiguous in a way that affects trading logic, risk or data correctness, ask me
  instead of guessing. For cosmetic choices, decide and note it.
- Never add a dependency without stating why.
- Never weaken a safety rule from §4 to make something work. If a rule blocks you, tell me.
- If backtest results look too good (e.g. Sharpe > 3, profit factor > 2.5 on intraday gold), assume
  a bug or look-ahead leak first and investigate before reporting.
- Keep `CLAUDE.md` updated with conventions you establish.
- At the end of every phase, produce `reports/phase_<n>.md`: what was built, tests, exit-gate
  result, known issues, next steps.

## 10. First task (Sprint 1 = Phase 0 only)

Do only the following now, on macOS, then stop and report back:

1. Initialise the repo structure from §6 (empty modules with `__init__.py` and TODO docstrings where
   later phases go).
2. `pyproject.toml` with uv, Python 3.12, dependencies from §5 (Sprint 1 subset only), MetaTrader5
   with the Windows marker, dev group (pytest, hypothesis, ruff, mypy, pre-commit, pytest-cov),
   `[project.scripts] aurum = "aurum.cli:app"`.
3. `.gitignore`, `.env.example`, `.pre-commit-config.yaml`, ruff + mypy + pytest config.
4. `CLAUDE.md` and `.cursor/rules/aurum.mdc` containing a condensed version of §2, §4 and §9.
5. `configs/*.yaml` from §2 with Pydantic validation in `settings.py` (fail loudly on invalid
   config).
6. `core/`: domain models, `Mode`, `Timeframe` (with `bar_close_time(open_time)` and
   `last_completed_bar(now)`), injectable `Clock`.
7. `ports/broker.py` (`BrokerPort` Protocol) and `adapters/fake/broker.py` (`FakeBroker` with
   in-memory ticks, bars, positions and deterministic fills).
8. `adapters/mt5/` skeleton: lazy import, `is_available()`, connection class with the demo-only
   guard implemented and unit-tested via mocks (real calls come in Phase 1).
9. `sessions/`: session clock + state machine with DST tests (last Sunday of March and October in
   Europe/London).
10. `ledger/`: SQLite trial ledger (`record_trial`, `list_trials`, immutable IDs).
11. `cli.py` with `aurum doctor` (Python version + architecture, OS, MT5 importable, config valid,
    data dir writable) and placeholders for later commands.
12. `scripts/mac_setup.sh`, `scripts/windows_setup.ps1`, `docs/VM_SETUP.md`,
    `docs/PROJECT_CHARTER.md`, `docs/STRATEGY_SPEC_TEMPLATE.md`, `docs/ARCHITECTURE.md`,
    `docs/DECISIONS/ADR-0001-ports-and-adapters.md`.
13. Tests: unit + property tests for timeframes, sessions/DST, settings validation, FakeBroker, the
    demo-only guard, ledger.
14. Run `uv run ruff check`, `uv run mypy`, `uv run pytest` and fix everything until green.
15. Write `reports/phase_0.md` and give me: a summary of what exists, the exact commands to run on
    my Mac, the exact commands to run in the Windows VM for Phase 1, any questions or decisions you
    need from me.

Do not implement strategies, backtests, ML, dashboards or real MT5 order logic yet.
