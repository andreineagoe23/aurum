# Phase 5 report: baseline strategy lab

- **Closed:** 2026-10-11, by owner decision; report written 2026-10-12.
- **Exit gate:** **NOT MET** (MASTER_PROMPT §7, Phase 5). Every evaluated variant failed at
  least one registered section 16 gate, and vwap_reversion could not reach an evaluation.
- **Result:** all three planned families were rejected. **No deployable strategy was
  selected.** None reached the sealed period, which remains unopened.
- **Closure (V14, accepted at the vwap_reversion B0 review):** the three-family lab ends. No
  fourth family, rescue revision or ML work is authorised; any of them needs a separate owner
  decision recorded in `docs/STATUS.md`. The charter's kill criterion
  (`docs/PROJECT_CHARTER.md`) applies: stop, write up findings, revise hypotheses, and never
  add ML to rescue a failing baseline. Under V14, any hypothesis revision also needs that
  separate decision.
- Commits: 6f371d2 … this report, on `main`.

## 1. The registered process

Every family went through the same sequence. Claude stopped at each boundary, and one
approval never covered the next.

1. **Spec before code.** A spec from `docs/STRATEGY_SPEC_TEMPLATE.md`, with a stop-size cap
   (§8a), a fixed grid and the evaluation rules, reviewed and then frozen. Data-informed
   choices are listed as such.
2. **Feasibility (optional, descriptive).** Counts on development bars only, with no fill,
   exit or P&L.
3. **Implementation and deterministic tests** on synthetic data, then a real-data trace check
   with no P&L and no ledger.
4. **Development trials.** One ledger trial per declared grid point, over the development
   period **2020-12-15 … 2025-09-30** (amendment A1), each a ledger attempt first with its
   frozen evaluation context. One command per variant, no automatic retry.
5. **First-stage assessment**, read-only, of the existing runs under the registered section 16:
   anchored walk-forward over folds 1–4 with prior-only selection (≥ 100 prior trades),
   concatenated OOS trades, then gates 1–4, 6–9, 12 and 13. Any failure rejects the variant
   and stops evaluation; gates 5 (spread stress), 10 (neighbourhood) and 11 (best-case
   intra-bar replay) are run only if everything else passes.
6. **Decision** by the reviewer or the owner. The sealed final test (2025-10-01 … 2026-10-08)
   comes only after a candidate passes all of this, opened once.

Every variant pair is m15 (M15 decision bars) and m5 (M15 setup, M5 trigger), everything else
identical, as MASTER_PROMPT requires.

## 2. Trial accounting

| Family | Development trials | Attempts | Failures | Kind |
|---|---|---|---|---|
| trend_pullback 0.2.0 + A1 | 54 (27 per variant) | 54 | 0 | research trials, P&L |
| opening_range_breakout 0.1.0 | 12 (6 per variant) | 12 | 0 | research trials, P&L |
| vwap_reversion 0.1.0 draft rev. 4 | **0** | 0 | — | B1 feasibility diagnostic only, no P&L |
| **Cumulative research trials** | **N = 66** | | | |

- **N = 66** = 54 + 12. Rejected trials stay in the count. The 10 `reference_fixed_time`
  trials (null baseline, parity runs) are not research trials and are excluded. Gate 12 (DSR)
  used N = 54 for trend_pullback (the count at the time) and N = 66 for opening_range_breakout.
- **Development trials vs the VWAP diagnostic.** A trial is a ledger-recorded backtest of one
  grid point, with fills, exits and P&L. vwap_reversion's B1 was not a trial: a read-only
  script counted *geometry-eligible signals* on development bid bars (no risk engine, broker,
  quotes, fill, exit or P&L), and wrote nothing to the ledger. It does not add to N.
- Ledger at the opening_range_breakout post-run backup: 76 trial rows (66 research + 10
  reference), 66 attempts, integrity ok. Phase 5 made no ledger write after that.

## 3. Outcomes

### trend_pullback 0.2.0 + A1: rejected by the reviewer (2026-10-11)

H1 EMA trend, M15 pullback, trigger on the decision bars. Grid `h1_ema` {30, 50, 80} ×
`stop_atr_mult` {0.25, 0.5, 1.0} × `target_r` {1.0, 1.5, 2.0}. Concatenated OOS, folds 1–4:

| Variant | Trades | Net USD | Gross USD (mid) | Net USD/trade | PF | Max DD | DSR | Failed gates |
|---|---|---|---|---|---|---|---|---|
| m15 | 698 | −251.75 | 114.58 | −0.36 | 0.955 | 400.89 | 0.0175 | 2, 3, 4, 7, 8, 9, 12 |
| m5 | 685 | 667.97 | 1,056.22 | 0.98 | 1.119 | 358.50 | 0.3434 | 9, 12 |

m5 is profitable in simulation but depends on one session (`london_morning` −0.08 USD/trade)
and is far below the deflated-Sharpe bar. Gates 5, 10 and 11 were not evaluated. A future
revision would be a new post-results hypothesis whose search counts these 54 trials.

### opening_range_breakout 0.1.0: rejected by the owner under section 16 (2026-10-11)

08:00 London and 09:30 New York ranges, breakout with the opposite boundary + 20 points as the
stop. Grid `or_bars` {2, 4} × `target_r` {1.0, 1.5, 2.0}. Concatenated OOS, folds 1–4:

| Variant | Trades | Net USD | Gross USD (mid) | Net USD/trade | PF | Max DD | DSR | Failed gates |
|---|---|---|---|---|---|---|---|---|
| m15 | 1,166 | −271.05 | 258.78 | −0.23 | 0.958 | 417.47 | 0.0061 | 2, 3, 4, 7, 8, 9, 12 |
| m5 | 1,176 | 58.24 | 615.41 | 0.05 | 1.008 | 447.01 | 0.0354 | 3, 4, 7, 8, 9, 12 |

Both lose on the `london` range and on the short side. The positive `newyork` slices are
descriptive findings, not approved candidates. Gates 5, 10 and 11 were not evaluated.

**Costs.** All four OOS variants are positive gross (mid to mid). Costs (gross − net: spread,
commission, slippage) are 366–557 USD per variant and leave three of the four negative or near
zero. Only trend_pullback m5 keeps a material net result, and it fails gates 9 and 12.

### vwap_reversion 0.1.0 draft revision 4: rejected by the owner at B1 (2026-10-11)

Tick-weighted session mean, H1 efficiency-ratio regime, M15 setup at a new session extreme
`k_dev` ATR from the mean, rejection trigger, target at the mean. Grid `k_dev` {1.0, 1.5, 2.0}
× `er_max` {0.30, 0.50}, 12 planned trials. B0 accepted decisions V1–V17; B1 was authorised
with rulings R1–R6 and run once, then reproduced byte for byte.

- m15: 7–9 geometry-eligible signals per point; m5: 21–41.
- The maximum strictly before fold 1 is 3. No point reaches 100 prior signals at any
  selection boundary or over the whole period.
- Geometry-eligible signals are an upper bound on eventual trades, so no fold can select a
  point and no frozen configuration can exist under section 16. Most spends fail the stop
  minimum or the reward-to-risk rule (default m15: 391 spends → 287 `stop_below_minimum`,
  96 `reward_risk_below_min`, 7 signals).

The family was rejected as **structurally infeasible under its accepted rules**: a rejection,
not a freeze and not an implementation authorisation. B2–B9 did not run: no snapshot read,
implementation, config, registry, trace, backtest, P&L, ledger access, trial, parity access
or sealed access. The spec stays draft revision 4, never frozen, with a post-B1 disposition
(section 18d). Any revival would be a new, explicitly authorised, data-informed hypothesis,
not a continuation of 0.1.0.

## 4. Sealed period, parity and integrity

- **Sealed period (2025-10-01 … 2026-10-08): unopened.** No `--open-sealed` was used, and no
  Phase 5 run read a date on or after 2025-10-01: 0 sealed openings and 0 parity accesses in
  both trial batches; vwap_reversion's B1 excluded rows from 2025-09-30 22:00Z (R6) and opened
  no later partition. Disclosures that stay on record:
  - 10 `reference_fixed_time` trials (2026-10-09 22:16 … 10-10 00:24 UTC) cover that range.
    They predate the seal and are listed apart in each family's gate 13.
  - vwap_reversion's draft cites spread and intra-bar statistics measured before the seal
    existed on dates now inside it (V17, accepted explicitly).
- **Tester parity: still unresolved** (Phase 3, `docs/STATUS.md` queue C). The
  trend_pullback and opening_range_breakout trials ran while it was open. vwap_reversion
  never needed it: its parity decision was due at B5, and it stopped at B1, before any
  implementation or trial.
- **Reproducibility.** Both analysis scripts and the B1 script reproduce their outputs byte
  for byte. B1 runs only from `a09b196`: the script checks the spec's revision 4 git blob,
  and the post-B1 disposition changed the spec.

## 5. Evidence (committed)

| Family | Files under `docs/strategies/` | Key commits |
|---|---|---|
| trend_pullback | `trend_pullback.md`; `evidence/trend_pullback_2026-10-10_warmup_preflight.md`, `_dev_batch.md`, `_dev_index.csv`, `_dev_contexts.json`, `_dev_analysis.md`, `_dev_points.csv` | freeze `03421c0`; batch `382d671`; assessment `67969a0`; rejection `49a1130` |
| opening_range_breakout | `opening_range_breakout.md`; `evidence/opening_range_breakout_2026-10-11_feasibility.{md,csv}`, `_trace_check.md`, `_dev_batch.md`, `_dev_index.csv`, `_dev_contexts.json`, `_dev_launch.md`, `_dev_analysis.md`, `_dev_points.csv`, `_dev_dsr_trials.csv` | freeze `d06ca37`; batch `7bcca8b`; assessment `c9f3196`; rejection `ecd3b6f` |
| vwap_reversion | `vwap_reversion.md`; `evidence/vwap_reversion_2026-10-11_b0_audit.{md,patch}`, `_b1_feasibility.{md,csv}`, `_b1_run.md` | B0 `af49da8`; methodology `a09b196`; result `6c18e54` |

Scripts: `scripts/trend_pullback_dev_analysis.py`, `scripts/opening_range_breakout_*.py`,
`scripts/vwap_reversion_feasibility.py`. Shared selection and gate code:
`aurum.validation.walkforward`. Decision records: `docs/STATUS.md` queues A2, A3 and A4.

## 6. Open items carried forward

- **Tester parity** (queue C): the user's MT5 Strategy Tester run.
- **Tick collector stopped:** last heartbeat 2026-10-10 16:42Z, found 2026-10-11. Claude has
  not started or touched it. *(Stale since 2026-10-12 15:31:53Z: see the addendum below.)*
- Phase 6 has not started; it needs a strategy that passes Phase 5. No next step is
  authorised.

## Addendum (2026-10-12): correction to section 6

- **The collector item in section 6 is stale.** This report was committed (`4fc0a9c`) a few
  minutes before the owner restarted the live tick collector, from the dev venv, at
  **2026-10-12 15:31:53Z** (the collector's `started` event is 15:31:56Z). On restart it
  re-fetched the outage (2026-10-10 16:42:22Z … 2026-10-12 15:31:56Z, 62,997 market seconds)
  from the terminal's tick history: 200,104 rows, no `gap` events. It has run continuously
  since. Details: `docs/STATUS.md` queue C.
- The other section 6 items are unchanged: tester parity stays open (owner ruling of
  2026-10-12: it must close before the next P&L-producing run is authorised), and Phase 6 has
  not started.
- Nothing else in this report changes: no verdict, trial count (N = 66) or evidence.
