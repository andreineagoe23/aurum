# Status — read this second (after CLAUDE.md)

Where the project stands and what comes next. Update it whenever a phase, a decision or the
work queue changes. History lives in `reports/phase_<n>.md`; rules in `CLAUDE.md`; the full spec
in `docs/MASTER_PROMPT.md`; the code map in `docs/ARCHITECTURE.md`.

_Last updated: 2026-10-11._

## Phases

| Phase | State | Report |
|---|---|---|
| 0 Charter, scaffold | Done | `reports/phase_0.md` |
| 1 MT5 connectivity | Done | `reports/phase_1.md` |
| 2 Data foundation | Done | `reports/phase_2.md` |
| 3 Simulator | Done except **tester parity** (waiting for the user's MT5 Strategy Tester export) | `reports/phase_3.md` |
| 4 Risk + execution | **Approved** by the user's reviewer 2026-10-10 | `reports/phase_4.md` |
| 5 Strategy lab | **In progress.** Family 1, `trend_pullback` 0.2.0 + amendment A1: **rejected** by the reviewer on 2026-10-11 (queue A2). m15 and m5 both fail registered gates, and no candidate goes to the sealed test. Family 2, `opening_range_breakout`: spec **draft 0.1.0** written (docs only, no code, no P&L), **awaiting review** (queue A3) | — |

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

### A2. Phase 5 queue for trend_pullback (owner's order, 2026-10-10)

1. ~~**Freeze spec 0.2.0**~~. **Approved** by the owner on 2026-10-10 (text `b9baf4c`):
   D1 sealed 2025-10-01 … 2026-10-08, D2 cap 2,000 points, D3 minimum 500 points, D4 54
   trials, D5 section-16 thresholds, D6 (b) timeout from `Signal.time`. The owner approved
   the reported specification, not a line-by-line review of the diff.
2. ~~**Sealed-period protection**~~ (552beed): config boundaries; backtests refuse the range; an explicit
   `--open-sealed` with a journal of when, why and by whom; opened once; exact-tick subset
   reported separately.
3. ~~**Warm-up by bar count**~~ (c6b0c86): 504 H1, 509 M15 and 3 M5 completed bars; no activity in warm-up.
4. ~~**Timeout semantics confirmed**~~ (1b23791): signal-to-exit, existing engine behaviour; docs + a
   regression assertion.
5. ~~**Strategy rules**~~, exactly as specified: no parameter change, no extra filter.
6. ~~**Deterministic rule tests**~~: timestamps, equality boundaries, setup replacement, gaps,
   stop bounds, restart restoration.
7. **Run the 54 development trials.** Every trial goes in the ledger, including failures and
   rejected-setup counts. **Claude stops for review before this first P&L-producing step.**
   **Ready for that review (2026-10-10):**
   - **Spec audit:** an independent read-only audit found no deviation or ambiguity. Two of
     its notes were applied: the strategy checks decision times itself, and M15 gaps are
     counted for m5.
   - **Mutation test:** 22 targeted rule mutations (strict comparisons, windows, rounding
     directions, expiry order, one use, regime at the trigger, invalidation window) are all
     caught by `tests/unit/test_trend_pullback.py`. The only survivor is an equivalent mutant:
     the decision-time check in `on_bar` duplicates the one in the trigger and setup rules.
   - **Real-data trace:** development-period bars reproduce the spec trace at
     2024-03-01 14:15Z exactly (no P&L computed).
   **Second review (2026-10-10), fixed. No trial run, no sealed opening:**
   - **Parity exemption narrowed** (54c2470): only `reference_fixed_time` m5, inside
     2026-09-14 … 10-08, in the tester context, via `--parity`, journalled in
     `parity_accesses`. Ordinary reference backtests are sealed too.
   - **`data_gap` defined** (8b91d8c): one per decision time with a missing bar in the
     variant's read set.
   - **Literal restart-after-deadline test** (e758221).
   - **Durable attempts and the grid runner:** every evaluation is a ledger attempt before it
     runs, and failures keep their stage and error. Retries are explicit and linked.
     `aurum backtest grid` runs only the declared 27 points per variant. All of this is
     tested on synthetic data only.
   **Third review (2026-10-10): evaluation-integrity blockers, fixed. No trial run, no
   development P&L, no sealed opening, no parity on real data, ledger untouched:**
   - **Frozen batch identity.** `pipeline.prepare` resolves the evaluation context before any
     attempt:
     - code: the `aurum` source tree by content, installed distributions, Python;
     - the spec document's hash;
     - the config hash, plus the effective costs, risk and account values;
     - the symbol snapshot, name and content;
     - input data: a hash over every planned file, warm-up included. The loader now plans
       files before reading any (`plan_market`, `read_market`);
     - the declared grid and the dates.

     Every attempt stores its context. A batch is frozen to its first run's context
     (`grid_batches`, append-only). A grid run, an attempt or a retry under another context is
     refused before any attempt, naming the changed parts. A grid request must name its own
     batch id and a declared point. Code identity is the source content, not the git sha: an
     uncommitted edit breaks a batch, a docs-only commit does not.
   - **Post-trial recovery.** The order is now: stage the run files
     (`reports/runs/.staging/<id>/`), then record the trial and the success outcome in one
     transaction, then publish (rename).
     - A unique index allows one trial per batch point.
     - A point with a trial is never evaluated again, whatever its attempt rows say. That
       includes a trial left next to an unfinished attempt by the old, non-atomic order.
     - `check_retry` refuses an attempt whose point has a trial.
     - A failed publish raises `ArtifactsPendingError` and the trial stands.
       `aurum backtest recover` publishes staged files after checking their hashes against
       the trial. It runs nothing and writes no ledger row.
   - **Empty periods.** `first ≤ last` is checked in `prepare`, `resolve_access`, `run_grid`
     and `start_attempt`, before any journal, opening or attempt row.
   - **Test evidence (synthetic only):**
     - `test_a_grid_batch_is_frozen_to_its_evaluation_context`: changing each of code, spec,
       costs, risk, symbol snapshot, a warm-up data file or the declared grid is refused with
       the changed part named and no new attempt. Other dates under the same batch id are
       refused. Restored, the batch completes (27 trials, 28 attempts, 1 batch).
     - `test_recorded_trials_are_recovered_never_evaluated_again`: four injected failures.
       - Staging-write failure: failed at `write`, no trial.
       - Publish failure: succeeded, files staged.
       - Success-outcome recording failure inside the transaction: rolled back, failed at
         `record`, no trial.
       - Crash after the trial was persisted, with no outcome: unfinished, with a trial.

       Resuming reconciles the two points that have trials and does not rerun them.
       `recover` publishes both. `--retry-failed` reruns only the two points with no trial,
       never the unfinished one. In the end there are 27 trials and 27 published runs, and
       every point is simulated once, except the two retried points (twice).
     - `test_an_empty_period_leaves_no_ledger_trace`: a reversed range on run, `--open-sealed`,
       `--parity` and grid gives exit 1 and zero rows in every ledger table. Before this fix,
       the parity path journalled such a range.
     - The retry test also checks a refusal under another context, and a refusal when the
       point already has a trial.
     - Re-introducing each bug fails a test: no range check, non-atomic trial + outcome,
       "done" taken from attempt status, no data identity, retries ignoring context or
       trials, no batch freeze.
   - Ledger schema: two nullable columns on `attempts` (added in place if the table predates
     them), plus `grid_batches` and the unique index. The real ledger (`C:\aurum-data`,
     checked read-only) has only `trials` (10 rows) and no attempts, so no old attempt is
     affected. Nothing was deleted or rewritten.
   **Approved to run (2026-10-10), then refused before any attempt.** The reviewer approved
   the 54 trials on `f5b2744` (2020-11-12 … 2025-09-30, one grid process at a time).
   - Pre-flight: clean worktree; only the collector running; ledger backup
     `C:\aurum-data\ledger\backups\trials_20261010T1422Z_before_trend_pullback_dev_grid.sqlite`
     (sha256 `0c0171ca…6329076`); no earlier batch to reconcile.
   - The m15 grid was refused in `prepare` with "warm-up needs 504 completed H1 bars before
     2020-11-12; only 0 in the store". There was no attempt, batch or row, and m5 was not
     started. Evidence: `docs/strategies/evidence/trend_pullback_2026-10-10_warmup_preflight.md`.
   **Amendment A1 (reviewer, option A, pre-results):**
   - The evaluated trading period is 2020-12-15 … 2025-09-30, with 10,000 USD at 2020-12-15
     and the training-only window from 2020-12-15. 2020-11-12 … 12-14 is warm-up only.
   - OOS folds, rules, grid, costs, risk, thresholds and the warm-up requirement are
     unchanged. No backfill.
   - Recorded in spec section 16 and the change log.
   **Pinned symbol snapshot** (reviewer condition, needed a code change):
   - `backtest.yaml` `symbol_snapshot` is required: `20261010T000046Z.json`, sha256
     `0426cf8c…be0c7ee`. Every backtest loads exactly that file (`specs.pinned_spec`) and
     refuses it if it is missing or its bytes differ. The collector's newer daily snapshots
     are ignored, so a batch no longer depends on finishing before 00:00 UTC.
   - Snapshot file and hash are in the frozen context.
   - Tests (synthetic):
     - changing the pin is refused (`config_hash, symbol`);
     - a tampered pinned file is refused before any attempt;
     - a newer snapshot leaves the batch unchanged and lets it finish.
   - Re-introducing "latest snapshot" or skipping the hash check fails the test.
   **Claude stops for review of the snapshot pin before running any of the 54 trials.** Next,
   once approved, run sequentially:
   `uv run --no-sync aurum backtest grid trend_pullback --variant m15 --from 2020-12-15 --to
   2025-09-30`, then the same with `--variant m5`. No automatic `--retry-failed`.
   - Open point for later, not now: the null baseline in "Phase 5 conditions" covers
     2020-11-12 … 2026-10-08. A like-for-like comparison would rerun it over 2020-12-15 …
     2025-09-30. That is an extra evaluation and needs approval.
   **Done (2026-10-10).** The reviewer approved the pin, A1 and the 54 points on `9afdf37`.
   - m15 ran 14:40:58–15:08:47Z and m5 ran 15:08:59–15:43:37Z, one after the other, with
     `uv run --no-sync`.
   - Each batch has 27 attempts, all succeeded, 0 retries and 27 trials: 54 points, 54
     attempts, 54 trials in all. All run files are published and hash-verified; nothing is
     staged. Sealed openings 0, parity accesses 0.
   - Context hashes: m15 `2c9968b7…`, m5 `51552400…`. They differ only by `variant`.
   - Evidence: `docs/strategies/evidence/trend_pullback_2026-10-10_dev_batch.md`, plus
     `_index.csv` (54 rows) and `_contexts.json`.
   - Backups: pre-run `…1422Z_before…` (unchanged) and post-run
     `C:\aurum-data\ledger\backups\trials_20261010T1600Z_after_trend_pullback_dev_grid.sqlite`
     (sha256 `1a140ad3…`).
8. **Evaluate only the registered thresholds**; never open the sealed period.
   **Post-batch assessment written (2026-10-10), provisional, for review.** The pipeline
   already computed each trial's simulated P&L and summary metrics. This step applies the
   registered section 16 rules to the existing 54 runs, without simulating.
   - File: `docs/strategies/evidence/trend_pullback_2026-10-10_dev_analysis.md`, plus
     `_points.csv`.
   - Code: `scripts/trend_pullback_dev_analysis.py` (read-only, reproducible byte for byte)
     and `aurum.validation.walkforward` (selection, ties, drawdown, neighbours, DSR), tested.
   - The anchored walk-forward uses only trades entered before each fold, with ≥ 100 trades
     and exact tie-breaks. The selected points are:
     - m15: (80, 1, 2), (50, 0.5, 1.5), (30, 0.5, 1.5), (30, 0.25, 2);
     - m5: (80, 0.5, 1.5), (50, 0.25, 1.5), (50, 0.25, 1.5), (80, 0.25, 2).

     There were no ties.
   - Concatenated OOS: m15 has 698 trades, −0.36 USD/trade, PF 0.955; m5 has 685 trades,
     +0.98 USD/trade, PF 1.119.
   - Gates failed:
     - m15 fails 2, 3, 4, 7, 8, 9 and 12;
     - m5 fails 9 (`london_morning` −0.08 USD/trade) and 12 (DSR 0.343 < 0.95).
   - Not evaluated: gates 5 and 11 (stress and replay not authorised) and gate 10 on the
     frozen configuration (final selection not authorised; the neighbourhood is shown for
     information only).
   - Under section 16 each variant already fails at least one evaluated gate. The reviewer
     decides what follows. No final candidate is selected.

   **Reviewer conclusion (2026-10-11): trend_pullback 0.2.0 + A1 does not qualify for
   progression under the registered gates.**
   - **m15 rejected:** fails gates 2, 3, 4, 7, 8, 9 and 12.
   - **m5 rejected:** the OOS simulation is profitable, but it fails gates 9 and 12.
   - **No candidate progresses to the sealed test.**
   - Gates 5, 10 and 11 remain not evaluated; evaluation stopped after the mandatory failures.
   - **Gate 13 reporting corrected.** The literal ledger-wide condition ("no run whose range
     touches it") has historical exceptions: 10 `reference_fixed_time` trials, recorded
     2026-10-09 22:16 … 10-10 00:24 UTC. That is before the seal existed (D1, `b9baf4c`;
     loader refusal `552beed`, 09:59 UTC). The null-baseline runs are two of them. They are
     reported apart from the candidate-specific evidence: no trend_pullback run evaluated
     the sealed period, and there were 0 sealed openings and 0 parity accesses during the
     batch. No ledger row is deleted or rewritten.
   - **Preserved:** all 54 trials, attempts, run reports, hashes and both ledger backups.
   - **Not done, by decision:** removing `london_morning`, lowering the DSR threshold,
     choosing a different full-period winner, or rerunning this version to rescue it.
   - **Any future revision** of this family is a new, explicitly **post-results** hypothesis
     with additional search accounting (these 54 trials count). It is not an unseen-data
     validation.
   - **No additional simulation is authorised.**
   - The analysis document carries this conclusion (sections 5 and 6). The script was
     re-run read-only; only the analysis document changed. The batch evidence, index,
     contexts and points files were regenerated byte-identical.
9. ~~**Phase 3b replay only when triggered**~~: not reached (no candidate).
10. ~~**Select or reject the family**~~: **rejected** (2026-10-11, above).

Nothing in the spec changes after step 7 started. The result is "trend_pullback 0.2.0
failed".

### A3. Phase 5 family 2: opening_range_breakout (reviewer, 2026-10-11)

1. **Draft the specification only** (authorised). **Done, awaiting review:**
   `docs/strategies/opening_range_breakout.md`, draft 0.1.0. It follows the template and
   covers the hypothesis, exact timing and rules, execution assumptions, warm-up, the grid
   and search budget (12 trials), the stop-size basis and cap, and the evaluation thresholds.
   - Its section 0 lists the decisions the reviewer must make (O1–O8).
   - No strategy code, no backtest, no P&L, no ledger row.
   - Inputs used: descriptive counts from development-period bars only (bar dates ≤
     2025-09-30): range widths and the stop distance at the first close outside the range.
     No price after the breakout close was read, and nothing in the sealed period.
     The scratch script is not committed; the counts are in spec sections 8a and 15.
2. Not authorised yet: implementation, rule tests, any backtest or grid. **Claude stops for
   review of the draft.**

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
- **Review the opening_range_breakout draft spec** (queue A3) and decide O1–O8 in its
  section 0.

## Phase 5 conditions (from the reviewer; part of the gate)

- Write each strategy spec (`docs/STRATEGY_SPEC_TEMPLATE.md`) **before any code**, including the
  stop-size cap (§8a, derived per spec). Trend-pullback: drafted by Claude on 2026-10-10 at the
  reviewer's request, 0.2.0 approved 2026-10-10, rejected 2026-10-11 (queue A2).
  Opening-range breakout: draft 0.1.0 on 2026-10-11, awaiting review (queue A3). Then VWAP
  reversion.
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
  most one ~$20 trade a day, far from the $250 weekly limit). **Since 54c2470 the sealed
  period applies to the reference strategy too:** a baseline rerun or comparison for Phase 5
  ends 2025-09-30, the end of the development period.

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
- Test suite: 413 tests (404 passed, 9 skipped on the VM: 6 live-market checks with the market
  closed, 2 demo-order tests, 1 non-Windows test), ~2 min with coverage, 92.13% combined on
  the VM (2026-10-10, after the Phase 5 development analysis). Keep it lean (see `CLAUDE.md`). CI on Linux/macOS: 382 passed,
  11 skipped, 91.97% (item 6).
