# ADR-0008: Dukascopy XAUUSD ticks as a separate, labelled research source

- **Status:** **Accepted (deferred).** Approved in principle by the owner on 2026-10-09; not
  implemented. See "Decision" for the conditions.
- **Date:** 2026-10-09

## Context

Measured in Phase 2 (reports/phase_2.md §1, ADR-0007):

- Fusion M1..H1 bars: continuous from **2020-11-12** (≈5.9 years). Enough for bar-level
  research.
- Fusion ticks: from **2026-03-29** (≈6.3 months), and only the newest ≈3.5 months reproduce the
  broker's own bars (tick fidelity `exact`, 90 days). That is far below the ~2–3 years needed for
  tick-level work: intra-bar stop/target ordering in the simulator (Phase 3), slippage and
  latency studies, and tick-based features.

Dukascopy publishes free historical XAU/USD tick data (bid/ask with volumes, from about 2003)
over its public datafeed. It is a different liquidity pool from Fusion's, so its prices and
spreads are **not** Fusion's.

## Proposal

1. **Purpose, narrowly:** tick *paths* for research only, i.e. the order in which prices moved
   inside a bar, so Phase 3 can resolve stop-vs-target inside a bar on days without exact Fusion
   ticks, and check robustness on 2020–2026. **Never** for spreads, costs, fills or calibration
   (risk limits, slippage). Fusion's own ticks and bars stay the source of truth for those.
2. **Separate storage, never mixed silently:** `raw_ticks/dukascopy/XAUUSD/<date>.parquet`
   (immutable, same rules as ADR-0006), `normalized_ticks_dukascopy/…`, and a `source` column
   with value `dukascopy` on every row. Canonical bars stay Fusion-only. Any dataset that
   combines sources must say so in its name and keep the `source` column.
3. **Ingestion:** a new adapter `aurum.adapters.dukascopy` (download the hourly `.bi5` files,
   decompress with LZMA, decode the big-endian records, convert prices with the instrument's
   point value; times are already UTC), bounded and resumable like `aurum data backfill`. It
   would need one new dependency at most (an HTTP client; the standard library may do).
4. **Validation before use:** for the overlap with Fusion `exact` days, measure how often
   Dukascopy's intra-minute ordering of highs and lows agrees with Fusion's, the price offset
   between the feeds, and the timestamp alignment. Proceed only if agreement is high; otherwise
   stop and report.
5. **Licensing:** confirm that Dukascopy's terms allow personal research use before
   downloading at scale.

## Alternatives

- **Only Fusion ticks, accumulate forward:** honest and simple, but it takes years to reach a
  useful depth. Bar-level research can still start on 5.9 years of bars, using worst-case
  intra-bar assumptions (MASTER_PROMPT Phase 3: stop first, flagged).
- **Another broker's MT5 demo ticks:** same depth limits as Fusion are likely.

## Decision (owner, 2026-10-09)

Approved in principle, **deferred**:

1. Phase 3 is built on Fusion data only, with the worst-case intra-bar rule (stop first) on days
   without `exact` ticks. Every trade that depends on that rule is flagged, and the Phase 3
   report states their share.
2. If that share is material, Dukascopy ingestion becomes **Phase 3b**, and only if both hold:
   a. Dukascopy's terms are confirmed to allow personal research use;
   b. the validation in proposal item 4 passes against Fusion `exact` days.
3. Until then nothing in this ADR is implemented and no Dukascopy data is downloaded.

## Amendment (owner, 2026-10-10): what "material" means

"Material" in Decision 2 is decided per Phase 5 candidate by an exact rule
(`aurum.validation.phase3b.decide_phase_3b`):

```text
phase_3b_required = worst_case_share > 1%
                 or sign(net_worst) != sign(net_best)
                 or verdict_worst != verdict_best
```

- `worst_case_share` = trades flagged by an intra-bar rule (ADR-0009 §5) ÷ **total closed
  trades** of the worst-case run. Strictly greater: exactly 1.00% does not trigger, 1.01% does.
- `net_worst`/`net_best`: net P&L of the worst-case run and of its best-case **replay**
  (ADR-0009, "Best-case replay"), rounded to cents; 0.00 is its own sign, separate from
  positive and negative.
- `verdict_*`: the candidate gate's pass/fail on each run.

If any condition holds, Phase 3b is required for that candidate and it has **not passed**;
no manual override can declare it passed. The conditions in Decision 2 (licence, validation
against `exact` days) still apply before any Dukascopy data is used.
