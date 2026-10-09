# ADR-0007: Canonical bars follow the broker's M1 where stored ticks do not reproduce it

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

The master prompt asks for deterministic resampling ticks → M1 → M5/M15/H1 and a comparison with
the broker's bars. Fusion's server serves ticks from 2026-03-29 and M1 bars from 2020-11-12
(reports/phase_2.md §1). Building M1 from the stored ticks and comparing it with the broker's M1
for the same minutes gave (2026-10-09):

| Tick days | M1 bars from ticks equal to the broker's M1 |
|---|---|
| 2026-06-26 … 2026-10-08 (90 days) | **all of them**: OHLC, bar count, `tick_volume`, minimum spread |
| 2026-03-29 … 2026-06-25 (76 days) | **none fully.** 5.3% of minutes differ in OHLC (up to $19.72 at 2026-04-07 22:33 UTC); 340 broker minutes have no ticks at all (2026-05-21, 05-22, 06-21) |

In the differing minutes the broker's `tick_volume` exceeds the number of bid-changing ticks it
now serves (for example 471 vs 280). The broker built those bars from more ticks than its tick
history still holds: the older tick history is **thinned**, with holes. The cut-off at roughly
3.5 months suggests the server compresses ticks as they age, but that is an inference. Re-fetching
an old day later will show whether the boundary moves.

Also measured: the broker's bar `spread` is the **minimum** spread in the bar (equal to the tick
minimum in 99.85–100% of minutes), and `tick_volume` counts **bid-changing** ticks (flag bit 2).

## Decision

1. Per UTC day, build M1 from the (normalized) ticks and compare it with the broker's raw M1.
   - Exactly equal → tick fidelity **exact**; the tick-built M1 is canonical
     (`source = mt5_history` or `mt5_live`).
   - Any difference → tick fidelity **incomplete**; the broker's M1 is canonical
     (`source = mt5_bars_m1`). The broker's bars come from the full feed, so they are closer to
     what a live system saw. Shadow/demo parity (MASTER_PROMPT §8) needs that.
   - No broker M1 for the day → **unverified**; tick-built M1 is canonical.
2. Normalized ticks are written for every tick day regardless. `canonical_bars/days/<symbol>.parquet`
   records, per day, the canonical source, the tick fidelity and the mismatch counts. Phase 3 must
   use tick-level replay (intra-bar SL/TP ordering) only on `exact` days, or flag the result.
3. Canonical `tick_count` follows the MT5 convention (bid-changing ticks), `quote_count` counts
   all ticks, and bars built from broker M1 carry only `spread_min` (other spread columns null).
4. Spread statistics for calibration use `exact` days only (DATA_DICTIONARY, `aurum data spreads`).

## Consequences

- With this rule every canonical bar on every day equals the broker's own bar at all four
  timeframes (0 differences over 2,157 days). The comparison is part of each `aurum data
  resample` report.
- Tick-level research on Fusion data is limited to `exact` days (90 so far). Days captured by
  the live collector and the daily backfill add exact days over time. Fetching ticks while they
  are fresh is the only way to keep them at full fidelity.
- This is one more reason to consider an independent tick source for longer tick history
  (ADR-0008, proposed). Fusion's own ticks remain the reference for spreads and costs.
