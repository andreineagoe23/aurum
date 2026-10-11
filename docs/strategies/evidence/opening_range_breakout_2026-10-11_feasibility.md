# opening_range_breakout 0.1.0 draft: feasibility evidence (2026-10-11)

Generated read-only by `scripts/opening_range_breakout_feasibility.py`. **No P&L**: no fill,
exit, return or P&L is computed, and nothing is written to the ledger. Whole development
bar files are loaded, so prices after a spending close are in memory, but no such price
is used for any count, fill, exit, return or P&L. Per-range-day rows: `opening_range_breakout_2026-10-11_feasibility.csv`.

## Inputs

- Trading dates 2020-12-15 … 2025-09-30 from the market calendar (`configs/data.yaml`): 1237.
- Sealed period from `configs/backtest.yaml`: first 2025-10-01. Bar files were selected by
  name (dates 2020-12-14 … 2025-09-30) before reading; none dated in the sealed period was opened.
- Canonical M15 bars: 1752 files, 2020-12-14 … 2025-09-30; digest of (name, sha256) `9bb6a7d499b08997e4f513287c2ba21d1a7662c2dbf953d04ab2ea62bba97900`.
- Canonical M5 bars: 1752 files, 2020-12-14 … 2025-09-30; digest of (name, sha256) `4132cb869b1f994a00f88befb114c2ae0f6fefc9e990209df561c3dfbc9ce89e`.
- Symbol point 0.01 (pinned snapshot `20261010T000046Z.json`).
- Session eligibility: `TradingCalendar.state_at(T)` (session name and `entries_allowed`), the
  same calendar the backtests use, early closes included. News: `NewsBlackout.reason_at(T)`
  on `configs/risk.yaml` `news_blackout`, the risk engine's own object.
- Draft rules: anchors 08:00 London / 09:30 New York; `or_bars` [2, 4]; window 120 min;
  stop 20 points beyond the opposite side; bounds 500 / 2000 points.

Statuses per range-day (draft rules, blackout closes neither signal nor spend):

- `signal`: the spending close gives `500 ≤ d ≤ 2,000`; `stop_below_minimum`, `stop_above_cap`:
  the spending close is outside the bounds; `no_break`: no eligible close outside the range;
  `data_gap`: the range exists but a read-set bar is missing before any spend;
  `range_missing`: a range bar is missing; `no_decision_time`: no eligible instant that day.

## 1. Counts per range, `or_bars` and variant (whole period)

| range, or_bars, variant | range-days | no decision time | range missing | data_gap days | no break | spent | d < 500 | d > 2,000 | signals | W p10 / p50 / p90 | d p10 / p50 / p90 / p99 | share < 500 | share > 2,000 | `data_gap` count | `blackout_skipped` count |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| london, 2, m15 | 1237 | 0 | 0 | 0 | 122 | 1115 | 558 | 5 | 552 | 213 / 383 / 739 | 291 / 499 / 982 / 1759 | 50.0% | 0.4% | 0 | 0 |
| london, 2, m5 | 1237 | 0 | 0 | 0 | 84 | 1153 | 662 | 5 | 486 | 213 / 383 / 739 | 272 / 458 / 866 / 1601 | 57.4% | 0.4% | 0 | 0 |
| london, 4, m15 | 1237 | 0 | 0 | 0 | 330 | 907 | 292 | 13 | 602 | 288 / 529 / 1046 | 348 / 619 / 1186 / 2073 | 32.2% | 1.4% | 0 | 0 |
| london, 4, m5 | 1237 | 0 | 0 | 0 | 226 | 1011 | 379 | 12 | 620 | 288 / 529 / 1046 | 339 / 584 / 1107 / 2073 | 37.5% | 1.2% | 0 | 0 |
| newyork, 2, m15 | 1237 | 0 | 0 | 0 | 284 | 953 | 79 | 43 | 831 | 414 / 716 / 1329 | 524 / 906 / 1634 / 2881 | 8.3% | 4.5% | 0 | 0 |
| newyork, 2, m5 | 1237 | 0 | 0 | 0 | 201 | 1036 | 98 | 45 | 893 | 414 / 716 / 1329 | 510 / 860 / 1590 / 2758 | 9.5% | 4.3% | 0 | 725 |
| newyork, 4, m15 | 1237 | 0 | 0 | 0 | 664 | 573 | 17 | 72 | 484 | 569 / 988 / 1898 | 668 / 1181 / 2107 / 4188 | 3.0% | 12.6% | 0 | 0 |
| newyork, 4, m5 | 1237 | 0 | 0 | 0 | 502 | 735 | 25 | 65 | 645 | 569 / 988 / 1898 | 656 / 1066 / 1940 / 3913 | 3.4% | 8.8% | 0 | 0 |

## 2. Signals and bound shares by year

`signals` / `spent` (share below 500, share above 2,000):

| range, or_bars, variant | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|
| london, 2, m15 | 8 / 10 (20.0%, 0.0%) | 88 / 232 (62.1%, 0.0%) | 111 / 232 (52.2%, 0.0%) | 67 / 232 (71.1%, 0.0%) | 130 / 239 (45.6%, 0.0%) | 148 / 170 (10.0%, 2.9%) |
| london, 2, m5 | 7 / 10 (30.0%, 0.0%) | 62 / 238 (73.9%, 0.0%) | 99 / 238 (58.4%, 0.0%) | 57 / 242 (76.4%, 0.0%) | 107 / 246 (56.5%, 0.0%) | 154 / 179 (11.2%, 2.8%) |
| london, 4, m15 | 7 / 8 (12.5%, 0.0%) | 105 / 188 (44.1%, 0.0%) | 130 / 191 (30.9%, 1.0%) | 92 / 186 (50.5%, 0.0%) | 143 / 193 (24.9%, 1.0%) | 125 / 141 (5.0%, 6.4%) |
| london, 4, m5 | 9 / 9 (0.0%, 0.0%) | 99 / 209 (52.6%, 0.0%) | 130 / 211 (37.9%, 0.5%) | 97 / 211 (54.0%, 0.0%) | 147 / 216 (31.0%, 0.9%) | 138 / 155 (5.2%, 5.8%) |
| newyork, 2, m15 | 8 / 8 (0.0%, 0.0%) | 167 / 187 (9.1%, 1.6%) | 175 / 201 (10.4%, 2.5%) | 162 / 191 (12.6%, 2.6%) | 196 / 218 (6.4%, 3.7%) | 123 / 148 (2.0%, 14.9%) |
| newyork, 2, m5 | 9 / 9 (0.0%, 0.0%) | 182 / 206 (10.2%, 1.5%) | 188 / 219 (12.3%, 1.8%) | 174 / 209 (14.8%, 1.9%) | 209 / 232 (6.9%, 3.0%) | 131 / 161 (1.9%, 16.8%) |
| newyork, 4, m15 | 6 / 6 (0.0%, 0.0%) | 98 / 113 (4.4%, 8.8%) | 103 / 115 (3.5%, 7.0%) | 109 / 119 (2.5%, 5.9%) | 104 / 127 (3.9%, 14.2%) | 64 / 93 (0.0%, 31.2%) |
| newyork, 4, m5 | 8 / 8 (0.0%, 0.0%) | 132 / 148 (4.7%, 6.1%) | 142 / 154 (4.5%, 3.2%) | 143 / 154 (2.6%, 4.5%) | 136 / 158 (4.4%, 9.5%) | 84 / 113 (0.0%, 25.7%) |

Signals per year, averaged over the full years 2021–2024:

| variant, or_bars | london | newyork | total | per week |
|---|---|---|---|---|
| m15, 2 | 99.0 | 175.0 | 274.0 | 5.3 |
| m5, 2 | 81.2 | 188.2 | 269.5 | 5.2 |
| m15, 4 | 117.5 | 103.5 | 221.0 | 4.2 |
| m5, 4 | 118.2 | 138.2 | 256.5 | 4.9 |

## 3. O2: stop basis (opposite side vs range midpoint)

Same spending closes; only the stop rule differs (midpoint = `H − W/2` for a long, ± 20 points).

| range, or_bars, variant | spending closes | d < 500, opposite side | d < 500, midpoint |
|---|---|---|---|
| london, 2, m15 | 1115 | 50.0% | 81.9% |
| london, 2, m5 | 1153 | 57.4% | 89.9% |
| london, 4, m15 | 907 | 32.2% | 71.9% |
| london, 4, m5 | 1011 | 37.5% | 79.0% |
| newyork, 2, m15 | 953 | 8.3% | 42.5% |
| newyork, 2, m5 | 1036 | 9.5% | 48.9% |
| newyork, 4, m15 | 573 | 3.0% | 27.9% |
| newyork, 4, m5 | 735 | 3.4% | 33.3% |

## 4. O3: news-blackout treatment, on one population

Population: range-days on which rule (a) met no missing range or read-set bar (status
`signal`, a stop bound, or `no_break`); rule (b) reads a subset of the same bars, because it
spends at or before (a)'s spending close. (a) the draft rule: a close at a blackout instant neither
signals nor spends; (b) the alternative: it spends the range, and the signal is one the risk
engine must reject. Only windows that contain a blackout instant can differ.

| range, or_bars, variant | range-days | (a) spent | (a) spent after a skipped close outside | share | (b) spent | (b) spent at a blackout instant (lost) | share |
|---|---|---|---|---|---|---|---|
| london, 2, m15 | 1237 | 1115 | 0 | 0.0% | 1115 | 0 | 0.0% |
| london, 2, m5 | 1237 | 1153 | 0 | 0.0% | 1153 | 0 | 0.0% |
| london, 4, m15 | 1237 | 907 | 0 | 0.0% | 907 | 0 | 0.0% |
| london, 4, m5 | 1237 | 1011 | 0 | 0.0% | 1011 | 0 | 0.0% |
| newyork, 2, m15 | 1237 | 953 | 0 | 0.0% | 953 | 0 | 0.0% |
| newyork, 2, m5 | 1237 | 1036 | 468 | 45.2% | 1055 | 487 | 46.2% |
| newyork, 4, m15 | 1237 | 573 | 0 | 0.0% | 573 | 0 | 0.0% |
| newyork, 4, m5 | 1237 | 735 | 0 | 0.0% | 735 | 0 | 0.0% |

## 5. Eligible decision times (calendar only, no prices)

Every eligible decision time of each range-day's window (rules 6.1–6.4), whether or not
the range would be spent earlier. Local times; `timeout` = last eligible time + 120 min, the
latest `max_hold` deadline. DST regime: `normal` when 09:30 New York is 14:30 London,
`mismatch` when it is 13:30 London.

| range, or_bars, variant | DST regime | first (local) | last (local) | eligible times | blackout instants skipped | timeout of the last | days |
|---|---|---|---|---|---|---|---|
| london, 2, m15 | mismatch | 08:45 | 10:30 | 8 | 0 | 12:30 London / 08:30 New York | 79 |
| london, 2, m15 | normal | 08:45 | 10:30 | 8 | 0 | 12:30 London / 07:30 New York | 1158 |
| london, 2, m5 | mismatch | 08:35 | 10:30 | 24 | 0 | 12:30 London / 08:30 New York | 79 |
| london, 2, m5 | normal | 08:35 | 10:30 | 24 | 0 | 12:30 London / 07:30 New York | 1158 |
| london, 4, m15 | mismatch | 09:15 | 10:45 | 7 | 0 | 12:45 London / 08:45 New York | 79 |
| london, 4, m15 | normal | 09:15 | 10:45 | 7 | 0 | 12:45 London / 07:45 New York | 1158 |
| london, 4, m5 | mismatch | 09:05 | 10:55 | 23 | 0 | 12:55 London / 08:55 New York | 79 |
| london, 4, m5 | normal | 09:05 | 10:55 | 23 | 0 | 12:55 London / 07:55 New York | 1158 |
| newyork, 2, m15 | mismatch | 10:15 | 12:00 | 8 | 0 | 18:00 London / 14:00 New York | 79 |
| newyork, 2, m15 | normal | 10:15 | 11:15 | 5 | 0 | 18:15 London / 13:15 New York | 1158 |
| newyork, 2, m5 | mismatch | 10:15 | 12:00 | 22 | 2 | 18:00 London / 14:00 New York | 79 |
| newyork, 2, m5 | normal | 10:15 | 11:25 | 15 | 2 | 18:25 London / 13:25 New York | 1158 |
| newyork, 4, m15 | mismatch | 10:45 | 12:15 | 7 | 0 | 18:15 London / 14:15 New York | 79 |
| newyork, 4, m15 | normal | 10:45 | 11:15 | 3 | 0 | 18:15 London / 13:15 New York | 1158 |
| newyork, 4, m5 | mismatch | 10:35 | 12:25 | 23 | 0 | 18:25 London / 14:25 New York | 79 |
| newyork, 4, m5 | normal | 10:35 | 11:25 | 11 | 0 | 18:25 London / 13:25 New York | 1158 |

Local = London for `london`, New York for `newyork`. Rows with few days are early closes.

## 6. Trace, 2024-03-01 (spec section 17)

**london, or_bars 2, m15:** status `signal`, d 533 points

- range bar 08:00–08:15Z: H 2045.71 L 2043.25
- range bar 08:15–08:30Z: H 2043.89 L 2042.04
- H 2045.71, L 2042.04, W 367 points
- 08:45Z (08:45 London, 03:45 New York): close 2040.58, outside

**london, or_bars 2, m5:** status `stop_below_minimum`, d 459 points

- range bar 08:00–08:15Z: H 2045.71 L 2043.25
- range bar 08:15–08:30Z: H 2043.89 L 2042.04
- H 2045.71, L 2042.04, W 367 points
- 08:35Z (08:35 London, 03:35 New York): close 2041.32, outside

**newyork, or_bars 2, m15:** status `signal`, d 1029 points

- range bar 14:30–14:45Z: H 2054.85 L 2051.29
- range bar 14:45–15:00Z: H 2051.70 L 2045.62
- H 2054.85, L 2045.62, W 923 points
- 15:15Z (15:15 London, 10:15 New York): close 2055.71, outside

**newyork, or_bars 2, m5:** status `signal`, d 1029 points

- range bar 14:30–14:45Z: H 2054.85 L 2051.29
- range bar 14:45–15:00Z: H 2051.70 L 2045.62
- H 2054.85, L 2045.62, W 923 points
- 15:05Z (15:05 London, 10:05 New York): close 2050.71, blackout
- 15:10Z (15:10 London, 10:10 New York): close 2052.63, blackout
- 15:15Z (15:15 London, 10:15 New York): close 2055.71, outside
