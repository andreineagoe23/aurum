# trend_pullback 0.2.0: refused pre-flight and warm-up evidence (2026-10-10)

Evidence for evaluation-period amendment A1 (`docs/strategies/trend_pullback.md`, section 16).
Nothing here is an evaluation: no decision, signal, trade or P&L was computed, and no price
was read. The bar counts are Parquet row counts per stored UTC day.

## 1. The refused pre-flight run

- **Approved command (reviewer, 2026-10-10):** `uv run --no-sync aurum backtest grid
  trend_pullback --variant m15 --from 2020-11-12 --to 2025-09-30`
- **Code:** `f5b2744` (HEAD = origin/main, clean worktree).
- **Started:** 2026-10-10T14:21:17Z. **Finished:** 2026-10-10T14:21:18Z.
- **Log**, verbatim (`reports/grid_logs/m15.log`, kept, git-ignored):

  ```
  warm-up needs 504 completed H1 bars before 2020-11-12; only 0 in the store
  exit 1
  ```
- **Refused in `pipeline.prepare`, before any attempt.** The trial ledger
  (`C:\aurum-data\ledger\trials.sqlite`, read-only check) still had only `trials` (10 rows,
  none for trend_pullback) and no `attempts`, `grid_batches`, `sealed_openings` or
  `parity_accesses` table. No run files were staged.
- The m5 grid was not started: it needs the same warm-up.
- **Ledger backup taken before the run:**
  `C:\aurum-data\ledger\backups\trials_20261010T1422Z_before_trend_pullback_dev_grid.sqlite`
  (sqlite backup API, `integrity_check` ok, 10 trials, sha256
  `0c0171cac0e284aaadf7698157be0c03dda511d6d319c31f5bbcb810e6329076`).

## 2. Warm-up history in the store (metadata only)

The store starts on 2020-11-12. The spec needs 504 H1, 509 M15 and 3 M5 completed bars
before the first decision. The loader counts whole UTC days before the first decision's UTC
date (London midnight; GMT in December, so 00:00 UTC).

```
H1: completed bars per stored UTC day (Parquet row counts only)
  2020-11-12 Thu   23 cumulative   23
  2020-11-13 Fri   22 cumulative   45
  2020-11-14 Sat    0 cumulative   45
  2020-11-15 Sun    1 cumulative   46
  2020-11-16 Mon   23 cumulative   69
  2020-11-17 Tue   23 cumulative   92
  2020-11-18 Wed   23 cumulative  115
  2020-11-19 Thu   23 cumulative  138
  2020-11-20 Fri   22 cumulative  160
  2020-11-21 Sat    0 cumulative  160
  2020-11-22 Sun    1 cumulative  161
  2020-11-23 Mon   23 cumulative  184
  2020-11-24 Tue   23 cumulative  207
  2020-11-25 Wed   23 cumulative  230
  2020-11-26 Thu   18 cumulative  248
  2020-11-27 Fri   18 cumulative  266
  2020-11-28 Sat    0 cumulative  266
  2020-11-29 Sun    1 cumulative  267
  2020-11-30 Mon   23 cumulative  290
  2020-12-01 Tue   23 cumulative  313
  2020-12-02 Wed   23 cumulative  336
  2020-12-03 Thu   23 cumulative  359
  2020-12-04 Fri   22 cumulative  381
  2020-12-05 Sat    0 cumulative  381
  2020-12-06 Sun    1 cumulative  382
  2020-12-07 Mon   23 cumulative  405
  2020-12-08 Tue   23 cumulative  428
  2020-12-09 Wed   23 cumulative  451
  2020-12-10 Thu   23 cumulative  474
  2020-12-11 Fri   22 cumulative  496
  2020-12-12 Sat    0 cumulative  496
  2020-12-13 Sun    1 cumulative  497
  2020-12-14 Mon   23 cumulative  520  <- reaches 504
M15: completed bars per stored UTC day (Parquet row counts only)
  2020-11-12 Thu   92 cumulative   92
  2020-11-13 Fri   88 cumulative  180
  2020-11-14 Sat    0 cumulative  180
  2020-11-15 Sun    4 cumulative  184
  2020-11-16 Mon   92 cumulative  276
  2020-11-17 Tue   92 cumulative  368
  2020-11-18 Wed   92 cumulative  460
  2020-11-19 Thu   92 cumulative  552  <- reaches 509
  2020-11-20 Fri   88 cumulative  640
  2020-11-21 Sat    0 cumulative  640
  2020-11-22 Sun    4 cumulative  644
  2020-11-23 Mon   92 cumulative  736
  2020-11-24 Tue   92 cumulative  828
  2020-11-25 Wed   92 cumulative  920
  2020-11-26 Thu   72 cumulative  992
  2020-11-27 Fri   72 cumulative 1064
  2020-11-28 Sat    0 cumulative 1064
  2020-11-29 Sun    4 cumulative 1068
  2020-11-30 Mon   92 cumulative 1160
  2020-12-01 Tue   92 cumulative 1252
  2020-12-02 Wed   92 cumulative 1344
  2020-12-03 Thu   92 cumulative 1436
  2020-12-04 Fri   88 cumulative 1524
  2020-12-05 Sat    0 cumulative 1524
  2020-12-06 Sun    4 cumulative 1528
  2020-12-07 Mon   92 cumulative 1620
  2020-12-08 Tue   92 cumulative 1712
  2020-12-09 Wed   92 cumulative 1804
  2020-12-10 Thu   92 cumulative 1896
  2020-12-11 Fri   88 cumulative 1984
  2020-12-12 Sat    0 cumulative 1984
  2020-12-13 Sun    4 cumulative 1988
  2020-12-14 Mon   92 cumulative 2080
```

- **H1:** 497 bars through Sun 2020-12-13 and 520 through Mon 2020-12-14, so the first date
  with 504 bars behind it is **Tue 2020-12-15**.
- **M15:** the 509 needed are reached on 2020-11-19.
- **M5:** 3 bars, reached on the first day.

## 3. Plans checked with the loader (`plan_market`: file listing and row counts, no reads)

```
plan --from 2020-12-14: refused: warm-up needs 504 completed H1 bars before 2020-12-14; only 497 in the store
plan --from 2020-12-15 (as the pipeline resolves it): ok; first decision 2020-12-15T00:00:00+00:00; load range 2020-11-12..2025-09-30; 7137 files; exact tick days 0
```

- The evaluated period 2020-12-15 … 2025-09-30 is served entirely from the development store.
  Its warm-up comes from 2020-11-12 … 2020-12-14. Every day is bar-only (exact ticks start
  2026-06-26, inside the sealed period).
- One run of this check passed the sealed ranges without the end-day clamp
  `resolve_access` adds (`last_data_day = last` when `last + 1` is sealed). That check was
  refused **before reading** 2025-10-01. This is the loader working as designed; the
  pipeline always passes the clamp.

## 4. Pinned symbol snapshot

- `configs/backtest.yaml` `symbol_snapshot`: `20261010T000046Z.json`, sha256
  `0426cf8caefd2faa93b5b3b24004db656e2768e139122d2cd34e394b0be0c7ee`.
- Server `FusionMarkets-Demo`, USD account. This is the contract cited in the spec, section 8a.
- Resolved by `pinned_spec` on the real store; the hash matches.
