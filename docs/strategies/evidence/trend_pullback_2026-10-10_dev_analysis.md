# trend_pullback 0.2.0 + A1: development analysis of the 54 trials (2026-10-10)

Generated read-only by `scripts/trend_pullback_dev_analysis.py` from the existing 54 runs
(no simulation). Rules: spec section 16 as amended by A1. Evidence package:
`trend_pullback_2026-10-10_dev_batch.md`.

**Status of this document.** The pipeline computed simulated P&L and summary metrics for
every trial when it ran. This is the **post-batch assessment** against the registered
rules. It does **not** select the final candidate (the frozen configuration for Phase 6
is not determined here), and it does not use full-period P&L to choose anything.

Definitions used (spec section 16):

- Trade P&L = booked net P&L (all costs). Slices by **entry decision time** (`signal_time`,
  UTC); session = the session of that decision time.
- Net expectancy = Σ net P&L ÷ closed trades. PF = Σ wins ÷ |Σ losses|.
- Max drawdown = closed-trade, trades ordered by exit time, cumulative from 0, USD.
- R per trade = net (or gross) P&L ÷ the fill-to-stop risk at entry,
  |entry price − stop| × volume × 100 USD per price unit per lot (pinned snapshot).
  The spec's R is from `ref`, which the trades table does not store; the realised fill is
  slightly worse than `ref` (section 9), so this R is the money actually at risk.

## 1. Defaults (h1_ema 50, stop_atr_mult 0.5, target_r 1.5)

Full evaluated period 2020-12-15 … 2025-09-30. **Descriptive only**: a fixed point over the
whole period, not walk-forward evidence.

#### m15 default (50, 0.5, 1.5)

| measure | value |
|---|---|
| closed trades (long / short) | 775 (452 / 323) |
| net P&L USD | 213.88 |
| gross P&L USD (mid to mid) | 614.66 |
| net expectancy USD/trade | 0.28 |
| gross expectancy USD/trade | 0.79 |
| net / gross expectancy R/trade | 0.018 / 0.042 |
| net profit factor | 1.036 |
| max drawdown, closed-trade, USD | 437.66 |
| win rate (not a criterion) | 0.475 |
| holding minutes, mean / median | 137.0 / 180.0 |
| cost attribution USD: spread / slippage / commission | 108.33 / 197.45 / 95.00 |
| worst-case (intra-bar) flagged trades | 5 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| training (2020-12-15..2021-09-30) | 114 | 20.02 | 0.18 | 0.002 | 1.02 |
| fold 1 (2021-10-01..2022-09-30) | 168 | 298.59 | 1.78 | 0.081 | 1.27 |
| fold 2 (2022-10-01..2023-09-30) | 121 | 3.79 | 0.03 | 0.018 | 1.00 |
| fold 3 (2023-10-01..2024-09-30) | 180 | -42.61 | -0.24 | -0.002 | 0.97 |
| fold 4 (2024-10-01..2025-09-30) | 192 | -65.91 | -0.34 | -0.010 | 0.96 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| year 2020 | 7 | -71.28 | -10.18 | -0.474 | 0.08 |
| year 2021 | 143 | -13.33 | -0.09 | -0.012 | 0.99 |
| year 2022 | 168 | 469.85 | 2.80 | 0.134 | 1.43 |
| year 2023 | 120 | -30.53 | -0.25 | 0.006 | 0.96 |
| year 2024 | 191 | -222.13 | -1.16 | -0.041 | 0.87 |
| year 2025 | 146 | 81.30 | 0.56 | 0.024 | 1.07 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| BUY | 452 | 92.29 | 0.20 | 0.012 | 1.03 |
| SELL | 323 | 121.59 | 0.38 | 0.027 | 1.05 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| session ldn_ny_overlap | 411 | 449.40 | 1.09 | 0.062 | 1.15 |
| session london_morning | 364 | -235.52 | -0.65 | -0.032 | 0.92 |

#### m5 default (50, 0.5, 1.5)

| measure | value |
|---|---|
| closed trades (long / short) | 874 (508 / 366) |
| net P&L USD | 898.90 |
| gross P&L USD (mid to mid) | 1,379.68 |
| net expectancy USD/trade | 1.03 |
| gross expectancy USD/trade | 1.58 |
| net / gross expectancy R/trade | 0.054 / 0.079 |
| net profit factor | 1.136 |
| max drawdown, closed-trade, USD | 352.58 |
| win rate (not a criterion) | 0.479 |
| holding minutes, mean / median | 138.1 / 180.0 |
| cost attribution USD: spread / slippage / commission | 131.31 / 234.90 / 114.56 |
| worst-case (intra-bar) flagged trades | 2 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| training (2020-12-15..2021-09-30) | 133 | 44.72 | 0.34 | 0.020 | 1.05 |
| fold 1 (2021-10-01..2022-09-30) | 193 | 709.81 | 3.68 | 0.163 | 1.60 |
| fold 2 (2022-10-01..2023-09-30) | 136 | 17.74 | 0.13 | 0.022 | 1.02 |
| fold 3 (2023-10-01..2024-09-30) | 200 | 123.68 | 0.62 | 0.038 | 1.08 |
| fold 4 (2024-10-01..2025-09-30) | 212 | 2.95 | 0.01 | 0.011 | 1.00 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| year 2020 | 12 | -108.41 | -9.03 | -0.464 | 0.12 |
| year 2021 | 164 | 350.39 | 2.14 | 0.098 | 1.36 |
| year 2022 | 191 | 594.97 | 3.12 | 0.144 | 1.46 |
| year 2023 | 134 | -57.46 | -0.43 | -0.003 | 0.94 |
| year 2024 | 217 | -88.87 | -0.41 | -0.008 | 0.96 |
| year 2025 | 156 | 208.28 | 1.34 | 0.072 | 1.17 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| BUY | 508 | 176.72 | 0.35 | 0.024 | 1.04 |
| SELL | 366 | 722.18 | 1.97 | 0.096 | 1.28 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| session ldn_ny_overlap | 457 | 989.89 | 2.17 | 0.110 | 1.31 |
| session london_morning | 417 | -90.99 | -0.22 | -0.008 | 0.97 |

## 2. All 54 points, full evaluated period (descriptive only)

Grid order, not ranked. Full-period figures are **not** a selection basis.
`stop>cap`, `stop<min` and `data_gap` are the strategy's diagnostic counts; `risk rej` =
risk-engine rejections in the run journal; `worst` = trades decided by a worst-case
intra-bar assumption. Machine-readable: `trend_pullback_2026-10-10_dev_points.csv`.

| variant | point (h1_ema, stop_atr_mult, target_r) | trades | net USD | net USD/trade | net R/trade | gross USD/trade | PF | max DD USD | win rate | stop>cap | stop<min | data_gap | risk rej | worst |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| m15 | (30, 0.25, 1) | 819 | -32.45 | -0.04 | 0.000 | 0.45 | 0.995 | 351.04 | 0.486 | 84 | 831 | 7 | 604 | 5 |
| m15 | (30, 0.25, 1.5) | 786 | 256.92 | 0.33 | 0.021 | 0.85 | 1.042 | 341.85 | 0.468 | 84 | 831 | 7 | 637 | 3 |
| m15 | (30, 0.25, 2) | 771 | 326.54 | 0.42 | 0.025 | 0.97 | 1.052 | 334.70 | 0.459 | 84 | 831 | 7 | 652 | 3 |
| m15 | (30, 0.5, 1) | 873 | -198.66 | -0.23 | -0.005 | 0.25 | 0.968 | 547.67 | 0.493 | 107 | 653 | 7 | 705 | 4 |
| m15 | (30, 0.5, 1.5) | 839 | 221.94 | 0.26 | 0.019 | 0.78 | 1.035 | 436.43 | 0.477 | 107 | 653 | 7 | 739 | 4 |
| m15 | (30, 0.5, 2) | 822 | 240.05 | 0.29 | 0.018 | 0.82 | 1.038 | 435.98 | 0.468 | 107 | 653 | 7 | 756 | 5 |
| m15 | (30, 1, 1) | 937 | 162.29 | 0.17 | 0.012 | 0.63 | 1.027 | 416.09 | 0.499 | 157 | 407 | 7 | 837 | 4 |
| m15 | (30, 1, 1.5) | 902 | 181.89 | 0.20 | 0.012 | 0.68 | 1.030 | 478.66 | 0.481 | 157 | 407 | 7 | 872 | 4 |
| m15 | (30, 1, 2) | 895 | 296.78 | 0.33 | 0.018 | 0.83 | 1.048 | 480.67 | 0.478 | 157 | 407 | 7 | 879 | 1 |
| m15 | (50, 0.25, 1) | 762 | -98.22 | -0.13 | -0.005 | 0.36 | 0.983 | 400.38 | 0.483 | 77 | 766 | 7 | 568 | 5 |
| m15 | (50, 0.25, 1.5) | 730 | 170.93 | 0.23 | 0.014 | 0.75 | 1.030 | 326.50 | 0.464 | 77 | 766 | 7 | 600 | 3 |
| m15 | (50, 0.25, 2) | 715 | 256.59 | 0.36 | 0.020 | 0.90 | 1.044 | 317.68 | 0.457 | 77 | 766 | 7 | 615 | 3 |
| m15 | (50, 0.5, 1) | 807 | -246.67 | -0.31 | -0.011 | 0.17 | 0.958 | 559.93 | 0.487 | 98 | 607 | 7 | 661 | 5 |
| m15 | (50, 0.5, 1.5) **default** | 775 | 213.88 | 0.28 | 0.018 | 0.79 | 1.036 | 437.66 | 0.475 | 98 | 607 | 7 | 693 | 5 |
| m15 | (50, 0.5, 2) | 759 | 237.31 | 0.31 | 0.019 | 0.84 | 1.040 | 437.24 | 0.468 | 98 | 607 | 7 | 709 | 6 |
| m15 | (50, 1, 1) | 864 | 195.43 | 0.23 | 0.014 | 0.68 | 1.035 | 365.12 | 0.498 | 148 | 378 | 7 | 783 | 2 |
| m15 | (50, 1, 1.5) | 830 | 183.50 | 0.22 | 0.014 | 0.70 | 1.033 | 420.37 | 0.478 | 148 | 378 | 7 | 817 | 2 |
| m15 | (50, 1, 2) | 824 | 231.79 | 0.28 | 0.017 | 0.77 | 1.041 | 424.72 | 0.476 | 148 | 378 | 7 | 823 | 0 |
| m15 | (80, 0.25, 1) | 714 | -229.14 | -0.32 | -0.013 | 0.16 | 0.957 | 418.16 | 0.476 | 73 | 733 | 7 | 529 | 5 |
| m15 | (80, 0.25, 1.5) | 684 | 115.82 | 0.17 | 0.007 | 0.69 | 1.022 | 350.67 | 0.455 | 73 | 733 | 7 | 559 | 3 |
| m15 | (80, 0.25, 2) | 670 | 174.11 | 0.26 | 0.010 | 0.79 | 1.032 | 310.02 | 0.449 | 73 | 733 | 7 | 573 | 3 |
| m15 | (80, 0.5, 1) | 759 | -430.30 | -0.57 | -0.027 | -0.09 | 0.921 | 633.76 | 0.474 | 93 | 583 | 7 | 614 | 3 |
| m15 | (80, 0.5, 1.5) | 729 | 49.65 | 0.07 | 0.004 | 0.58 | 1.009 | 423.05 | 0.465 | 93 | 583 | 7 | 644 | 3 |
| m15 | (80, 0.5, 2) | 714 | -24.69 | -0.03 | 0.000 | 0.49 | 0.996 | 446.29 | 0.458 | 93 | 583 | 7 | 659 | 4 |
| m15 | (80, 1, 1) | 815 | -6.95 | -0.01 | -0.000 | 0.44 | 0.999 | 363.01 | 0.487 | 141 | 364 | 7 | 729 | 2 |
| m15 | (80, 1, 1.5) | 783 | -32.80 | -0.04 | 0.001 | 0.43 | 0.994 | 441.50 | 0.470 | 141 | 364 | 7 | 761 | 2 |
| m15 | (80, 1, 2) | 778 | 11.02 | 0.01 | 0.002 | 0.50 | 1.002 | 438.84 | 0.468 | 141 | 364 | 7 | 766 | 0 |
| m5 | (30, 0.25, 1) | 943 | 618.04 | 0.66 | 0.032 | 1.18 | 1.091 | 373.06 | 0.507 | 143 | 1670 | 25 | 1366 | 2 |
| m5 | (30, 0.25, 1.5) | 893 | 1,012.86 | 1.13 | 0.050 | 1.70 | 1.144 | 350.03 | 0.484 | 143 | 1670 | 25 | 1416 | 3 |
| m5 | (30, 0.25, 2) | 873 | 1,047.72 | 1.20 | 0.054 | 1.78 | 1.147 | 309.29 | 0.472 | 143 | 1670 | 25 | 1436 | 3 |
| m5 | (30, 0.5, 1) | 1006 | 641.78 | 0.64 | 0.031 | 1.14 | 1.092 | 327.17 | 0.502 | 186 | 1370 | 25 | 1560 | 5 |
| m5 | (30, 0.5, 1.5) | 955 | 873.25 | 0.91 | 0.048 | 1.46 | 1.122 | 372.76 | 0.477 | 186 | 1370 | 25 | 1611 | 3 |
| m5 | (30, 0.5, 2) | 934 | 944.77 | 1.01 | 0.051 | 1.58 | 1.130 | 328.88 | 0.464 | 186 | 1370 | 25 | 1632 | 3 |
| m5 | (30, 1, 1) | 1113 | -198.59 | -0.18 | -0.005 | 0.29 | 0.973 | 585.24 | 0.482 | 271 | 816 | 25 | 1922 | 3 |
| m5 | (30, 1, 1.5) | 1067 | 376.38 | 0.35 | 0.017 | 0.86 | 1.050 | 425.95 | 0.468 | 271 | 816 | 25 | 1968 | 3 |
| m5 | (30, 1, 2) | 1051 | 515.88 | 0.49 | 0.022 | 1.02 | 1.069 | 376.43 | 0.461 | 271 | 816 | 25 | 1984 | 2 |
| m5 | (50, 0.25, 1) | 862 | 571.42 | 0.66 | 0.031 | 1.18 | 1.092 | 399.49 | 0.508 | 133 | 1520 | 25 | 1282 | 1 |
| m5 | (50, 0.25, 1.5) | 817 | 948.05 | 1.16 | 0.051 | 1.73 | 1.148 | 311.79 | 0.486 | 133 | 1520 | 25 | 1327 | 2 |
| m5 | (50, 0.25, 2) | 798 | 1,108.16 | 1.39 | 0.059 | 1.98 | 1.171 | 279.17 | 0.475 | 133 | 1520 | 25 | 1346 | 2 |
| m5 | (50, 0.5, 1) | 919 | 779.70 | 0.85 | 0.040 | 1.36 | 1.123 | 304.86 | 0.506 | 172 | 1242 | 25 | 1464 | 4 |
| m5 | (50, 0.5, 1.5) **default** | 874 | 898.90 | 1.03 | 0.054 | 1.58 | 1.136 | 352.58 | 0.479 | 172 | 1242 | 25 | 1509 | 2 |
| m5 | (50, 0.5, 2) | 853 | 957.60 | 1.12 | 0.056 | 1.69 | 1.143 | 334.96 | 0.464 | 172 | 1242 | 25 | 1530 | 2 |
| m5 | (50, 1, 1) | 1013 | -66.35 | -0.07 | -0.001 | 0.40 | 0.990 | 527.49 | 0.484 | 251 | 743 | 25 | 1790 | 2 |
| m5 | (50, 1, 1.5) | 971 | 442.17 | 0.46 | 0.020 | 0.97 | 1.065 | 383.25 | 0.469 | 251 | 743 | 25 | 1832 | 2 |
| m5 | (50, 1, 2) | 956 | 523.11 | 0.55 | 0.024 | 1.07 | 1.077 | 360.53 | 0.460 | 251 | 743 | 25 | 1847 | 2 |
| m5 | (80, 0.25, 1) | 809 | 417.00 | 0.52 | 0.025 | 1.03 | 1.071 | 391.63 | 0.506 | 128 | 1446 | 25 | 1194 | 1 |
| m5 | (80, 0.25, 1.5) | 764 | 775.04 | 1.01 | 0.047 | 1.58 | 1.128 | 354.30 | 0.483 | 128 | 1446 | 25 | 1239 | 2 |
| m5 | (80, 0.25, 2) | 747 | 916.28 | 1.23 | 0.054 | 1.81 | 1.149 | 358.50 | 0.471 | 128 | 1446 | 25 | 1256 | 3 |
| m5 | (80, 0.5, 1) | 863 | 688.51 | 0.80 | 0.038 | 1.30 | 1.115 | 252.51 | 0.504 | 167 | 1187 | 25 | 1360 | 2 |
| m5 | (80, 0.5, 1.5) | 814 | 824.44 | 1.01 | 0.054 | 1.56 | 1.133 | 324.14 | 0.479 | 167 | 1187 | 25 | 1409 | 0 |
| m5 | (80, 0.5, 2) | 796 | 873.02 | 1.10 | 0.055 | 1.67 | 1.139 | 293.80 | 0.464 | 167 | 1187 | 25 | 1427 | 1 |
| m5 | (80, 1, 1) | 947 | -212.44 | -0.22 | -0.008 | 0.24 | 0.966 | 528.04 | 0.483 | 244 | 719 | 25 | 1667 | 1 |
| m5 | (80, 1, 1.5) | 909 | 256.83 | 0.28 | 0.014 | 0.79 | 1.040 | 361.58 | 0.466 | 244 | 719 | 25 | 1705 | 2 |
| m5 | (80, 1, 2) | 895 | 336.53 | 0.38 | 0.017 | 0.90 | 1.052 | 345.86 | 0.458 | 244 | 719 | 25 | 1719 | 1 |

## 3. Registered anchored walk-forward (OOS evidence)

For fold k, the selected point has the highest net expectancy over **all development
trades entered before fold k** (training + earlier folds), among points with ≥ 100 such trades. Ties are compared exactly (integer cents): the default
wins a tie, then the lexicographically smallest (h1_ema, stop_atr_mult, target_r). The
fold's OOS trades are the selected point's trades entered in that fold, from its own run
(no rescaling).

### m15

| fold | selected point | eligible points (≥100 prior) | prior trades | prior net USD/trade | exact ties | OOS trades | OOS net USD | OOS net USD/trade |
|---|---|---|---|---|---|---|---|---|
| fold 1 | (80, 1, 2) | 24 | 120 | 0.49 | none | 179 | -168.05 | -0.94 |
| fold 2 | (50, 0.5, 1.5) | 27 | 282 | 1.13 | none | 121 | 3.79 | 0.03 |
| fold 3 | (30, 0.5, 1.5) | 27 | 440 | 0.85 | none | 195 | -102.18 | -0.52 |
| fold 4 | (30, 0.25, 2) | 27 | 568 | 0.55 | none | 203 | 14.69 | 0.07 |

#### m15 concatenated OOS (folds 1–4)

| measure | value |
|---|---|
| closed trades (long / short) | 698 (405 / 293) |
| net P&L USD | -251.75 |
| gross P&L USD (mid to mid) | 114.58 |
| net expectancy USD/trade | -0.36 |
| gross expectancy USD/trade | 0.16 |
| net / gross expectancy R/trade | -0.007 / 0.018 |
| net profit factor | 0.955 |
| max drawdown, closed-trade, USD | 400.89 |
| win rate (not a criterion) | 0.466 |
| holding minutes, mean / median | 140.1 / 180.0 |
| cost attribution USD: spread / slippage / commission | 100.04 / 181.55 / 84.74 |
| worst-case (intra-bar) flagged trades | 2 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| fold 1 (2021-10-01..2022-09-30) | 179 | -168.05 | -0.94 | -0.045 | 0.87 |
| fold 2 (2022-10-01..2023-09-30) | 121 | 3.79 | 0.03 | 0.018 | 1.00 |
| fold 3 (2023-10-01..2024-09-30) | 195 | -102.18 | -0.52 | -0.010 | 0.94 |
| fold 4 (2024-10-01..2025-09-30) | 203 | 14.69 | 0.07 | 0.016 | 1.01 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| year 2021 | 41 | -105.42 | -2.57 | -0.128 | 0.63 |
| year 2022 | 174 | 4.00 | 0.02 | 0.004 | 1.00 |
| year 2023 | 125 | -35.27 | -0.28 | 0.011 | 0.96 |
| year 2024 | 205 | -207.03 | -1.01 | -0.036 | 0.89 |
| year 2025 | 153 | 91.97 | 0.60 | 0.039 | 1.07 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| BUY | 405 | 68.17 | 0.17 | 0.018 | 1.02 |
| SELL | 293 | -319.92 | -1.09 | -0.041 | 0.87 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| session ldn_ny_overlap | 370 | 52.69 | 0.14 | 0.030 | 1.02 |
| session london_morning | 328 | -304.44 | -0.93 | -0.048 | 0.89 |

Monthly (entry month, UTC):

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| 2021-10 | 16 | -67.71 | -4.23 | -0.224 | 0.42 |
| 2021-11 | 13 | 18.31 | 1.41 | 0.064 | 1.21 |
| 2021-12 | 12 | -56.02 | -4.67 | -0.208 | 0.29 |
| 2022-01 | 14 | -6.44 | -0.46 | -0.030 | 0.94 |
| 2022-02 | 13 | -66.89 | -5.15 | -0.241 | 0.43 |
| 2022-03 | 20 | 155.24 | 7.76 | 0.320 | 3.15 |
| 2022-04 | 11 | -25.19 | -2.29 | -0.084 | 0.72 |
| 2022-05 | 21 | -170.40 | -8.11 | -0.382 | 0.30 |
| 2022-06 | 15 | -2.08 | -0.14 | -0.008 | 0.98 |
| 2022-07 | 13 | 79.10 | 6.08 | 0.319 | 2.47 |
| 2022-08 | 14 | 10.86 | 0.78 | 0.054 | 1.10 |
| 2022-09 | 17 | -36.83 | -2.17 | -0.087 | 0.70 |
| 2022-10 | 14 | 96.77 | 6.91 | 0.313 | 2.25 |
| 2022-11 | 13 | -73.54 | -5.66 | -0.217 | 0.39 |
| 2022-12 | 9 | 43.40 | 4.82 | 0.214 | 2.03 |
| 2023-01 | 11 | -112.48 | -10.23 | -0.435 | 0.17 |
| 2023-02 | 10 | -44.86 | -4.49 | -0.243 | 0.40 |
| 2023-03 | 19 | 8.02 | 0.42 | 0.045 | 1.05 |
| 2023-04 | 10 | -2.35 | -0.24 | 0.022 | 0.97 |
| 2023-05 | 8 | -1.70 | -0.21 | 0.024 | 0.96 |
| 2023-06 | 9 | -32.81 | -3.65 | -0.140 | 0.45 |
| 2023-07 | 5 | 53.52 | 10.70 | 0.510 | 13.33 |
| 2023-08 | 5 | 31.53 | 6.31 | 0.309 | 2.28 |
| 2023-09 | 8 | 38.29 | 4.79 | 0.231 | 1.92 |
| 2023-10 | 15 | -14.86 | -0.99 | -0.027 | 0.86 |
| 2023-11 | 11 | -2.89 | -0.26 | -0.003 | 0.94 |
| 2023-12 | 14 | 45.32 | 3.24 | 0.218 | 1.44 |
| 2024-01 | 16 | -91.59 | -5.72 | -0.242 | 0.53 |
| 2024-02 | 9 | -56.20 | -6.24 | -0.268 | 0.32 |
| 2024-03 | 24 | -2.65 | -0.11 | -0.004 | 0.99 |
| 2024-04 | 22 | 7.19 | 0.33 | 0.026 | 1.04 |
| 2024-05 | 20 | -3.43 | -0.17 | -0.025 | 0.98 |
| 2024-06 | 13 | -15.05 | -1.16 | -0.024 | 0.87 |
| 2024-07 | 22 | -51.63 | -2.35 | -0.131 | 0.74 |
| 2024-08 | 17 | 97.75 | 5.75 | 0.305 | 2.34 |
| 2024-09 | 12 | -14.14 | -1.18 | -0.021 | 0.85 |
| 2024-10 | 20 | -32.35 | -1.62 | -0.068 | 0.85 |
| 2024-11 | 16 | -39.29 | -2.46 | -0.116 | 0.79 |
| 2024-12 | 14 | -5.64 | -0.40 | 0.031 | 0.95 |
| 2025-01 | 23 | 66.35 | 2.88 | 0.138 | 1.31 |
| 2025-02 | 17 | 30.53 | 1.80 | 0.066 | 1.21 |
| 2025-03 | 16 | 30.35 | 1.90 | 0.101 | 1.20 |
| 2025-04 | 14 | 52.69 | 3.76 | 0.260 | 1.50 |
| 2025-05 | 17 | 101.25 | 5.96 | 0.315 | 2.20 |
| 2025-06 | 14 | -46.28 | -3.31 | -0.089 | 0.73 |
| 2025-07 | 12 | -64.18 | -5.35 | -0.248 | 0.55 |
| 2025-08 | 22 | -119.98 | -5.45 | -0.252 | 0.54 |
| 2025-09 | 18 | 41.24 | 2.29 | 0.048 | 1.31 |

#### m15: registered thresholds on the concatenated OOS trades

| gate | result |
|---|---|
| 1 sample (≥30 per fold, ≥200) | pass: [179, 121, 195, 203], total 698 |
| 2 positive after costs | FAIL: -0.36 USD/trade, -251.75 USD |
| 3 ≥3 of 4 folds positive | FAIL: 2 of 4 |
| 4 PF ≥ 1.10 | FAIL: 0.955 |
| 5 spread ×1.5 re-run | not evaluated (stress re-runs not authorised) |
| 6 max DD ≤ 1,000 USD | pass: 400.89 |
| 7 no single month | FAIL: without 2022-03 (155.24) = -406.99 |
| 8 not one direction | FAIL: BUY 405 @ 0.17, SELL 293 @ -1.09 |
| 9 not one session | FAIL: london_morning 328 @ -0.93, ldn_ny_overlap 370 @ 0.14 |
| 10 neighbourhood | not evaluated (frozen configuration not selected); informational below |
| 11 intra-bar (Phase 3b) | not evaluated (best-case replay not authorised); worst-case flagged 2 of 698 = 0.29% |
| 12 DSR ≥ 0.95 | FAIL: 0.0175 |
| 13 sealed untouched | pass: no trend_pullback run reads 2025-10-01 or later; 0 openings |

Neighbourhood (informational, gate 10 not evaluated): (80, 1, 2): 1 of 3 neighbours > 0 in folds 1–4 (needs 2: not met); (50, 0.5, 1.5): 5 of 6 neighbours > 0 in folds 1–4 (needs 4: met); (30, 0.5, 1.5): 4 of 5 neighbours > 0 in folds 1–4 (needs 4: met); (30, 0.25, 2): 3 of 3 neighbours > 0 in folds 1–4 (needs 2: met).

DSR inputs: SR -0.01600 per day, T 1032 market days, skew 0.6841, kurtosis 5.9238, V 4.709e-04 (sample variance of the 54 trials' SR, ddof 1), N 54, SR0 0.05004. With V as the population variance (ddof 0): DSR 0.0182.

### m5

| fold | selected point | eligible points (≥100 prior) | prior trades | prior net USD/trade | exact ties | OOS trades | OOS net USD | OOS net USD/trade |
|---|---|---|---|---|---|---|---|---|
| fold 1 | (80, 0.5, 1.5) | 27 | 117 | 0.88 | none | 180 | 525.26 | 2.92 |
| fold 2 | (50, 0.25, 1.5) | 27 | 300 | 2.70 | none | 128 | -6.52 | -0.05 |
| fold 3 | (50, 0.25, 1.5) | 27 | 428 | 1.87 | none | 185 | 108.42 | 0.59 |
| fold 4 | (80, 0.25, 2) | 27 | 555 | 1.58 | none | 192 | 40.81 | 0.21 |

#### m5 concatenated OOS (folds 1–4)

| measure | value |
|---|---|
| closed trades (long / short) | 685 (394 / 291) |
| net P&L USD | 667.97 |
| gross P&L USD (mid to mid) | 1,056.22 |
| net expectancy USD/trade | 0.98 |
| gross expectancy USD/trade | 1.54 |
| net / gross expectancy R/trade | 0.043 / 0.068 |
| net profit factor | 1.119 |
| max drawdown, closed-trade, USD | 358.50 |
| win rate (not a criterion) | 0.480 |
| holding minutes, mean / median | 136.9 / 180.0 |
| cost attribution USD: spread / slippage / commission | 104.62 / 191.25 / 92.38 |
| worst-case (intra-bar) flagged trades | 2 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| fold 1 (2021-10-01..2022-09-30) | 180 | 525.26 | 2.92 | 0.128 | 1.44 |
| fold 2 (2022-10-01..2023-09-30) | 128 | -6.52 | -0.05 | 0.006 | 0.99 |
| fold 3 (2023-10-01..2024-09-30) | 185 | 108.42 | 0.59 | 0.029 | 1.07 |
| fold 4 (2024-10-01..2025-09-30) | 192 | 40.81 | 0.21 | 0.003 | 1.02 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| year 2021 | 38 | 154.51 | 4.07 | 0.159 | 1.88 |
| year 2022 | 183 | 387.83 | 2.12 | 0.095 | 1.29 |
| year 2023 | 120 | 49.76 | 0.41 | 0.022 | 1.06 |
| year 2024 | 201 | 4.63 | 0.02 | 0.007 | 1.00 |
| year 2025 | 143 | 71.24 | 0.50 | 0.015 | 1.05 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| BUY | 394 | 480.21 | 1.22 | 0.058 | 1.15 |
| SELL | 291 | 187.76 | 0.65 | 0.023 | 1.08 |

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| session ldn_ny_overlap | 369 | 694.05 | 1.88 | 0.089 | 1.24 |
| session london_morning | 316 | -26.08 | -0.08 | -0.011 | 0.99 |

Monthly (entry month, UTC):

| slice | trades | net USD | net USD/trade | net R/trade | PF |
|---|---|---|---|---|---|
| 2021-10 | 17 | 4.52 | 0.27 | -0.071 | 1.05 |
| 2021-11 | 11 | 128.60 | 11.69 | 0.584 | 16.61 |
| 2021-12 | 10 | 21.39 | 2.14 | 0.083 | 1.32 |
| 2022-01 | 14 | 38.42 | 2.74 | 0.111 | 1.56 |
| 2022-02 | 11 | 0.09 | 0.01 | -0.019 | 1.00 |
| 2022-03 | 22 | 221.32 | 10.06 | 0.525 | 2.93 |
| 2022-04 | 13 | -64.68 | -4.98 | -0.241 | 0.54 |
| 2022-05 | 21 | 38.39 | 1.83 | 0.092 | 1.20 |
| 2022-06 | 16 | 7.48 | 0.47 | 0.000 | 1.06 |
| 2022-07 | 11 | 125.13 | 11.38 | 0.492 | 6.59 |
| 2022-08 | 16 | 23.43 | 1.46 | 0.064 | 1.22 |
| 2022-09 | 18 | -18.83 | -1.05 | -0.061 | 0.88 |
| 2022-10 | 14 | 63.22 | 4.52 | 0.184 | 1.79 |
| 2022-11 | 15 | -84.01 | -5.60 | -0.263 | 0.44 |
| 2022-12 | 12 | 37.87 | 3.16 | 0.140 | 1.35 |
| 2023-01 | 7 | -81.11 | -11.59 | -0.472 | 0.17 |
| 2023-02 | 10 | -82.16 | -8.22 | -0.353 | 0.21 |
| 2023-03 | 17 | 48.36 | 2.84 | 0.134 | 1.33 |
| 2023-04 | 12 | -58.98 | -4.91 | -0.190 | 0.53 |
| 2023-05 | 11 | 38.41 | 3.49 | 0.179 | 1.69 |
| 2023-06 | 11 | 1.65 | 0.15 | -0.008 | 1.03 |
| 2023-07 | 5 | 43.56 | 8.71 | 0.406 | 6.36 |
| 2023-08 | 5 | 58.84 | 11.77 | 0.519 | 421.29 |
| 2023-09 | 9 | 7.83 | 0.87 | 0.081 | 1.10 |
| 2023-10 | 13 | -1.28 | -0.10 | -0.009 | 0.99 |
| 2023-11 | 10 | -54.40 | -5.44 | -0.246 | 0.24 |
| 2023-12 | 10 | 129.04 | 12.90 | 0.485 | 5.22 |
| 2024-01 | 17 | -20.04 | -1.18 | -0.058 | 0.88 |
| 2024-02 | 10 | -50.11 | -5.01 | -0.214 | 0.55 |
| 2024-03 | 20 | 113.96 | 5.70 | 0.296 | 2.19 |
| 2024-04 | 19 | 27.86 | 1.47 | 0.050 | 1.18 |
| 2024-05 | 19 | 14.75 | 0.78 | 0.023 | 1.09 |
| 2024-06 | 16 | -48.03 | -3.00 | -0.104 | 0.70 |
| 2024-07 | 19 | -15.20 | -0.80 | -0.012 | 0.91 |
| 2024-08 | 22 | 3.33 | 0.15 | 0.011 | 1.02 |
| 2024-09 | 10 | 8.54 | 0.85 | 0.048 | 1.09 |
| 2024-10 | 22 | -80.34 | -3.65 | -0.170 | 0.69 |
| 2024-11 | 17 | 10.55 | 0.62 | -0.017 | 1.05 |
| 2024-12 | 10 | 39.36 | 3.94 | 0.239 | 1.66 |
| 2025-01 | 20 | 60.58 | 3.03 | 0.124 | 1.36 |
| 2025-02 | 17 | -32.67 | -1.92 | -0.104 | 0.81 |
| 2025-03 | 12 | 49.10 | 4.09 | 0.168 | 1.48 |
| 2025-04 | 13 | 10.93 | 0.84 | 0.098 | 1.09 |
| 2025-05 | 23 | 47.99 | 2.09 | 0.139 | 1.23 |
| 2025-06 | 13 | -72.41 | -5.57 | -0.277 | 0.60 |
| 2025-07 | 16 | -98.50 | -6.16 | -0.327 | 0.46 |
| 2025-08 | 16 | -54.10 | -3.38 | -0.145 | 0.69 |
| 2025-09 | 13 | 160.32 | 12.33 | 0.470 | 3.67 |

#### m5: registered thresholds on the concatenated OOS trades

| gate | result |
|---|---|
| 1 sample (≥30 per fold, ≥200) | pass: [180, 128, 185, 192], total 685 |
| 2 positive after costs | pass: 0.98 USD/trade, 667.97 USD |
| 3 ≥3 of 4 folds positive | pass: 3 of 4 |
| 4 PF ≥ 1.10 | pass: 1.119 |
| 5 spread ×1.5 re-run | not evaluated (stress re-runs not authorised) |
| 6 max DD ≤ 1,000 USD | pass: 358.50 |
| 7 no single month | pass: without 2022-03 (221.32) = 446.65 |
| 8 not one direction | pass: BUY 394 @ 1.22, SELL 291 @ 0.65 |
| 9 not one session | FAIL: london_morning 316 @ -0.08, ldn_ny_overlap 369 @ 1.88 |
| 10 neighbourhood | not evaluated (frozen configuration not selected); informational below |
| 11 intra-bar (Phase 3b) | not evaluated (best-case replay not authorised); worst-case flagged 2 of 685 = 0.29% |
| 12 DSR ≥ 0.95 | FAIL: 0.3434 |
| 13 sealed untouched | pass: no trend_pullback run reads 2025-10-01 or later; 0 openings |

Neighbourhood (informational, gate 10 not evaluated): (80, 0.5, 1.5): 5 of 5 neighbours > 0 in folds 1–4 (needs 4: met); (50, 0.25, 1.5): 5 of 5 neighbours > 0 in folds 1–4 (needs 4: met); (80, 0.25, 2): 3 of 3 neighbours > 0 in folds 1–4 (needs 2: met); (50, 0.5, 1.5): 6 of 6 neighbours > 0 in folds 1–4 (needs 4: met).

DSR inputs: SR 0.03766 per day, T 1032 market days, skew 0.8132, kurtosis 5.8931, V 4.709e-04 (sample variance of the 54 trials' SR, ddof 1), N 54, SR0 0.05004. With V as the population variance (ddof 0): DSR 0.3490.

## 4. Summary

| gate | m15 | m5 |
|---|---|---|
| 1 sample (≥30 per fold, ≥200) | pass: [179, 121, 195, 203], total 698 | pass: [180, 128, 185, 192], total 685 |
| 2 positive after costs | FAIL: -0.36 USD/trade, -251.75 USD | pass: 0.98 USD/trade, 667.97 USD |
| 3 ≥3 of 4 folds positive | FAIL: 2 of 4 | pass: 3 of 4 |
| 4 PF ≥ 1.10 | FAIL: 0.955 | pass: 1.119 |
| 5 spread ×1.5 re-run | not evaluated (stress re-runs not authorised) | not evaluated (stress re-runs not authorised) |
| 6 max DD ≤ 1,000 USD | pass: 400.89 | pass: 358.50 |
| 7 no single month | FAIL: without 2022-03 (155.24) = -406.99 | pass: without 2022-03 (221.32) = 446.65 |
| 8 not one direction | FAIL: BUY 405 @ 0.17, SELL 293 @ -1.09 | pass: BUY 394 @ 1.22, SELL 291 @ 0.65 |
| 9 not one session | FAIL: london_morning 328 @ -0.93, ldn_ny_overlap 370 @ 0.14 | FAIL: london_morning 316 @ -0.08, ldn_ny_overlap 369 @ 1.88 |
| 10 neighbourhood | not evaluated (frozen configuration not selected); informational below | not evaluated (frozen configuration not selected); informational below |
| 11 intra-bar (Phase 3b) | not evaluated (best-case replay not authorised); worst-case flagged 2 of 698 = 0.29% | not evaluated (best-case replay not authorised); worst-case flagged 2 of 685 = 0.29% |
| 12 DSR ≥ 0.95 | FAIL: 0.0175 | FAIL: 0.3434 |
| 13 sealed untouched | pass: no trend_pullback run reads 2025-10-01 or later; 0 openings | pass: no trend_pullback run reads 2025-10-01 or later; 0 openings |

- **m15:** **fails** registered gate(s) 2, 3, 4, 7, 8, 9, 12 — under section 16 this variant cannot pass, whatever the unevaluated gates show.
- **m5:** **fails** registered gate(s) 9, 12 — under section 16 this variant cannot pass, whatever the unevaluated gates show.

Not evaluated here (not authorised): gate 5 (spread ×1.5 re-run), gate 11 (best-case
intra-bar replay / Phase 3b), gate 10 on the frozen configuration (final candidate
selection), the null-baseline rerun over 2020-12-15 … 2025-09-30. The existing null
baseline (`reference_fixed_time`, 2020-11-12 … 2026-10-08, −381.76 USD, docs/STATUS.md)
covers another period, including the sealed one, so it is not shown side by side; its
trades were not read.
- Total trial count: 54 (the whole declared search; N for the DSR).
- Trades exiting on a date outside the market calendar in folds 1–4 (all 54 runs): 0.
