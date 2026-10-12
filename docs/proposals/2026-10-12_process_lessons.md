# Process lessons from the three-family lab (2026-10-12)

> **PROPOSAL, NOT ADOPTED.** Written after the read-only post-closure review of 2026-10-12,
> at the owner's request (queue D, S1). Nothing here changes a verdict, the trial count
> (N = 66), a registered gate, a frozen spec or any code or config. Each item needs its own
> owner decision. Under owner ruling 3 (2026-10-12) the programme is paused to collect data:
> no fourth family is authorised. Any item adopted applies only to future families and is
> fixed before their results; none re-scores the 66 trials or revives a rejected family.

## 1. Check sample size and trade rate before drafting a spec

**What happened.** vwap_reversion went through four draft revisions, an independent audit and
500 synthetic test cases before B1 showed it could never be selected: at most 3 geometry-eligible
signals per point before fold 1, against the 100 that selection needs
(`docs/strategies/vwap_reversion.md` section 18d).

**Why it was predictable.** The registered selection rule (trend_pullback and
opening_range_breakout section 16) picks, for each fold, among grid points with **≥ 100
development trades before that fold**. Fold 1 starts 2021-10-01, so the only trades before
it are in the training-only window 2020-12-15 … 2021-09-30, about 9.5 months. A grid point
therefore needs roughly **100 ÷ 9.5 ≈ 10.5 trades per month** from the start, or fold 1 has
no eligible point and gate 1 fails. That floor is much tighter than gate 1's own sample rule
(≥ 30 per 12-month fold, ≥ 200 in total, about 2.5 a month). It silently excludes
low-frequency hypotheses.

**Proposal.**

- Add a pre-spec step (before B0): a one-page hypothesis note with the expected trade rate,
  checked against the implied floor, plus a necessary-condition count on development bars
  only (signals or setups; no risk engine, fill, exit or P&L). The count is data-informed and
  is disclosed as such.
- `docs/STRATEGY_SPEC_TEMPLATE.md` gains a line stating the minimum trade rate per grid point
  that the evaluation rules imply, and the hypothesis's expected rate next to it.
- If a hypothesis is naturally low-frequency, any change to the window or selection rule is
  decided and registered **before** drafting, never after a count fails.

## 2. N and V across families (DSR, gate 12)

**Current policy** (owner ruling 4: kept for now). N is cumulative over all research trials:
54 for trend_pullback, 66 for opening_range_breakout, reference trials excluded. V is the
sample variance (ddof 1) of every listed trial's daily Sharpe ratio, across families.

**Effects.**

- Each new family raises N, and with it the benchmark `SR0`, for every later family.
- Mixing families in V raises it whenever families differ systematically, which raises
  `SR0` further. Recorded values: V = 4.709e-04 over the 54 trend_pullback trials, giving
  `SR0` = 0.050 per day; V = 6.293e-04 over the 66 trials for opening_range_breakout.
- Order matters: an identical family evaluated later faces a higher bar.

**Options for a decision** (not proposed for any past verdict):

- (a) keep cumulative N and mixed V (current);
- (b) cumulative N, V computed within the family under test;
- (c) an effective N from clustering correlated trials (López de Prado's approach), with
  the clustering rule registered before results.

Whatever is chosen is written into the next spec's section 16 before any of its trials run.
Gate 12 was not the only failure for any evaluated variant: each also fails gate 9.

## 3. Where slippage figures come from

**What is modelled.** A fixed slippage of **5 points per side** (`configs/broker.yaml`,
provisional), on market entries and stop or close exits, not on target fills
(`reports/phase_3.md` §4). It is charged on top of the latency-delayed quote
(`configs/backtest.yaml` `latency_ms`). It is the Phase 0 placeholder (`reports/phase_0.md`,
issue 4), to be replaced in Phase 8. It was never measured.

**Weight.** Summed over every development trial's full run (committed evidence files
`trend_pullback_2026-10-10_dev_points.csv` and
`opening_range_breakout_2026-10-11_dev_points.csv`):

| Family | Trades (all trials) | Spread USD/trade | Slippage USD/trade | Commission USD/trade | Slippage share of costs |
|---|---|---|---|---|---|
| trend_pullback (54) | 45,798 | 0.14 | 0.25 | 0.12 | 49% |
| opening_range_breakout (12) | 15,327 | 0.12 | 0.23 | 0.10 | 51% |

The unmeasured slippage is about half of all modelled costs. That is conservative for
falsification but may produce false negatives. **This is not a reason to re-score any trial.**

**Proposal.**

- Every spec lists each cost input with its provenance: measured (dataset, dates, method) or
  placeholder.
- Under owner ruling 1, and only after a pre-commitment is recorded, measure quote movement in
  the latency window at decision instants on forward-reserve ticks (≥ 2026-10-09), with
  enough data (about one month, from about 2026-11-09). The pre-commitment states that the
  result applies only to future pre-registered families. Real fill slippage still comes from
  Phase 8 demo orders.

## 4. Code guard for rejected strategies

**Gap.** `aurum.backtest.sealed` allows `--open-sealed` for any research strategy whose config
has `spec:` set. The rejected trend_pullback and opening_range_breakout keep `spec:` (it is
also the record of their spec), so only process stops either from spending its one-time
opening.

**Proposal** (accepted in principle, owner ruling 6; a later code change needing its own
approval):

- add a validated lifecycle field to strategy configs, e.g. `status: draft | frozen |
  rejected`;
- `--open-sealed` requires `status: frozen`. A rejected strategy is refused before any
  journal row, with the reason named, and `backtest grid` refuses it too;
- one named safety test (rejected → refused, no ledger row); no other test changes.

## 5. Cost settings drawn from data now inside the seal

All of these were measured **before the seal existed** (decision D1, 2026-10-10) on dates now
inside it (2025-10-01 … 2026-10-08). None was tuned on strategy performance, but together they
make the cost model partly calibrated on the hold-out. Until now they were disclosed in
separate places:

| Setting or figure | Measured on | Used for | Source |
|---|---|---|---|
| `risk.max_spread_points: 20` | p99 of spreads at M5 decision instants, exact days 2026-06-26 … 10-08 | Spread gate in every policy-risk backtest | `reports/phase_2.md` §5, `reports/phase_3.md` §2 |
| `backtest.bar_spread_extra_points: 2` | First-quote spread minus bar minimum, exact days 2026-06-26 … 10-08, London sessions | Spread assumed for fills on bar-only days | `configs/backtest.yaml` comment |
| 2026 intra-bar ambiguity rates | Bar days January–June 2026 and exact days 2026-06-26 … 10-08 | Basis for the Phase 3b trigger proposal (1% of trades) | `reports/phase_3.md` §4 |
| vwap_reversion V17 statistics | Spread and intra-bar statistics on dates now sealed | Draft design choices, accepted with disclosure | `docs/strategies/vwap_reversion.md` |

**Proposal.**

- Any sealed-test report carries this table as a standing disclosure next to its result.
- A recalibration from forward-reserve data (ruling 1, under pre-commitment) may replace
  these values **for future families only**. Past runs keep the values they ran with.

## Not proposed

- No change to any registered gate, threshold, fold or verdict of the 66 trials.
- No new family, rescue revision, ML or Dukascopy work.
- No use of forward-reserve data for development or tuning (ruling 1).
