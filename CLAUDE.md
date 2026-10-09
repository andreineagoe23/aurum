# CLAUDE.md — Aurum working rules

Read `docs/MASTER_PROMPT.md` (full spec) and this file at the start of every session.
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
  (account ccy, read from the broker: `broker.account_currency: auto`; the demo is GBP).
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
- Unimplemented CLI commands exit with code 2 and name their phase.
- Dependencies: add only when used, with a one-line reason in `pyproject.toml`.

## How to work (MASTER_PROMPT §9)

- One phase at a time; stop and ask before the next phase. End each phase with
  `reports/phase_<n>.md` and ADRs for important decisions.
- Small commits, conventional messages (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`), no
  attribution trailers.
- Tests alongside code; never call something done with failing tests.
- Ask when ambiguity touches trading logic, risk or data correctness; decide cosmetics and note it.
- Results that look too good (Sharpe > 3, PF > 2.5) → assume a bug/leak first.
