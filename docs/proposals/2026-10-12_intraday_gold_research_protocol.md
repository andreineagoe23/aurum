# Aurum: intraday gold research protocol proposal

Status: PROPOSAL, NOT ADOPTED. Written 2026-10-12 after the owner authorised
"G-B plus research proposal"; that authorisation covers G-B implementation and public-evidence
research, not a fourth family, strategy implementation, new gates, data access, trials or a push.

Repository baseline: `d570119ca55bb1685f0c7653db577690819326e3`. Read with
`docs/STATUS.md`, which wins in any conflict, and
`docs/proposals/2026-10-12_process_lessons.md`, which remains unadopted.
This document uses repository records, public methodology papers and platform documentation.
No Aurum market data, production ledger, snapshots, reserve or sealed files were inspected,
and no candidate or trading-performance estimate was produced.

## Recommendation

Treat the owner's desired 70–75% wins as an aspiration to test, not a number to engineer into
a backtest. My recommendation is to preserve the closed Phase 5 results, complete the
approved protection work, and agree a prospective evidence contract before considering
another family.

The immediate deliverable is an evaluation protocol, not trading rules. There is no evidence
in this review that establishes a profitable Aurum strategy or makes a 70–75% live win rate
achievable; none of the reviewed literature is a validation of this bot.

## What success should mean

### Profitability before hit rate

For a simple hypothetical model, let \(p\) be the win probability, \(W\) the mean gross win,
\(L\) the absolute mean gross loss, and \(c\) the mean round-trip cost, measured in the same
units. Then expected net profit per trade is
\[
E = pW-(1-p)L-c,\qquad p_{\mathrm{break-even}}=\frac{L+c}{W+L}.
\]
This is arithmetic, not an estimated Aurum result. Real assessment must use the actual
distribution of net outcomes, rather than assume constant trade sizes or costs.

- **Example:** 75 wins of £1 and 25 losses of £4 give a £25 loss before costs.
- **Example:** with \(W=L=1R\) and \(c=0.1R\), break-even is 55%; with
  \(W=0.5R,\ L=1R,\ c=0.1R\), it is about 73.33%.
- **Proposed priority:** positive net expectancy under the already-required cost stresses,
  acceptable drawdown and tail losses, reproducible execution, and only then the hit-rate
  preference. Do not loosen the existing risk or validation rules to obtain that percentage.

For a future preregistration, I recommend defining a win as strictly positive realised P&L
after all costs for one complete position lifecycle, with net-zero positions counted as
non-wins in the headline denominator. Include all eligible closed positions, not just fills
with convenient outcomes; report skipped signals, rejected orders, cancelled/unfilled orders,
and incomplete positions separately with an explicit evaluation-end treatment.

This definition is a proposal, not a change to current metrics or historical results. A
future spec must settle partial closes, multiple exits and holding-period accounting before
results are seen, so splitting one winning position into several fills cannot inflate the
headline rate.

### Evidence behind a percentage

A binomial proportion needs an uncertainty interval, rather than just its observed fraction;
NIST describes the Wilson construction for this purpose
([NIST, Proportion Confidence Interval](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/propconf.htm)).
The following are my calculations for independent, equal-probability hypothetical binary
outcomes using that two-sided 95% construction, not Aurum observations
([NIST, Wilson method](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/propconf.htm)).

| Hypothetical closed positions | Hypothetical winners | Observed fraction | Two-sided 95% Wilson interval |
|---|---|---|---|
| 100 | 75 | 75% | 65.70–82.45% ([calculation method](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/propconf.htm)) |
| 300 | 225 | 75% | 69.80–79.56% ([calculation method](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/propconf.htm)) |
| 1,000 | 750 | 75% | 72.22–77.58% ([calculation method](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/propconf.htm)) |

These illustrations do not establish that trading observations satisfy the binomial model.
For any future protocol, assess temporal dependence and regime change before interpreting
such intervals as evidence of a persistent trading advantage.

Block-based inference is a relevant methodological alternative: Politis and Romano's
stationary bootstrap addresses weakly dependent stationary observations, not arbitrary
nonstationary market histories
([Politis and Romano, 1994](https://www.tandfonline.com/doi/abs/10.1080/01621459.1994.10476870)).
My recommendation is to preregister the dependence treatment and block-length choice, retain
the existing protocol until any amendment is approved, and explicitly report when the
available sample cannot support the desired claim.

- **Fixed horizon:** set the sample sufficiency calculation, evaluation dates and stop rules
  prospectively. Do not repeatedly check until a confidence bound happens to pass.
- **Honest alternatives:** a positive-expectancy result below the desired hit rate and a
  high-hit-rate result with negative expectancy must be reported differently. Neither can
  be relabelled as satisfying both objectives.
- **No arbitrary sample gate here:** the illustrative counts above are not proposed
  deployment thresholds. A future design must account for effect size, payoff variability,
  dependence, selection and the available independent trading days.

## What public evidence contributes

### Intraday event risk, not a ready-made signal

Elder, Miao and Ramchander's June 2011 working-paper version studies gold, silver and copper
futures over 2002–2008 and finds rapid reactions to scheduled US news; its findings concern
returns, volatility and volume rather than a tested after-cost trading strategy
([Impact of macroeconomic news on metal futures](https://api.mountainscholar.org/server/api/core/bitstreams/021aba79-eb08-40ca-85f4-5e9716c424d5/content)).
The sample and instrument are historical futures, not evidence about current Aurum broker
fills, retail XAUUSD spreads or live profitability
([study scope and methods](https://api.mountainscholar.org/server/api/core/bitstreams/021aba79-eb08-40ca-85f4-5e9716c424d5/content)).

My implication is to treat news timing and execution risk as prospective validation
requirements, not an invitation to invent an announcement strategy or change the existing
blackout. A future proposal would need an economic mechanism and a separate, owner-approved
test before any claim of an exploitable effect.

### Venue and session differences need transfer validation

Hauptfleisch, Putniņš and Lucey compare London OTC spot quotes and New York COMEX futures in
a 1997–2014 sample, finding that the futures market leads overall in price discovery
([Who Sets the Price of Gold? London or New York](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/29790/T-Putnis-GoldILS-v4.3.pdf)).
That study does not report a profitable, after-cost trading strategy and does not validate a
retail CFD execution model
([paper methods and conclusions](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/29790/T-Putnis-GoldILS-v4.3.pdf)).

My recommendation is not to import a futures result as proof of an XAUUSD edge or call the
London/New York research anchors a unique gold-market opening. No cross-market feed,
venue-arbitrage family or session-filter revision is proposed for implementation here.

### Tester settings are part of the experiment

MetaQuotes documents real-tick versus generated-tick differences, including within-minute
spread variation and history-dependent handling of missing ticks or minute bars
([MetaTrader 5, Real and Generated Ticks](https://www.metatrader5.com/en/terminal/help/algotrading/tick_generation)).
Its tester also exposes execution-delay and account/commission settings, so a test's mode
and configuration are material to interpreting its output
([MetaTrader 5, Strategy Testing](https://www.metatrader5.com/en/terminal/help/algotrading/testing)).

My implication is to keep tester parity as an engineering prerequisite, not describe it as
proof of an edge. Actual broker coverage, spread, fills and latency remain unverified in this
session; no tester, terminal, market-history request or measurement was run.

## Selection control and the existing process-lessons proposal

Bailey and López de Prado's DSR construction addresses selection and non-normality, with
trial-count and trial-variance assumptions; it does not justify choosing whichever penalty
makes an observed candidate pass
([The Deflated Sharpe Ratio, July 2014 version](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)).
Their discussion of correlated trials is a methodological issue to resolve prospectively,
not permission to reset Aurum's cumulative search history
([DSR assumptions and dependent trials](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)).

Bailey, Borwein, López de Prado and Zhu's PBO framework assesses selection overfitting among
the supplied configurations, and cannot repair incorrect transaction-cost or information
assumptions in the underlying backtests
([The Probability of Backtest Overfitting, February 2015 version](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)).
The paper also warns against making the overfitting measure itself an optimisation target
([PBO limitations](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)).

The following are recommendations for an owner decision, not adoption of any item:

- **Sample feasibility, proposal item 1:** recommend a precommitted, separately authorised
  structural-feasibility stage before any future spec and implementation. Compare the
  expected trade rate with the selection rule's implied sample floor; a necessary-condition
  count would be data-informed and would need its own exact read authorisation. Do not
  adjust the floor after an observed failure.
- **DSR policy, item 2:** retain cumulative N and mixed V as currently ruled.
  Before any future search, specify the counted universe, variance population, dependency
  assumptions and any prospective amendment. No ledger reread, re-scoring, historical
  verdict change, or search-history reset is authorised by this document.
- **Slippage provenance, item 3:** recommend labelling every cost input as measured or
  provisional, with its method and population. Any future reserve quote-movement study
  needs a precommitment and separately authorised data handling; quote movement during
  latency must not be relabelled as observed broker fill slippage. The proposal's
  approximate November measurement date does not override S3's December review or the
  first eligible reserve trading date. No current cost setting changes.
- **Seal guard, item 4:** the narrowly chosen G-B guard is implemented separately under the
  present authorisation. This does not adopt the proposal's broader ban on ordinary grids,
  implement lifecycle-transition approval machinery, or allow another candidate to be frozen.
- **Pre-seal calibration disclosure, item 5:** recommend carrying forward the proposal's
  table of cost settings measured before the seal existed on dates now within it. The seal
  is unopened for a strategy verdict, but that does not mean these dates were never read
  historically. Future-family recalibration requires its own approval and cannot alter
  historical inputs or rescue past verdicts.

Separately, I recommend a prospective report contract before future evaluation: code/config
identity, allowed data bounds, complete trial accounting, gate vector, cost provenance,
uncertainty and omissions. This adds no reporting code and rescores no existing result.

## Prospective approval sequence

This sequence preserves STATUS; it does not bring deferred items forward. It separates
infrastructure acceptance from the decision to resume strategy research.

- **G-B review and delivery:** review the local changes and synthetic-test evidence, then
  separately authorise any push and deployment. `status: frozen` is an eligibility check,
  not proof of signed owner approval or permission to trade.
- **Tester parity:** after G-B is accepted and available in the intended environment,
  obtain separate approval for the exact reference run and any necessary data reads.
  Synthetic parity regression coverage does not close Phase 3 parity.
- **Reserve processing:** retain S3's 2026-12-15 review unless explicitly advanced by the
  owner. Before reserve QA, approve a bounded implementation that neither opens nor
  rewrites sealed files or sealed-day manifest rows.
- **Operational readiness:** collector release-venv/logon work, S2a, S2b, VM_SETUP and
  CRLF identity stay separate, untouched tasks. No task should be registered from the
  currently conflicting VM_SETUP instructions.
- **Programme decision:** only a later explicit owner ruling can authorise another family
  or revise the research protocol. That decision must identify what development evidence
  may be used; the forward reserve is not a substitute development or tuning set.
- **Future experiment, if approved:** pre-register the mechanism, finite search budget,
  data boundaries, feasibility criteria, cost model, fixed evaluation horizon, all
  acceptance gates and failure/stop conditions. Then seek the separate implementation,
  data-access and run approvals required by STATUS.

Forward data from 2026-10-09 remains reserve-only; trading date 2026-10-09 stays partial
and excluded, and the first reserve trading date remains 2026-10-12. No sealed file may
be read for warm-up; the recorded mid-November estimate concerns warm-up accumulation,
not statistical sufficiency or permission to run ahead of S3.

Reserve QA and spread/latency measurement must remain within their precommitted operational
purpose, with any conclusions confined to future preregistered families. They must not
become a covert signal-discovery exercise, retrospective strategy filter or re-scoring of
the 66 trials.

## Decision requested after review

Review G-B for delivery first, then decide whether any individual prospective process
recommendation should be adopted. Keep the programme paused in the meantime; this proposal
does not authorise a fourth family, rescue revision, ML work or a trading deployment.

The aim is a bot with demonstrated positive net expectancy and controlled risk, with
70–75% wins only if the evidence supports it. The present result is better protection and
a research proposal, not a profitable bot or a performance guarantee.
