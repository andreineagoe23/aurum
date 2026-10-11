# opening_range_breakout 0.1.0: first-stage assessment of the 12 development trials (2026-10-11)

Generated read-only by `scripts/opening_range_breakout_dev_analysis.py` from the existing
12 runs (ledger opened read-only; no simulation). Rules: frozen spec section 16. Inventory:
`opening_range_breakout_2026-10-11_dev_batch.md`. Launch and identity evidence: `opening_range_breakout_2026-10-11_dev_launch.md`.

**Status: provisional, for review.** This applies the registered first-stage gates (1–4,
6–9, 12, 13). Gates 5, 10 and 11 are **not evaluated** (no stress re-run, no frozen
configuration, no best-case replay). No final candidate is selected and no full-period
figure is used to choose anything.

Definitions (spec section 16, as trend_pullback):

- Trade P&L = booked net P&L (all costs). Slices by **entry decision time** (`signal_time`,
  UTC); session = the calendar session of that time (`london_morning` = the `london` range,
  `ldn_ny_overlap` = the `newyork` range). DST = the UK season of the London date and
  whether 09:30 New York is 14:30 London (normal) or 13:30 London (mismatch week).
- Net expectancy = Σ net P&L ÷ closed trades. PF = Σ wins ÷ |Σ losses|. Max drawdown =
  closed-trade, by exit time, cumulative from 0, USD.
- R per trade = P&L ÷ the fill-to-stop risk at entry, |entry price − stop| × volume × 100
  USD per price unit per lot (pinned snapshot). The spec's R is from `ref`, which the trades
  table does not store; the fill is slightly worse than `ref` (section 9).

## 1. Defaults (or_bars 2, target_r 1.5), full period, descriptive only

2020-12-15 … 2025-09-30, one fixed point over the whole period: **not** walk-forward
evidence.

#### m15 default (2, 1.5)

| measure | value |
|---|---|
| closed trades (long / short) | 1382 (730 / 652) |
| net P&L USD | -140.74 |
| gross P&L USD (mid to mid) | 512.57 |
| net expectancy USD/trade | -0.10 |
| gross expectancy USD/trade | 0.37 |
| net / gross expectancy R/trade | -0.002 / 0.021 |
| net profit factor | 0.981 |
| max drawdown, closed-trade, USD | 467.71 |
| win rate (not a criterion) | 0.473 |
| holding minutes, mean / median | 108.7 / 120.0 |
| cost attribution USD: spread / slippage / commission | 170.39 / 331.70 / 151.22 |
| exit reasons | STOP 182, TARGET 95, TIMEOUT 1105 |
| worst-case (intra-bar) flagged trades | 0 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| training (2020-12-15..2021-09-30) | 216 | -133.06 | -0.62 | -0.027 | 0.88 |
| fold 1 (2021-10-01..2022-09-30) | 268 | -151.57 | -0.57 | -0.029 | 0.90 |
| fold 2 (2022-10-01..2023-09-30) | 256 | -3.21 | -0.01 | 0.012 | 1.00 |
| fold 3 (2023-10-01..2024-09-30) | 268 | -35.29 | -0.13 | -0.011 | 0.98 |
| fold 4 (2024-10-01..2025-09-30) | 374 | 182.39 | 0.49 | 0.029 | 1.09 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| year 2020 | 16 | 32.68 | 2.04 | 0.078 | 1.36 |
| year 2021 | 255 | -281.61 | -1.10 | -0.049 | 0.80 |
| year 2022 | 286 | -53.44 | -0.19 | -0.008 | 0.96 |
| year 2023 | 229 | 36.48 | 0.16 | 0.015 | 1.03 |
| year 2024 | 325 | 142.96 | 0.44 | 0.018 | 1.08 |
| year 2025 | 271 | -17.81 | -0.07 | 0.006 | 0.99 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| BUY | 730 | 212.50 | 0.29 | 0.017 | 1.06 |
| SELL | 652 | -353.24 | -0.54 | -0.023 | 0.90 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| session ldn_ny_overlap | 831 | 176.43 | 0.21 | 0.017 | 1.04 |
| session london_morning | 551 | -317.17 | -0.58 | -0.030 | 0.91 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| UK summer, normal | 835 | -338.18 | -0.41 | -0.016 | 0.93 |
| UK winter, mismatch | 108 | -138.06 | -1.28 | -0.058 | 0.80 |
| UK winter, normal | 439 | 335.50 | 0.76 | 0.039 | 1.16 |

#### m5 default (2, 1.5)

| measure | value |
|---|---|
| closed trades (long / short) | 1378 (729 / 649) |
| net P&L USD | -113.71 |
| gross P&L USD (mid to mid) | 550.97 |
| net expectancy USD/trade | -0.08 |
| gross expectancy USD/trade | 0.40 |
| net / gross expectancy R/trade | -0.002 / 0.022 |
| net profit factor | 0.986 |
| max drawdown, closed-trade, USD | 487.23 |
| win rate (not a criterion) | 0.489 |
| holding minutes, mean / median | 106.3 / 120.0 |
| cost attribution USD: spread / slippage / commission | 174.12 / 336.05 / 154.50 |
| exit reasons | STOP 217, TARGET 105, TIMEOUT 1056 |
| worst-case (intra-bar) flagged trades | 1 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| training (2020-12-15..2021-09-30) | 202 | -234.62 | -1.16 | -0.048 | 0.79 |
| fold 1 (2021-10-01..2022-09-30) | 273 | -60.19 | -0.22 | -0.009 | 0.96 |
| fold 2 (2022-10-01..2023-09-30) | 257 | 243.78 | 0.95 | 0.054 | 1.20 |
| fold 3 (2023-10-01..2024-09-30) | 264 | -106.28 | -0.40 | -0.026 | 0.94 |
| fold 4 (2024-10-01..2025-09-30) | 382 | 43.60 | 0.11 | 0.008 | 1.02 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| year 2020 | 16 | -7.97 | -0.50 | 0.006 | 0.93 |
| year 2021 | 244 | -355.04 | -1.46 | -0.065 | 0.75 |
| year 2022 | 287 | 196.19 | 0.68 | 0.034 | 1.13 |
| year 2023 | 231 | 37.62 | 0.16 | 0.016 | 1.03 |
| year 2024 | 315 | 134.81 | 0.43 | 0.018 | 1.07 |
| year 2025 | 285 | -119.32 | -0.42 | -0.018 | 0.94 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| BUY | 729 | 134.96 | 0.19 | 0.009 | 1.03 |
| SELL | 649 | -248.67 | -0.38 | -0.014 | 0.94 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| session ldn_ny_overlap | 893 | 127.42 | 0.14 | 0.011 | 1.03 |
| session london_morning | 485 | -241.13 | -0.50 | -0.024 | 0.93 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| UK summer, normal | 847 | -206.47 | -0.24 | -0.010 | 0.96 |
| UK winter, mismatch | 101 | -96.21 | -0.95 | -0.046 | 0.86 |
| UK winter, normal | 430 | 188.97 | 0.44 | 0.025 | 1.08 |

## 2. All 12 points, full period (descriptive only)

Grid order, not ranked; not a selection basis. `signal` / `stop<min` / `stop>cap` are the
strategy's range-date statuses (before the risk engine); `risk rej` = risk-engine
rejections in the run journal; `worst` = trades decided by a worst-case intra-bar
assumption. Machine-readable: `opening_range_breakout_2026-10-11_dev_points.csv`.

| variant | point (or_bars, target_r) | trades | net USD | net USD/trade | net R/trade | gross USD/trade | PF | max DD USD | win rate | signal | stop<min | stop>cap | risk rej | worst |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| m15 | (2, 1.0) | 1382 | -230.22 | -0.17 | -0.004 | 0.29 | 0.969 | 449.13 | 0.480 | 1383 | 637 | 48 | 1 | 3 |
| m15 | (2, 1.5) **default** | 1382 | -140.74 | -0.10 | -0.002 | 0.37 | 0.981 | 467.71 | 0.473 | 1383 | 637 | 48 | 1 | 0 |
| m15 | (2, 2.0) | 1382 | -258.18 | -0.19 | -0.005 | 0.29 | 0.966 | 476.84 | 0.472 | 1383 | 637 | 48 | 1 | 1 |
| m15 | (4, 1.0) | 1085 | -849.27 | -0.78 | -0.040 | -0.37 | 0.826 | 977.34 | 0.460 | 1086 | 309 | 85 | 1 | 5 |
| m15 | (4, 1.5) | 1085 | -706.76 | -0.65 | -0.033 | -0.22 | 0.856 | 859.65 | 0.459 | 1086 | 309 | 85 | 1 | 3 |
| m15 | (4, 2.0) | 1085 | -750.15 | -0.69 | -0.035 | -0.26 | 0.848 | 925.94 | 0.458 | 1086 | 309 | 85 | 1 | 2 |
| m5 | (2, 1.0) | 1378 | -180.25 | -0.13 | -0.004 | 0.34 | 0.977 | 411.49 | 0.496 | 1379 | 760 | 50 | 1 | 2 |
| m5 | (2, 1.5) **default** | 1378 | -113.71 | -0.08 | -0.002 | 0.40 | 0.986 | 487.23 | 0.489 | 1379 | 760 | 50 | 1 | 1 |
| m5 | (2, 2.0) | 1378 | -68.89 | -0.05 | 0.000 | 0.44 | 0.991 | 472.10 | 0.488 | 1379 | 760 | 50 | 1 | 4 |
| m5 | (4, 1.0) | 1264 | -603.43 | -0.48 | -0.026 | -0.06 | 0.894 | 733.40 | 0.467 | 1265 | 404 | 77 | 1 | 1 |
| m5 | (4, 1.5) | 1264 | -519.95 | -0.41 | -0.020 | 0.02 | 0.910 | 629.79 | 0.464 | 1265 | 404 | 77 | 1 | 2 |
| m5 | (4, 2.0) | 1264 | -503.51 | -0.40 | -0.020 | 0.04 | 0.913 | 637.92 | 0.463 | 1265 | 404 | 77 | 1 | 1 |

Risk-engine rejections by reason (numbers masked), summed over each variant's 6 runs:

- m15: 6 × `spread # points > #`
- m5: 6 × `spread # points > #`

## 3. Registered anchored walk-forward (OOS evidence)

Fold k's point has the highest net expectancy over **all development trades entered before
fold k**, among points with ≥ 100 such trades (`walkforward.walk_forward`).
Ties are exact (integer cents): the default wins, then the lexicographically smallest
(or_bars, target_r). No eligible point → the fold has no trades (no fallback). The fold's
OOS trades are the selected point's trades entered in it, from its own run (no rescaling).

### m15

| fold | selected point | eligible points (≥100 prior) | prior trades | prior net USD/trade | exact ties | OOS trades | OOS net USD | OOS net USD/trade |
|---|---|---|---|---|---|---|---|---|
| fold 1 | (2, 1.0) | 6 | 216 | -0.08 | none | 268 | -145.06 | -0.54 |
| fold 2 | (2, 1.0) | 6 | 484 | -0.34 | none | 256 | 17.11 | 0.07 |
| fold 3 | (2, 1.0) | 6 | 740 | -0.20 | none | 268 | -166.81 | -0.62 |
| fold 4 | (2, 2.0) | 6 | 1008 | -0.28 | none | 374 | 23.71 | 0.06 |

#### m15 concatenated OOS (folds 1–4)

| measure | value |
|---|---|
| closed trades (long / short) | 1166 (624 / 542) |
| net P&L USD | -271.05 |
| gross P&L USD (mid to mid) | 258.78 |
| net expectancy USD/trade | -0.23 |
| gross expectancy USD/trade | 0.22 |
| net / gross expectancy R/trade | -0.006 / 0.016 |
| net profit factor | 0.958 |
| max drawdown, closed-trade, USD | 417.47 |
| win rate (not a criterion) | 0.480 |
| holding minutes, mean / median | 104.3 / 120.0 |
| cost attribution USD: spread / slippage / commission | 137.62 / 267.05 / 125.16 |
| exit reasons | STOP 165, TARGET 139, TIMEOUT 862 |
| worst-case (intra-bar) flagged trades | 2 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| fold 1 (2021-10-01..2022-09-30) | 268 | -145.06 | -0.54 | -0.026 | 0.90 |
| fold 2 (2022-10-01..2023-09-30) | 256 | 17.11 | 0.07 | 0.014 | 1.01 |
| fold 3 (2023-10-01..2024-09-30) | 268 | -166.81 | -0.62 | -0.030 | 0.90 |
| fold 4 (2024-10-01..2025-09-30) | 374 | 23.71 | 0.06 | 0.011 | 1.01 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| year 2021 | 55 | -87.06 | -1.58 | -0.080 | 0.76 |
| year 2022 | 286 | -72.38 | -0.25 | -0.009 | 0.95 |
| year 2023 | 229 | 22.71 | 0.10 | 0.011 | 1.02 |
| year 2024 | 325 | 1.52 | 0.00 | 0.000 | 1.00 |
| year 2025 | 271 | -135.84 | -0.50 | -0.010 | 0.92 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| BUY | 624 | 46.93 | 0.08 | 0.007 | 1.01 |
| SELL | 542 | -317.98 | -0.59 | -0.021 | 0.90 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| session ldn_ny_overlap | 693 | 290.69 | 0.42 | 0.028 | 1.08 |
| session london_morning | 473 | -561.74 | -1.19 | -0.056 | 0.81 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| UK summer, normal | 704 | -392.58 | -0.56 | -0.021 | 0.90 |
| UK winter, mismatch | 95 | -92.17 | -0.97 | -0.045 | 0.85 |
| UK winter, normal | 367 | 213.70 | 0.58 | 0.032 | 1.12 |

Monthly (entry month, UTC):

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| 2021-10 | 21 | -133.70 | -6.37 | -0.294 | 0.32 |
| 2021-11 | 22 | 12.25 | 0.56 | 0.006 | 1.10 |
| 2021-12 | 12 | 34.39 | 2.87 | 0.136 | 1.75 |
| 2022-01 | 21 | -88.38 | -4.21 | -0.196 | 0.40 |
| 2022-02 | 21 | 63.75 | 3.04 | 0.182 | 1.82 |
| 2022-03 | 32 | -45.24 | -1.41 | -0.081 | 0.79 |
| 2022-04 | 27 | -41.76 | -1.55 | -0.069 | 0.72 |
| 2022-05 | 30 | 47.49 | 1.58 | 0.074 | 1.38 |
| 2022-06 | 24 | -34.58 | -1.44 | -0.068 | 0.76 |
| 2022-07 | 18 | 39.79 | 2.21 | 0.114 | 1.56 |
| 2022-08 | 20 | 5.02 | 0.25 | 0.008 | 1.05 |
| 2022-09 | 20 | -4.09 | -0.20 | -0.023 | 0.94 |
| 2022-10 | 31 | -26.04 | -0.84 | -0.024 | 0.84 |
| 2022-11 | 23 | 2.88 | 0.13 | 0.014 | 1.03 |
| 2022-12 | 19 | 8.78 | 0.46 | 0.013 | 1.09 |
| 2023-01 | 26 | 9.85 | 0.38 | 0.021 | 1.08 |
| 2023-02 | 22 | 34.69 | 1.58 | 0.096 | 1.43 |
| 2023-03 | 28 | -60.14 | -2.15 | -0.081 | 0.66 |
| 2023-04 | 21 | 68.70 | 3.27 | 0.146 | 2.23 |
| 2023-05 | 22 | 40.11 | 1.82 | 0.103 | 1.32 |
| 2023-06 | 17 | 28.96 | 1.70 | 0.120 | 1.77 |
| 2023-07 | 18 | -26.18 | -1.45 | -0.077 | 0.70 |
| 2023-08 | 16 | -28.45 | -1.78 | -0.086 | 0.63 |
| 2023-09 | 13 | -36.05 | -2.77 | -0.098 | 0.50 |
| 2023-10 | 18 | -28.57 | -1.59 | -0.094 | 0.76 |
| 2023-11 | 10 | 43.18 | 4.32 | 0.189 | 2.15 |
| 2023-12 | 18 | -23.39 | -1.30 | -0.072 | 0.79 |
| 2024-01 | 19 | 2.59 | 0.14 | 0.020 | 1.03 |
| 2024-02 | 13 | -42.96 | -3.30 | -0.158 | 0.59 |
| 2024-03 | 23 | 28.85 | 1.25 | 0.075 | 1.21 |
| 2024-04 | 35 | -47.14 | -1.35 | -0.089 | 0.76 |
| 2024-05 | 28 | -26.66 | -0.95 | -0.072 | 0.85 |
| 2024-06 | 20 | -50.31 | -2.52 | -0.126 | 0.68 |
| 2024-07 | 29 | 19.47 | 0.67 | 0.042 | 1.14 |
| 2024-08 | 26 | -23.86 | -0.92 | -0.005 | 0.82 |
| 2024-09 | 29 | -18.01 | -0.62 | -0.013 | 0.92 |
| 2024-10 | 38 | 54.51 | 1.43 | 0.053 | 1.28 |
| 2024-11 | 38 | 147.93 | 3.89 | 0.184 | 2.21 |
| 2024-12 | 27 | -42.89 | -1.59 | -0.075 | 0.74 |
| 2025-01 | 31 | 56.52 | 1.82 | 0.091 | 1.44 |
| 2025-02 | 26 | -46.00 | -1.77 | -0.080 | 0.75 |
| 2025-03 | 34 | -5.72 | -0.17 | -0.009 | 0.97 |
| 2025-04 | 19 | -53.71 | -2.83 | -0.125 | 0.69 |
| 2025-05 | 31 | -10.31 | -0.33 | 0.023 | 0.95 |
| 2025-06 | 30 | -7.01 | -0.23 | -0.007 | 0.96 |
| 2025-07 | 37 | 22.99 | 0.62 | 0.039 | 1.11 |
| 2025-08 | 30 | -147.15 | -4.91 | -0.228 | 0.39 |
| 2025-09 | 33 | 54.55 | 1.65 | 0.126 | 1.31 |

#### m15: registered thresholds on the concatenated OOS trades

| gate | result |
|---|---|
| 1 sample (≥30 per fold, ≥200) | pass: [268, 256, 268, 374], total 1166 |
| 2 positive after costs | FAIL: -0.23 USD/trade, -271.05 USD |
| 3 ≥3 of 4 folds positive | FAIL: 2 of 4 |
| 4 PF ≥ 1.10 | FAIL: 0.958 |
| 5 spread ×1.5 re-run | not evaluated (stress re-runs not authorised) |
| 6 max DD ≤ 1,000 USD | pass: 417.47 |
| 7 no single month | FAIL: without 2024-11 (147.93) = -418.98 |
| 8 not one direction | FAIL: BUY 624 @ 0.08, SELL 542 @ -0.59 |
| 9 not one session | FAIL: london_morning (london) 473 @ -1.19, ldn_ny_overlap (newyork) 693 @ 0.42 |
| 10 neighbourhood | not evaluated (frozen configuration not selected) |
| 11 intra-bar (Phase 3b) | not evaluated (best-case replay not authorised); worst-case flagged 2 of 1166 = 0.17% |
| 12 DSR ≥ 0.95 | FAIL: 0.0061 |
| 13 sealed untouched | pass: 0 opening_range_breakout runs read 2025-10-01 or later; sealed openings 0, parity accesses 0 by opening_range_breakout; 10 historical reference_fixed_time exceptions listed apart (section 6) |

DSR inputs: SR -0.01853 per day, T 1032 market days, skew 0.2695, kurtosis 3.9196, V 6.293e-04 (sample variance, ddof 1, of 66 defined trial SRs), N 66, SR0 0.05973; undefined trial SRs 0.

### m5

| fold | selected point | eligible points (≥100 prior) | prior trades | prior net USD/trade | exact ties | OOS trades | OOS net USD | OOS net USD/trade |
|---|---|---|---|---|---|---|---|---|
| fold 1 | (2, 1.0) | 6 | 202 | -0.66 | none | 273 | -50.63 | -0.19 |
| fold 2 | (2, 1.0) | 6 | 475 | -0.39 | none | 257 | 180.50 | 0.70 |
| fold 3 | (2, 2.0) | 6 | 732 | 0.00 | none | 264 | -61.16 | -0.23 |
| fold 4 | (2, 2.0) | 6 | 996 | -0.06 | none | 382 | -10.47 | -0.03 |

#### m5 concatenated OOS (folds 1–4)

| measure | value |
|---|---|
| closed trades (long / short) | 1176 (629 / 547) |
| net P&L USD | 58.24 |
| gross P&L USD (mid to mid) | 615.41 |
| net expectancy USD/trade | 0.05 |
| gross expectancy USD/trade | 0.52 |
| net / gross expectancy R/trade | 0.005 / 0.028 |
| net profit factor | 1.008 |
| max drawdown, closed-trade, USD | 447.01 |
| win rate (not a criterion) | 0.497 |
| holding minutes, mean / median | 104.3 / 120.0 |
| cost attribution USD: spread / slippage / commission | 145.41 / 280.90 / 130.86 |
| exit reasons | STOP 195, TARGET 118, TIMEOUT 863 |
| worst-case (intra-bar) flagged trades | 3 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| fold 1 (2021-10-01..2022-09-30) | 273 | -50.63 | -0.19 | -0.007 | 0.97 |
| fold 2 (2022-10-01..2023-09-30) | 257 | 180.50 | 0.70 | 0.044 | 1.15 |
| fold 3 (2023-10-01..2024-09-30) | 264 | -61.16 | -0.23 | -0.017 | 0.97 |
| fold 4 (2024-10-01..2025-09-30) | 382 | -10.47 | -0.03 | 0.001 | 1.00 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| year 2021 | 58 | -129.45 | -2.23 | -0.110 | 0.67 |
| year 2022 | 287 | 157.81 | 0.55 | 0.029 | 1.11 |
| year 2023 | 231 | 44.99 | 0.19 | 0.019 | 1.04 |
| year 2024 | 315 | 134.16 | 0.43 | 0.016 | 1.07 |
| year 2025 | 285 | -149.27 | -0.52 | -0.021 | 0.92 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| BUY | 629 | 83.02 | 0.13 | 0.007 | 1.02 |
| SELL | 547 | -24.78 | -0.05 | 0.001 | 0.99 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| session ldn_ny_overlap | 745 | 392.77 | 0.53 | 0.028 | 1.10 |
| session london_morning | 431 | -334.53 | -0.78 | -0.037 | 0.89 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| UK summer, normal | 725 | -215.81 | -0.30 | -0.014 | 0.95 |
| UK winter, mismatch | 91 | -28.16 | -0.31 | -0.015 | 0.96 |
| UK winter, normal | 360 | 302.21 | 0.84 | 0.046 | 1.16 |

Monthly (entry month, UTC):

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| 2021-10 | 22 | -149.90 | -6.81 | -0.301 | 0.28 |
| 2021-11 | 23 | 10.78 | 0.47 | -0.003 | 1.09 |
| 2021-12 | 13 | 9.67 | 0.74 | 0.022 | 1.15 |
| 2022-01 | 18 | -76.32 | -4.24 | -0.188 | 0.47 |
| 2022-02 | 19 | 95.87 | 5.05 | 0.261 | 2.44 |
| 2022-03 | 35 | -38.79 | -1.11 | -0.061 | 0.84 |
| 2022-04 | 25 | 16.13 | 0.65 | 0.014 | 1.16 |
| 2022-05 | 34 | -0.90 | -0.03 | 0.017 | 1.00 |
| 2022-06 | 22 | 14.04 | 0.64 | 0.015 | 1.10 |
| 2022-07 | 20 | 21.73 | 1.09 | 0.083 | 1.21 |
| 2022-08 | 18 | 4.70 | 0.26 | 0.003 | 1.06 |
| 2022-09 | 24 | 42.36 | 1.76 | 0.086 | 1.49 |
| 2022-10 | 30 | 42.97 | 1.43 | 0.083 | 1.31 |
| 2022-11 | 24 | -6.01 | -0.25 | -0.007 | 0.94 |
| 2022-12 | 18 | 42.03 | 2.33 | 0.096 | 1.69 |
| 2023-01 | 23 | -39.17 | -1.70 | -0.068 | 0.72 |
| 2023-02 | 21 | 94.60 | 4.50 | 0.236 | 2.40 |
| 2023-03 | 27 | -78.35 | -2.90 | -0.146 | 0.65 |
| 2023-04 | 22 | 89.37 | 4.06 | 0.183 | 2.57 |
| 2023-05 | 23 | 60.56 | 2.63 | 0.160 | 1.52 |
| 2023-06 | 19 | 71.51 | 3.76 | 0.206 | 3.29 |
| 2023-07 | 19 | -43.40 | -2.28 | -0.108 | 0.58 |
| 2023-08 | 18 | -39.37 | -2.19 | -0.089 | 0.62 |
| 2023-09 | 13 | -14.24 | -1.10 | -0.013 | 0.81 |
| 2023-10 | 16 | -48.79 | -3.05 | -0.178 | 0.58 |
| 2023-11 | 14 | 9.69 | 0.69 | 0.034 | 1.13 |
| 2023-12 | 16 | -17.42 | -1.09 | -0.030 | 0.87 |
| 2024-01 | 18 | 29.82 | 1.66 | 0.083 | 1.37 |
| 2024-02 | 11 | 13.71 | 1.25 | 0.027 | 1.16 |
| 2024-03 | 20 | 167.00 | 8.35 | 0.426 | 2.65 |
| 2024-04 | 34 | 3.99 | 0.12 | -0.025 | 1.02 |
| 2024-05 | 28 | -89.87 | -3.21 | -0.169 | 0.60 |
| 2024-06 | 18 | -137.94 | -7.66 | -0.356 | 0.24 |
| 2024-07 | 29 | 49.71 | 1.71 | 0.063 | 1.31 |
| 2024-08 | 29 | 25.64 | 0.88 | 0.024 | 1.17 |
| 2024-09 | 31 | -66.70 | -2.15 | -0.078 | 0.73 |
| 2024-10 | 35 | 63.44 | 1.81 | 0.096 | 1.33 |
| 2024-11 | 36 | 144.24 | 4.01 | 0.182 | 2.32 |
| 2024-12 | 26 | -68.88 | -2.65 | -0.129 | 0.60 |
| 2025-01 | 31 | 106.07 | 3.42 | 0.166 | 2.03 |
| 2025-02 | 30 | -146.69 | -4.89 | -0.200 | 0.50 |
| 2025-03 | 31 | -10.11 | -0.33 | -0.005 | 0.95 |
| 2025-04 | 18 | 21.76 | 1.21 | 0.064 | 1.15 |
| 2025-05 | 35 | 25.78 | 0.74 | 0.038 | 1.12 |
| 2025-06 | 32 | -26.80 | -0.84 | -0.016 | 0.88 |
| 2025-07 | 39 | -21.29 | -0.55 | -0.042 | 0.92 |
| 2025-08 | 34 | -177.92 | -5.23 | -0.284 | 0.40 |
| 2025-09 | 35 | 79.93 | 2.28 | 0.121 | 1.42 |

#### m5: registered thresholds on the concatenated OOS trades

| gate | result |
|---|---|
| 1 sample (≥30 per fold, ≥200) | pass: [273, 257, 264, 382], total 1176 |
| 2 positive after costs | pass: 0.05 USD/trade, 58.24 USD |
| 3 ≥3 of 4 folds positive | FAIL: 1 of 4 |
| 4 PF ≥ 1.10 | FAIL: 1.008 |
| 5 spread ×1.5 re-run | not evaluated (stress re-runs not authorised) |
| 6 max DD ≤ 1,000 USD | pass: 447.01 |
| 7 no single month | FAIL: without 2024-03 (167.00) = -108.76 |
| 8 not one direction | FAIL: BUY 629 @ 0.13, SELL 547 @ -0.05 |
| 9 not one session | FAIL: london_morning (london) 431 @ -0.78, ldn_ny_overlap (newyork) 745 @ 0.53 |
| 10 neighbourhood | not evaluated (frozen configuration not selected) |
| 11 intra-bar (Phase 3b) | not evaluated (best-case replay not authorised); worst-case flagged 3 of 1176 = 0.26% |
| 12 DSR ≥ 0.95 | FAIL: 0.0354 |
| 13 sealed untouched | pass: 0 opening_range_breakout runs read 2025-10-01 or later; sealed openings 0, parity accesses 0 by opening_range_breakout; 10 historical reference_fixed_time exceptions listed apart (section 6) |

DSR inputs: SR 0.00352 per day, T 1032 market days, skew 0.4722, kurtosis 4.1138, V 6.293e-04 (sample variance, ddof 1, of 66 defined trial SRs), N 66, SR0 0.05973; undefined trial SRs 0.

## 4. Summary

| gate | m15 | m5 |
|---|---|---|
| 1 sample (≥30 per fold, ≥200) | pass: [268, 256, 268, 374], total 1166 | pass: [273, 257, 264, 382], total 1176 |
| 2 positive after costs | FAIL: -0.23 USD/trade, -271.05 USD | pass: 0.05 USD/trade, 58.24 USD |
| 3 ≥3 of 4 folds positive | FAIL: 2 of 4 | FAIL: 1 of 4 |
| 4 PF ≥ 1.10 | FAIL: 0.958 | FAIL: 1.008 |
| 5 spread ×1.5 re-run | not evaluated (stress re-runs not authorised) | not evaluated (stress re-runs not authorised) |
| 6 max DD ≤ 1,000 USD | pass: 417.47 | pass: 447.01 |
| 7 no single month | FAIL: without 2024-11 (147.93) = -418.98 | FAIL: without 2024-03 (167.00) = -108.76 |
| 8 not one direction | FAIL: BUY 624 @ 0.08, SELL 542 @ -0.59 | FAIL: BUY 629 @ 0.13, SELL 547 @ -0.05 |
| 9 not one session | FAIL: london_morning (london) 473 @ -1.19, ldn_ny_overlap (newyork) 693 @ 0.42 | FAIL: london_morning (london) 431 @ -0.78, ldn_ny_overlap (newyork) 745 @ 0.53 |
| 10 neighbourhood | not evaluated (frozen configuration not selected) | not evaluated (frozen configuration not selected) |
| 11 intra-bar (Phase 3b) | not evaluated (best-case replay not authorised); worst-case flagged 2 of 1166 = 0.17% | not evaluated (best-case replay not authorised); worst-case flagged 3 of 1176 = 0.26% |
| 12 DSR ≥ 0.95 | FAIL: 0.0061 | FAIL: 0.0354 |
| 13 sealed untouched | pass: 0 opening_range_breakout runs read 2025-10-01 or later; sealed openings 0, parity accesses 0 by opening_range_breakout; 10 historical reference_fixed_time exceptions listed apart (section 6) | pass: 0 opening_range_breakout runs read 2025-10-01 or later; sealed openings 0, parity accesses 0 by opening_range_breakout; 10 historical reference_fixed_time exceptions listed apart (section 6) |

- **m15:** fails registered gate(s) 2, 3, 4, 7, 8, 9, 12; under section 16 this variant is rejected and evaluation stops (gates 5, 10, 11 are not run).
- **m5:** fails registered gate(s) 3, 4, 7, 8, 9, 12; under section 16 this variant is rejected and evaluation stops (gates 5, 10, 11 are not run).

- Not evaluated: gate 5 (spread ×1.5 re-run), gate 10 (neighbourhood of the frozen
  configuration, which is not selected) and gate 11 (best-case intra-bar replay).
- The null baseline (`reference_fixed_time`) is cited, not compared: a like-for-like rerun
  over 2020-12-15 … 2025-09-30 is not authorised, and its trades were not read.
- Trial count: 12 for this family, 66 cumulative (N for the DSR).
- Trades exiting on a date outside the market calendar in folds 1–4 (the 66 DSR trial series): 0.

## 5. DSR trial set (gate 12 inputs)

- N = 66: the 54 trend_pullback 0.2.0 + A1 trials (batches `trend_pullback/m15/2020-12-15..2025-09-30`, `trend_pullback/m5/2020-12-15..2025-09-30`)
  and the 12 opening_range_breakout trials. `reference_fixed_time` trials are excluded (not research trials).
- Each trial's daily SR over the same 1032 market days of folds 1–4 (`2021-10-01` … `2025-09-30`), from its own run.
- Defined SRs (the sample V is computed over): 66. Undefined (kept in N, left out of V): 0.
- Per-trial values: `opening_range_breakout_2026-10-11_dev_dsr_trials.csv`.

## 6. Sealed period (gate 13)

- opening_range_breakout trials: 12, latest `data_end` 2025-09-30T23:00:00+00:00 (London midnight of
  2025-10-01). opening_range_breakout trials whose `data_end` is after 2025-10-01 00:00 UTC: 0.
- Sealed openings: 0 (0 by opening_range_breakout). Parity accesses: 0 (0 by opening_range_breakout).
- Historical exceptions to the literal ledger-wide condition (listed apart, not a candidate failure; no row deleted or rewritten): 10 `reference_fixed_time` trials recorded 2026-10-09/10, before the seal existed:

| trial | strategy | data_start | data_end |
|---|---|---|---|
| `tr_20261009T221629964444_3c472c4b` | reference_fixed_time | 2026-09-13 | 2026-10-08 |
| `tr_20261009T221818318925_c0ca0f25` | reference_fixed_time | 2020-11-12 | 2026-10-08 |
| `tr_20261009T221917546708_32c86b28` | reference_fixed_time | 2020-11-12 | 2026-10-08 |
| `tr_20261009T222312946438_ded3b3af` | reference_fixed_time | 2026-09-13 | 2026-10-08 |
| `tr_20261009T231143063137_524c5051` | reference_fixed_time | 2026-09-13 | 2026-10-08 |
| `tr_20261009T231320639210_7398d837` | reference_fixed_time | 2020-11-12 | 2026-10-08 |
| `tr_20261009T232942016281_599861b8` | reference_fixed_time | 2026-09-13 | 2026-10-08 |
| `tr_20261009T233213662768_daa59b30` | reference_fixed_time | 2020-11-12 | 2026-10-08 |
| `tr_20261009T233343400292_605b5945` | reference_fixed_time | 2020-11-12 | 2026-10-08 |
| `tr_20261010T002444363991_d57c1e14` | reference_fixed_time | 2020-11-12 | 2026-10-08 |
