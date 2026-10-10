# Status — read this second (after CLAUDE.md)

Where the project stands and what comes next. Update it whenever a phase, a decision or the
work queue changes. History lives in `reports/phase_<n>.md`; rules in `CLAUDE.md`; the full spec
in `docs/MASTER_PROMPT.md`; the code map in `docs/ARCHITECTURE.md`.

_Last updated: 2026-10-10._

## Phases

| Phase | State | Report |
|---|---|---|
| 0 Charter, scaffold | Done | `reports/phase_0.md` |
| 1 MT5 connectivity | Done | `reports/phase_1.md` |
| 2 Data foundation | Done | `reports/phase_2.md` |
| 3 Simulator | Done except **tester parity** (waiting for the user's MT5 Strategy Tester export) | `reports/phase_3.md` |
| 4 Risk + execution | **Approved** by the user's reviewer 2026-10-10 | `reports/phase_4.md` |
| 5 Strategy lab | **Started 2026-10-10** (reviewer approved f561a4d). All six pre-Phase-5 items done. First step: `docs/strategies/trend_pullback.md` **DRAFT 0.1.0, awaiting owner approval** (its section 0 decisions); no strategy code, no backtest of it | — |

## Work queue (in order)

### A. Before Phase 5 starts (reviewer decisions, 2026-10-10)

1. ~~**Prospective loss budget**~~ — **done** (c76a9d5, ADR-0010 amended).
   `remaining = limit − loss to date − open risk` per daily and weekly limit; loss to date =
   −(closed P&L + open P&L); open risk = further loss from the current exit price (bid for a
   BUY, ask for a SELL) to each stop, plus exit commission and stop slippage; a stopless
   position or a missing/stale quote rejects. Fault test
   `test_a_trade_that_would_overrun_the_daily_limit_is_rejected`.
2. ~~**Parity check additions**~~ — **done**. `ParityReport.ok` = equal in-window trade counts
   (explicit `trade_count_matches`) + every trade paired and within field tolerances (1 s,
   1 point, same exit reason, $0.02) + |Σ net Aurum − Σ net tester| ≤
   `Tolerance.cumulative_money` ($0.02, separate, never count × $0.02), totals over every
   in-window trade. `parity.json` carries counts, totals, difference, tolerance and booleans.
   The tester settings that must match (commission, leverage, deposit, modelling, delays) are
   a table in `docs/strategies/reference_fixed_time.md`. Never loosen a tolerance to pass.
3. ~~**Cancel inside a bar**~~ — **done** (Phase 4 issue 7, ADR-0009 §5). On bars, a cancel
   arriving inside a bar that reaches the pending entry: opened through the level → filled
   first, not flagged; only the range reached it → assumed filled first (worst case),
   `intrabar_rule = cancel_bar`, `worst_case = true`, kept over any same-bar exit; not reached
   → cancelled. Freeze level only if still pending. Ticks keep the exact order. Summary key
   `intrabar_cancel_bar`. Golden scenario `cancel_and_entry_in_one_bar` (14th).
4. ~~**Phase 3b (Dukascopy) trigger**~~ — **done** as a contract (ADR-0008 amendment,
   ADR-0009 §9, `docs/VALIDATION_PROTOCOL.md` "Intra-bar sensitivity"). Rule in
   `aurum.validation.phase3b.decide_phase_3b`: required if flagged ÷ total closed trades
   **> 1%** (strict) or sign(net worst) ≠ sign(net best) (cents, 0.00 its own sign) or the
   verdicts differ. Best case = a second replay with `intrabar_policy: best`, linked in the
   ledger, never edited trades; missing with flagged trades → fail closed; no override. The
   best-case policy in `SimBroker` and the ledger link are built with the Phase 5 runner.
   Hardened (449d261): net P&L goes through one cent quantizer, `phase3b.to_cents`
   (decimal `ROUND_HALF_UP`, ±0.005 → ±0.01, never −0.00). NaN/±inf `net_pnl` is rejected.
   The summary adds `best_case_replay_required` and `best_run_reused`.
5. ~~**Stop-size cap in every strategy spec**~~ — **done** (docs only; no spec validator
   exists, so nothing is enforced in code). `docs/STRATEGY_SPEC_TEMPLATE.md` §8a requires an
   inputs table with sources, the minimum-volume affordability ceiling (the `size_volume`
   calculation, floored to whole points), a strategy cap ≤ ceiling, and the cap's equity
   floor. A stop above the cap → no signal, recorded as `stop_above_cap`; never clamped. A cap
   change is a version bump. Phase 5 reports count `stop_above_cap`
   (`docs/VALIDATION_PROTOCOL.md`). Recomputed motivating example:

   | Input | Value | Source |
   |---|---|---|
   | Reference equity, currency | 10,000.00 USD | `configs/backtest.yaml` `initial_balance`; `broker.yaml` `account_currency` |
   | Risk per trade → budget | 0.25% → **25.00 USD** | `configs/risk.yaml` |
   | Point, `volume_min`, `volume_step` | 0.01 price, 0.01 lot, 0.01 lot | spec snapshot `20261010T000046Z.json` (USD account); same in `reports/phase_1.md` |
   | Money per price unit per lot | 100.00 USD (`tick_value` 1.0 ÷ `tick_size` 0.01) | spec snapshot `20261010T000046Z.json` |
   | Commission | 2.25 per lot per side, 4.50 round trip | `configs/broker.yaml` |
   | Stop slippage | 5 points = 0.05 price (5.00 USD per lot) | `configs/broker.yaml` (provisional) |

   Ceiling = (25.00 ÷ 0.01 − 4.50 − 0.05 × 100) ÷ 100 = 24.905 price → floor(2,490.5) =
   **2,490 points (24.90 USD/oz)**. At 0.01 lot that stop risks 24.995 ≤ 25.00, and 2,491
   points risks 25.005. Both checked against `size_volume`: 2,490 sizes to 0.01 lot, 2,491 to
   0. The equity floor of a 2,490-point cap is 9,998.00 USD, so after a 2 USD loss the engine
   rejects cap-sized stops. A spec should pick a cap with room below the ceiling. The
   reference strategy's 14:30 SELL (3,000 points) risks 30.095 at 0.01 lot, hence all 1,524
   rejections.

   **Source of truth:** until 2026-10-10 the only *tracked* record of the XAUUSD contract was
   `reports/phase_1.md`, measured on the old **GBP** account. The USD-account values (point,
   volume min/step, `tick_value`/`tick_size`, from snapshot `20261010T000046Z.json`) are now
   recorded in `docs/strategies/trend_pullback.md` §8a. Re-check them against a fresh snapshot
   whenever a spec is approved.
6. ~~**CI**~~ — **done**: `.github/workflows/ci.yml` (GitHub Actions).
   - **Runs on:** push to `main`, every pull request, and manual dispatch.
   - **Matrix:** `ubuntu-latest` and `macos-latest`, Python 3.12 (`fail-fast: false`).
   - **Install:** uv 0.12.24, the version that wrote `uv.lock`, then `uv sync --locked --dev`.
     It installs exactly the lock, including the dev group, and fails if the lock is stale; it
     never rewrites it.
   - **Gates, in order:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`,
     then `uv run pytest --cov=aurum --cov-branch --cov-report=term-missing
     --cov-report=xml --cov-precision=2 --cov-fail-under=91`.
   - **Artifacts:** `coverage.xml` is uploaded per OS (`coverage-<os>-py3.12`, 7 days),
     including after failed test or coverage runs.
   - **Safety:** permissions `contents: read`; actions pinned to commit SHAs; no secrets,
     `.env`, MT5 or order variables; `AURUM_DATA_DIR` points at the runner's empty temp dir.
     Concurrency cancels superseded runs of the same ref. 30-minute timeout,
     `PYTHONHASHSEED=0`, `TZ=UTC`.
   - **Coverage floor 91, not 92.** On the VM the MT5 integration tests run against the
     terminal, and the total is 92.05–92.07%. Without them, as on Linux/macOS, a clean clone
     measures **91.99%** (lines 5,788/6,163 = 93.92%, branches 1,145/1,374 = 83.33%;
     hypothesis moves it by ±1–2 branches). That is below 92. `--cov-precision=2` because
     fail-under compares `round(total, precision)`, and at the default 0 a 91.99% total
     would pass a 92 floor. Raise the floor when the measured total allows; never lower it
     silently.
   - **MT5 tests skip** off Windows or without the `MetaTrader5` package (`tests/conftest.py`),
     so CI never contacts a terminal. Order tests also need `AURUM_MT5_ORDER_TESTS=1`, which CI
     never sets. Windows/MT5 checks stay on the VM.
   - **`uv run mypy src tests` is intentionally not a gate.** mypy is configured strict over
     `files = ["src"]`; pytest imports and runs the tests. That command fails before checking
     (`test_mt5_adapter.py` "found twice under different module names"), and tests are not
     reorganised for it.
   - **First run green.** f561a4d is on remote `main` (pushed by the user); run 38040512143.
     Both jobs passed every step: **382 passed, 11 skipped** (all 11 MT5 integration tests
     skipped). Coverage was identical on both: lines 5,787/6,163 = 93.90%, branches
     1,145/1,374 = 83.33%, **combined 91.97%**. uv 0.12.24 installed from the lock unchanged;
     cache saved; artifacts `coverage-ubuntu-latest-py3.12` and `coverage-macos-latest-py3.12`
     uploaded.
   - No README badge: the repo is private, so the badge image does not render outside GitHub
     sessions with access.

### B. Later, tracked

- Adopted positions (state file lost) get no timeout/invalidation, only force-flat + kill
  switch. Acceptable on demo; **fix before any live pilot** (Phase 4 issue 5).
- If Aurum ever trades several symbols at once, `risk.policy.open_risk()` must take a fresh
  quote and `SymbolSpec` per position's symbol instead of one shared `Tick`/`SymbolSpec`.
- Margin estimate vs `order_calc_margin` — Phase 8 (Phase 4 issue 1).
- Watchdog scheduling waits for the Phase 7/8 trader loops (Phase 4 issue 6).
- Add each year's FOMC dates to `configs/risk.yaml` in time (`aurum doctor` warns 60 days
  ahead; current list ends 2026-12-09) and future market holidays to `configs/data.yaml`.
- **Tighten the CI coverage floor** (reviewer, 2026-10-10) after comparing a few CI runs, not on
  one hypothesis-driven branch. If both platforms stay ≥ 91.95%, use `--cov-fail-under=91.90`;
  if one is lower, set it slightly below the lowest reproducible total. Never lower it
  silently. First run: 91.97% on both.
- **Run the collector from a versioned release environment**, not the editable dev install.
  Today it holds the dev venv's `aurum.exe` open, coupling development to the live process (see
  Environment facts).

### C. User actions (not Claude's)

- **Tester run**: second MT5 in portable mode (`terminal64.exe /portable`, separate folder, own
  data dir) so the live collector keeps running. Run `mql5/AurumReference.mq5`, export deals CSV
  + HTML report, then `aurum backtest parity 20261009T221629Z_reference_fixed_time_m5_be6c94
  <deals.csv>`. Steps and the settings that must match: `docs/strategies/reference_fixed_time.md`.
- **Review and approve `docs/strategies/trend_pullback.md`** (DRAFT 0.1.0): decisions D1–D5 in
  its section 0. It becomes the pre-registered spec only after approval.

## Phase 5 conditions (from the reviewer; part of the gate)

- Write each strategy spec (`docs/STRATEGY_SPEC_TEMPLATE.md`) **before any code**, including the
  stop-size cap (§8a, derived per spec). Trend-pullback: drafted by Claude on 2026-10-10 at the
  reviewer's request, awaiting approval. Then opening-range breakout and VWAP reversion, one at
  a time.
- Report `stop_above_cap` counts per candidate (`docs/VALIDATION_PROTOCOL.md`).
- Every trial in the ledger. A **hold-out period** never touched during search.
- Report **gross vs net**. Apply a **Deflated-Sharpe-style** correction to the best candidate.
- Report **intra-bar sensitivity** per candidate (worst + best replay, `phase_3b_required`);
  see `docs/VALIDATION_PROTOCOL.md`. Phase 3b required → not passed.
- Judge M15-direct vs M5-trigger on **net expectancy and drawdown after costs**, not win rate.
- **Null baseline**: `reference_fixed_time` with the policy risk engine, 2020-11-12 … 2026-10-08,
  run `…233326910002Z_reference_fixed_time_m5_ae3d92`: 1,520 trades, **−$381.76**, max DD 5.4%.
  This is what no edge looks like after costs; judge every candidate against it. Rerun on
  c76a9d5 (prospective budget) as `20261010T002425547831Z_reference_fixed_time_m5_3322ec`:
  byte-identical trades (sha256 `447228f1…`). The budget never binds for this strategy (at
  most one ~$20 trade a day, far from the $250 weekly limit).

## Environment facts

- This checkout runs on the **Windows 11 ARM VM** (`C:\code\aurum`), venv `C:\venvs\aurum`
  (x64 Python under emulation), data `C:\aurum-data` (`AURUM_DATA_DIR`). The live tick
  collector runs there continuously; don't stop it.
- **Editing `pyproject.toml` on the VM breaks `uv run` while the collector runs.** uv sees the
  file's new mtime, rebuilds the editable install, and fails to replace
  `C:\venvs\aurum\Scripts\aurum.exe` (os error 32), which the collector holds open. Found
  2026-10-10; that edit was reverted. Until the collector's next deliberate restart:
  - don't change `pyproject.toml` on this checkout;
  - run commands as `uv run --no-sync …`;
  - make dependency changes (e.g. for Phase 5) in another checkout, or restart the collector
    deliberately;
  - never work around this by editing file timestamps.
- Backtest runs: `reports/runs/<run_id>/` (git-ignored). Trial ledger: `<data>/ledger/`.
- Test suite: 393 tests (384 passed, 9 skipped on the VM: 6 live-market checks with the market
  closed, 2 demo-order tests, 1 non-Windows test), ~65–90 s with coverage, 92.07% combined on
  the VM (2026-10-10). Keep it lean (see `CLAUDE.md`). CI on Linux/macOS: 382 passed,
  11 skipped, 91.97% (item 6).
