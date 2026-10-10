# CLAUDE.md — Aurum working rules

Start of every session, in this order: this file → `docs/STATUS.md` (phase state, approved work
queue, open decisions) → `docs/ARCHITECTURE.md` (code map, flows, which test guards what) →
`docs/MASTER_PROMPT.md` (full spec) when a task touches scope or phase gates. Keep STATUS.md
current: update it when a phase, decision or the queue changes.
Aurum = research + automated trading platform for **intraday XAUUSD on MT5, demo only**.
Its job is to tell the truth, not to make backtests look good.

## Mandate (v1, frozen — MASTER_PROMPT §2)

- Fusion Markets MT5 Zero **demo**. XAUUSD symbol discovered at runtime, never hard-coded.
- Intraday only: flat before `force_flat_at` (20:00 London). No overnight/weekend exposure.
- Timeframe roles: H1 regime, M15 setup/invalidation, M5 trigger, ticks/M1 execution realism.
- Completed candles only; higher TFs as-of joined on **close time**.
- One position at a time. Broker-side stop on every trade.
- Costs: commission $2.25/lot/side, real bid/ask spread, configurable slippage.
- Deterministic rules first; ML only as a later meta-filter. No DL/RL.

## Safety rules (never weaken — tell the user if one blocks you)

1. Live trading blocked by default (`aurum.core.safety`): non-demo accounts need
   `AURUM_MODE=LIVE` + exact `AURUM_LIVE_ACK` + `promotion.yaml` sign-off. BACKTEST/SHADOW never
   send orders. Real-broker writes re-read `account_info()` every time.
2. Strategies return `Signal`s only. `OrderIntent` embeds an approved `RiskDecision`; brokers
   re-check it.
3. Every `OrderIntent` has a mandatory `stop_loss` and an idempotency key (`"AU" + signal.key`).
4. Reconcile on startup and after every action; restarts never duplicate orders.
5. Force-flat works after disconnect/restart (state machine emits on first update).
6. Kill switch: `KILL` file in data dir / `aurum kill` (Phase 4).
7. Never use a bar before its close time. Buys fill at ask, sells at bid.
8. Every trial goes in the ledger; never report only the best; never tune on the sealed test.
9. Never auto-promote. Secrets only in `.env`; never log passwords.

## Conventions established

- Layout: src layout, package `aurum`; Python 3.12 via uv. Commands: `uv run ruff check`,
  `uv run ruff format`, `uv run mypy` (strict, whole `src/`), `uv run pytest`.
- Time: aware UTC everywhere; naive datetimes rejected (ruff `DTZ`). Use `UtcDatetime` in
  models, `to_utc()` at boundaries, `ClockPort` for "now" (never `datetime.now` in logic).
  Bars keyed by open time. See ADR-0003.
- MT5 time is server wall clock (Fusion: New York + 7h). Convert only via
  `aurum.adapters.mt5.servertime.ServerTimeZone`, both for values read and for query bounds;
  nothing outside `aurum.adapters.mt5` sees a server timestamp. ADR-0004.
- Idempotency key = MT5 order comment (≤ 29 chars, the Python API limit), plus
  `broker.magic_number`. Dedup searches positions, pending orders and opening history orders.
  ADR-0005.
- Units in names/docstrings: *price* (USD/oz), *points* (× `SymbolSpec.point`), *lots*, *money*
  (account ccy, read from the broker; `broker.account_currency: USD` refuses any other account).
  Commission on deals is negative.
- Models: pydantic v2, `frozen=True`, `extra="forbid"`, invariants in `model_validator`.
- Config: YAML in `configs/`, validated in `aurum/settings.py`, unknown keys are errors,
  clock times must be quoted `"HH:MM"`. Safety caps live in the validators.
- `aurum.core` imports nothing else from `aurum`; strategies/features never import adapters,
  execution, apps or the broker port; `MetaTrader5` only inside functions in
  `aurum.adapters.mt5`. Enforced by `tests/unit/test_architecture.py`.
- Session phases: ADR-0002. `SessionPhase.entries_allowed` / `.must_be_flat`.
- Tests: `tests/factories.py` builders; `tests/unit`, `tests/property` (hypothesis),
  `tests/integration` (`@pytest.mark.mt5`, auto-skipped off Windows), `tests/replay`,
  `tests/fault`. Warnings are errors. MT5 logic is unit-tested against `tests/fake_mt5.py`;
  unit tests never touch a real terminal. Tests that send demo orders are also marked
  `mt5_orders` and need `AURUM_MT5_ORDER_TESTS=1`.
- Keep the suite lean (the user cut it from 610 to ~380 on 2026-10-10 and wants it to stay
  small). Extend an existing test before adding a new one; one test per behaviour, not per
  input. Use a loop with an assertion message for case tables, and a hypothesis property
  instead of long example tables. Safety guarantees (live guard, OrderIntent invariants,
  idempotency, kill switch, fault and golden scenarios) keep their own named tests. Check
  `--cov` stays level when merging or removing tests.
- Tests never depend on today's date: pin every clock, including `SystemClock` inside the
  code under test (see `test_cli.py::test_data_collect_runs_and_stops`).
- Unimplemented CLI commands exit with code 2 and name their phase.
- Dependencies: add only when used, with a one-line reason in `pyproject.toml`.
- Data (Phase 2, ADR-0006/0007): one Parquet file per symbol and UTC date under
  `AURUM_DATA_DIR`; schemas only in `aurum.data.schemas`, paths only in
  `aurum.adapters.storage.layout`. Raw is immutable (`write_parquet(..., overwrite=False)`),
  derived data is rebuilt deterministically (sort before grouping, integer sums for means) and
  checked with `aurum data hashes --compare`. Canonical bars come from ticks only on days whose
  tick-built M1 equals the broker's M1 (`tick_fidelity = exact`); see `canonical_bars/days`.
- Market hours, holidays and early closes live in `configs/data.yaml` (each with a reason);
  `aurum data qa --strict` must report 0 unexplained. Add future holidays before they happen.
- Backtests (Phase 3): strategies see only a `MarketView` of bars closed by `now`; the runner
  wakes at M5 closes, phase boundaries and `max_hold` deadlines; `SimBroker` fills on the first
  quote at or after `now + latency`. Bar-only days use the worst-case intra-bar rule and flag the
  trade (`intrabar_rule`, `worst_case`). Every `aurum backtest run` records a trial and writes
  `reports/runs/<id>/` (git-ignored) with output hashes; reruns must be byte-identical
  (`aurum backtest compare`).
- Golden replay (`tests/replay/scenarios.py`): any change to an expected number needs a
  stated reason in the commit; bugs become new scenarios. Hooks must be stateless.
- Parity: `mql5/AurumReference.mq5` mirrors `reference_fixed_time`; compare with
  `aurum backtest parity <run_id> <deals.csv>` (docs/strategies/reference_fixed_time.md).
- Risk (Phase 4, ADR-0010): `PolicyRiskEngine` in every mode; size = floor(equity × risk% ÷
  (stop × value + 2 × commission + stop slippage), step), never rounded up to `volume_min`.
  Loss limits and streaks come from the broker's deal history (magic + "AU"), never local
  state. Limits are prospective: new trade risk ≤ limit − loss to date − open risk (open risk
  from the current exit price to each stop, + exit commission + stop slippage; unknown → reject). News windows and FOMC dates live in risk.yaml; add each year's FOMC dates in time.
  `FixedVolumeRisk` is for the tester parity check only (`--risk fixed`).
- Execution (ADR-0011): the engine sends only through `OrderManager` (intent recorded first,
  60 s age limit, lost reply = UNKNOWN, never resent); `reconcile` fixes the store and unsafe
  broker state; no new entry while an outcome is unresolved. Ownership = magic AND "AU" key.
  Kill switch = `<data>/KILL`; state in `<data>/state/` (paths only in `DataLayout`).
- Fault tests wrap brokers in `tests/fault/faulty.py::FaultyBroker`.
- On the VM, import `aurum` before `polars` (polars needs its compat runtime under emulation,
  `aurum/_platform.py`).

## How to work (MASTER_PROMPT §9)

- One phase at a time; stop and ask before the next phase. End each phase with
  `reports/phase_<n>.md` and ADRs for important decisions.
- Small commits, conventional messages (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`), no
  attribution trailers.
- Tests alongside code; never call something done with failing tests.
- Ask when ambiguity touches trading logic, risk or data correctness; decide cosmetics and note it.
- Results that look too good (Sharpe > 3, PF > 2.5) → assume a bug/leak first.
