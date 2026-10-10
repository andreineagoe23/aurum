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
  and their signs, both verdicts, and `phase_3b_required` (`Phase3bDecision.summary()`).
- `phase_3b_required` = share > 1% (strict) **or** sign(net worst) ≠ sign(net best) (rounded
  to cents, 0.00 its own sign) **or** verdict worst ≠ verdict best.
- Phase 3b required → the candidate has not passed. No manual override.
- Flagged trades but no best-case replay → the report fails closed
  (`BestCaseReplayMissingError`). With no flagged trade the worst-case run is also the best.

## Sealed final test

- The newest substantial period is sealed. Its date range is stored in config.
- Tooling refuses to evaluate on it unless `--open-sealed` is passed. Opening is logged.
- It is opened once, against the pre-registered threshold, after all tuning is finished.
- TODO(Phase 6): choose range, config key, and enforcement in the data-access layer (not only the CLI).

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
