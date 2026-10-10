# Validation Protocol (Phase 0 skeleton)

Rules from MASTER_PROMPT Phases 5-6 and section 4 rules 9-11. Implementation details are marked TODO(Phase 6).

## Principles

- Every backtest and parameter trial is recorded in the trial ledger. Never report only the best result.
- Never tune against the sealed final test period.
- Never auto-promote a model or strategy into trading.
- Every strategy has a filled-in spec (`docs/STRATEGY_SPEC_TEMPLATE.md`) with pre-registered evaluation before any code runs.

## Trial ledger

- Append-only SQLite (`aurum.ledger.trial_ledger`). UPDATE and DELETE are blocked by triggers; each row carries a content hash and an immutable trial id.
- One row per run: strategy, variant, parameters, data range, costs, metrics, code/config hash.
- Reports must state the total trial count. The count feeds the Deflated Sharpe Ratio.
- TODO(Phase 6): ledger schema extensions for fold-level results and report linkage.

## Intra-bar sensitivity and the Phase 3b trigger (Phase 5 report contract)

Owner decision 2026-10-10; ADR-0008 amendment, ADR-0009 §9; rule in
`aurum.validation.phase3b`.

- Every reported candidate runs under `intrabar_policy: worst` (the verdict run) and is
  replayed under `intrabar_policy: best` on the same data, parameters, costs and risk
  configuration. The best case is a separate deterministic replay, never edited trades. Both
  runs go in the trial ledger, linked to each other.
- The report shows: worst-case count and share (÷ total closed trades), net P&L worst and best
  and their signs, both verdicts, `best_case_replay_required`, `best_run_reused` and
  `phase_3b_required` (`Phase3bDecision.summary()`).
- Net P&L is quantized to cents by `phase3b.to_cents` only: decimal `ROUND_HALF_UP` (half a
  cent away from zero, ±0.005 → ±0.01; anything closer to zero → 0.00, never −0.00).
  NaN or ±infinity is rejected when the run is recorded (`IntrabarRun`).
- `phase_3b_required` = share > 1% (strict) **or** sign(net worst) ≠ sign(net best) (in
  cents, 0.00 its own sign) **or** verdict worst ≠ verdict best.
- Phase 3b required → the candidate has not passed. No manual override.
- Flagged trades but no best-case replay → the report fails closed
  (`BestCaseReplayMissingError`). With no flagged trade the worst-case run is also the best,
  and the summary says so: `best_case_replay_required: false`, `best_run_reused: true`. With a
  supplied replay, `best_case_replay_required` = (worst-case run has flagged trades) and
  `best_run_reused: false`.

## Stop-size cap (Phase 5 report contract)

Reviewer condition 2026-10-10; `docs/STRATEGY_SPEC_TEMPLATE.md` §8a.

- Every spec states its stop-size cap: points and price, ≤ the minimum-volume affordability
  ceiling derived from its own stated inputs (reference equity, risk %, symbol point,
  `volume_min`, `volume_step`, money per price unit per lot, commission, stop slippage).
  There is no global cap.
- A setup whose stop is above the cap emits no signal. The stop is never clamped, shortened
  or moved to fit.
- Each candidate report shows `stop_above_cap`, the number of setups rejected for this reason.
  It sits next to the trades taken and the risk engine's rejections, which are counted
  separately (`sized volume < volume_min` and the other gates).

## Sealed final test

Owner decision D1, 2026-10-10; implemented in `aurum.backtest.sealed` and
`aurum.backtest.loading`.

- **Period:** 2025-10-01 … 2026-10-08, in `configs/backtest.yaml` `sealed_period` (required).
- **Every backtest** (research and reference alike):
  - a request whose trading dates overlap the period is refused (`SealedPeriodError`);
  - the loader refuses, **before reading**, any bar or tick file dated inside it, including
    warm-up history and the extra end day;
  - a run ending just before it loads data only up to the day before it.
- **Opening:** `aurum backtest run … --open-sealed --by NAME --reason TEXT`, only for a
  research strategy whose config has an approved `spec:`.
  - It is journalled in the trial ledger (`sealed_openings`, append-only) **before** any data
    is read, so a crash still counts.
  - It is allowed **once per strategy and variant** (UNIQUE), and the trial row gets
    `sealed_test_opened = true`.
  - A request that does not overlap the period cannot spend the opening.
- **Parity exemption** (`sealed_period.parity_exemption`): only the MT5 Strategy Tester
  comparison, with
  `aurum backtest run reference_fixed_time … --parity --variant m5 --risk fixed
  --latency-ms 0 --slippage-points 0`.
  - It is limited to that strategy and variant, with trading dates inside 2026-09-14 …
    10-08, and no calendar warm-up. The loader still refuses every sealed date outside the
    window.
  - Each access is journalled first in `parity_accesses`, apart from research trials and
    openings; the trial row carries `purpose: parity`.
  - It never spends an opening. Any other strategy, variant, date or context is refused,
    and ordinary reference backtests are sealed like research ones.
- **When:** opened once, against the pre-registered threshold, after the candidate and its
  parameters are frozen.
- **Reporting:** the result shows the exact-tick subset (2026-06-26 onward, trades with
  `entry_mode = ticks`) separately from the bar-only part.
- **During Phase 5** nothing inside it is inspected: no descriptive counts, signals, trades or
  P&L.

## Walk-forward

- Anchored or rolling windows; choose per strategy in the spec and do not change after seeing results.
- Parameters are fitted only inside training windows.
- Only out-of-sample trades are concatenated for reporting.
- TODO(Phase 6): window lengths, step, runner.

## Purging and embargo

- Any label or trade that spans a split boundary is purged from training.
- An embargo gap follows each test window.
- TODO(Phase 6): embargo length, based on max holding time.

## Stress tests

- Spread x1.5 and x2.0.
- Extra slippage points per side.
- Missed fills.
- Added latency.
- Earlier force-flat cutoff.

## Parameter neighbourhood stability

Produce heatmaps around the chosen point. Prefer plateaus. A result that exists at a single parameter point is rejected.

## Metrics

Net expectancy, profit factor, max drawdown, time under water, Sharpe, Sortino, return/drawdown, trade count, holding-time distribution, MAE/MFE; splits by year, month, session, direction and regime; trial count; Deflated Sharpe Ratio; probability of backtest overfitting. Report gross vs net with spread, commission and slippage attribution.

## "Too good to be true" rule

If Sharpe > 3 or profit factor > 2.5 on intraday gold, assume a bug or look-ahead leak first. Investigate (timestamps, as-of joins, fill prices, cost model) before reporting anything.

## Exit-gate checklist (Phase 6)

- [ ] All trials recorded in the ledger; trial count reported.
- [ ] Positive net expectancy under conservative costs (spread x1.5 / x2.0, extra slippage).
- [ ] Stable parameter neighbourhood.
- [ ] No dependence on one period, one month, one direction or one session.
- [ ] Walk-forward OOS results meet the pre-registered thresholds.
- [ ] Deflated Sharpe Ratio and PBO reported.
- [ ] Sealed test opened once, logged, and passes the pre-registered threshold.
- [ ] Report written to `reports/`.
