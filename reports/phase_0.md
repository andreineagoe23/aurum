# Phase 0 report: charter, scaffold and controls

- **Date:** 2026-10-08
- **Platform:** macOS (Apple Silicon), Python 3.12.15 via uv 0.12
- **Exit gate:** **PASSED** (see below). Phase 1 has not started; it waits for owner sign-off.

## What was built

| Area | Contents |
|---|---|
| Tooling | `pyproject.toml` (uv, hatchling, Python 3.12 only), `uv.lock` (resolves `MetaTrader5 5.0.6231 cp312 win_amd64` for Windows), ruff (incl. `DTZ` naive-datetime ban), mypy `--strict` on **all** of `src/`, pytest + hypothesis + coverage, pre-commit (ruff, mypy, a hook that blocks committing `.env`), `.gitattributes` (LF; CRLF for `.ps1`) |
| Config | `configs/{broker,sessions,risk,promotion}.yaml` + 3 strategy stubs, validated by `aurum.settings`. Unknown keys are errors. `HH:MM` times must be quoted. Safety caps are checked in validators. Strategies cannot be enabled without a spec document |
| `aurum.core` | `enums` (`Mode`, `Side`, `SessionPhase`…), `timeframes` (`Timeframe.bar_close_time`, `last_completed_bar`), `timeutil` (`UtcDatetime`; naive input rejected), `clock` (`SystemClock`, `ManualClock`), `models` (12 frozen domain models + `OrderState`, `DealRecord`, `OrderCheckResult`), `safety` (live-trading guard), `errors` |
| Ports | `BrokerPort` (full §3 surface), `ClockPort`; `DataStorePort` is a TODO for Phase 2 |
| Adapters | `FakeBroker`: in memory, fills at bid/ask with optional slippage, idempotent, commission, P&L, disconnect simulation. `MT5Broker` skeleton: lazy import, connect, `account_info`, and every write runs the guard after a fresh account read. Real calls come in Phase 1 |
| Sessions | `SessionClock` (London → UTC per date, DST-aware) and `SessionStateMachine` (emits a transition on startup, so force-flat still fires after a restart). Semantics are in ADR-0002 |
| Ledger | Append-only SQLite (`UPDATE`/`DELETE` blocked by triggers), generated immutable IDs, per-row SHA-256 with `verify()`, `count_trials` for DSR later |
| Monitoring | structlog JSON logging with secret-key redaction; doctor checks |
| CLI | `aurum doctor`, `aurum ledger list`. Placeholders for every other command, which exit with code 2 and name their phase |
| Docs | MASTER_PROMPT, PROJECT_CHARTER, ARCHITECTURE, DATA_DICTIONARY, STRATEGY_SPEC_TEMPLATE, VALIDATION_PROTOCOL, VM_SETUP, ADR-0001 (ports and adapters), ADR-0002 (session phases), ADR-0003 (time conventions), README, CLAUDE.md, `.cursor/rules/aurum.mdc` |
| Scripts | `scripts/mac_setup.sh`, `scripts/windows_setup.ps1` (forces x64 Python on ARM64, venv at `C:\venvs\aurum` via `UV_PROJECT_ENVIRONMENT`, data at `C:\aurum-data`) |

The later-phase packages (`data`, `features`, `strategies`, `risk`, `execution`, `backtest`,
`validation`, `adapters/storage`, `apps/*`) exist as stubs with TODO docstrings.

## Tests

`uv run ruff check`, `uv run ruff format --check`, `uv run mypy` and `pre-commit run --all-files`
are all clean. Results: **250 passed, 1 skipped** (the MT5 integration test, which is
Windows-only). Line coverage is 96% overall, and 98–100% on core, sessions and the fake broker.

The safety-relevant tests:

- **Live guard:** an exhaustive check over modes, ack phrases, `live_enabled` and sign-off shows
  that exactly one combination unlocks a real account. BACKTEST and SHADOW never send orders, even
  on demo. Every MT5 write (send, modify, close, cancel) is blocked on REAL, CONTEST and unknown
  account modes. The account is re-read on every write, so swapping demo for real is caught. The
  password never appears in logs.
- **Order invariants:** an `OrderIntent` cannot exist without an approved `RiskDecision` or a stop
  loss, and cannot exceed the approved volume. Brokers also refuse intents built with
  `model_construct`, which skips validation. A duplicate idempotency key never opens a second
  position.
- **Causality:** property tests show that `last_completed_bar(now).close_time <= now`, that it is
  always the latest such bar, and that higher-timeframe closes align with lower ones. A
  `MarketEvent` delivering a bar before its close fails validation. A `Signal` whose `data_as_of`
  is after its decision time fails validation. `FakeBroker` never returns a forming bar and never
  fills on a future tick.
- **Sessions and DST:** the March and October UK clock changes are tested for 2025, 2026 and
  2027. A property test runs 2020–2032 against an independent wall-clock oracle. Phases never move
  backwards within a day. A restart at 21:00 emits FORCE_FLAT.
- **Architecture:** `MetaTrader5` is imported only inside functions in `aurum.adapters.mt5`.
  `aurum.core` has no other `aurum` imports. Strategies and features cannot import brokers,
  execution or apps. The whole package imports on macOS.

## Exit gate

| Criterion | Result |
|---|---|
| `uv run pytest` green on Mac | ✅ 250 passed / 1 skipped |
| `aurum doctor` works | ✅ All checks OK on Mac (MT5 shows SKIP as expected) |
| Any decision at any timestamp describable without subjective words | ✅ Structurally. `SessionState` gives an objective phase for every instant. `Signal` requires non-empty `reasons`, numeric `features`, and `data_as_of` ≤ decision time. The spec template bans subjective words and requires a numeric decision trace. It gets tested on real strategies in Phase 5. |

## Known issues and risks

1. **MT5 timestamps are probably not UTC** (ADR-0003). The MetaTrader5 API returns epoch seconds
   in the broker server's time zone. Fusion Markets typically runs GMT+2/+3. Phase 1 must measure
   and convert this before any data is stored.
2. **Idempotency through the order comment.** The key (22 characters) fits MT5's 31-character
   comment limit, but some brokers rewrite comments. Phase 1 will check this on Fusion demo. If
   comments are rewritten, the fallback is the `magic` number plus a local intent journal.
3. `max_spread_points` is `null` until Phase 2 calibration. `aurum doctor` reports DEMO/LIVE mode
   as not ready (FAIL) until it is set.
4. Slippage is set to 5 points per side as a placeholder; Phase 8 replaces it.
5. The data stack (polars, pyarrow, duckdb, numpy, pandas) was deliberately left out. Each one is
   added in Phase 2 when first used.
6. The repo is at `~/Downloads/projects/aurum`, not `~/code/aurum`. Docs and scripts don't depend
   on the path.

## Next steps (Phase 1, after sign-off)

Implement the `MT5Broker` reads and writes behind the existing guard. This includes
reconnect/backoff, `aurum mt5 ping` and `aurum mt5 symbols --find gold` (with a spec snapshot to
`data/specs/`), server-time offset detection, the demo smoke test (check → open minimum volume →
modify → close → reconcile) and 20 reconnect cycles.
