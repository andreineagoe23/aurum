# vwap_reversion 0.1.0 draft revision 4: B1 feasibility counts (2026-10-11)

Generated read-only by `scripts/vwap_reversion_feasibility.py` under the owner's B1
authorisation and rulings R1–R6 (spec section 18c), from the clean methodology commit
`a09b1964eeb39dbb75e35c50130eb35f206d4a0b` (spec blob `22d1ffacaf618e39e687d77563b491a8d57809db`, both checked before any market data was read).
Machine-readable counts: `vwap_reversion_2026-10-11_b1_feasibility.csv` (long format: variant, k_dev, er_max, scope, slice,
metric, count).

- **Descriptive counts only.** No fill, exit, return, P&L or executable price is computed;
  no tick and no ask is read. No trial, attempt or ledger row; no symbol snapshot; no sealed
  file; no strategy implementation; no rule or parameter changed.
- **Data-informed from here on.** Any later choice about this family is made knowing
  these counts and must be listed as data-informed (spec section 18b, B1).
- **Geometry-eligible signals, never trades.** `signal` means the spend passed sections 8,
  8a and 9 on bid bars. The risk engine, the broker and the quotes (section 13.2) are not
  applied, so each count is an upper bound on what a backtest could take.

## 1. Inputs and reproducibility

- Evaluated period 2020-12-15 … 2025-09-30: 1251 weekdays, 1237 market trading dates
  (`MarketHours.trading_dates`, `configs/data.yaml`), 2474 session-dates
  (`TradingCalendar` windows `london_morning`, `ldn_ny_overlap`); session windows missing on a
  trading date: 0.
- Sealed period (`configs/backtest.yaml`) starts 2025-10-01. Files were selected by
  name (UTC date ≤ 2025-09-30) before any was opened; no partition dated later was opened.
- Warm-up exactly by bar count (spec section 3.3): H1 13, M15 100, M5 none; the fewest whole
  UTC days before 2020-12-15 holding that many bars (Parquet row counts), no calendar-day buffer.
  ATR(Ts) uses exactly the 100 newest M15 bars and ER(Ts) exactly the 13 newest H1 closes.
- Canonical M5: 1751 files, 2020-12-15 … 2025-09-30, 339,999 rows; digest of (name, sha256) `64135e792fb7376fd85ddfafce32757a636971e6d1d8d55717dbfe76ccdf2c24`.
- Canonical M15: 1755 files, 2020-12-11 … 2025-09-30, 113,536 rows; digest of (name, sha256) `44a88edbcd833b14617ee4d8b9110284e4b146101f63ff9b027d298bd56d683a`.
- Canonical H1: 1752 files, 2020-12-14 … 2025-09-30, 28,377 rows; digest of (name, sha256) `b010da9d63168cf4ee173d7ecd37e3ec7e9216b3f7ae75573fa459017f254479`.
- Ruling R6: every file was scanned with the predicate `open_time < 2025-09-30T22:00:00Z`
  (the market open of trading date 2025-10-01, checked against `configs/data.yaml`).
  Rows excluded, counted only (their values were not collected): M5 24, M15 8, H1 2. No returned frame holds a row at or after the cut (asserted).
- Null `tick_count` in M5 bars read: 0. Input files re-hashed after the run: unchanged.
- Provisional recorded inputs (not a snapshot verification; B2 verifies both): point 0.01
  and `trade_stops_level` 0, copied from the frozen `docs/strategies/opening_range_breakout.md`
  (sections 8a and 8); minimum `max(500, 0 + 10)` = 500 points, the same for any
  stops level ≤ 490; cap 2000; buffer 25; ATR(14) over 100 M15 bars; ER over
  13 H1 closes, path summed with `math.fsum`; expiry 30 min; decision instants `A + 60 < T ≤ E − 15`.
- Official classifications use exact float64 comparisons; comparisons within 1e-09 of their
  threshold are reported separately (section 6) and never change a classification.
- Synthetic tests run before any market data was read: 500 cases passed.
- Interpreter: Python 3.12.15, `python.exe`, site disabled (`-S`): True.
- Command: `uv run --no-sync python scripts/vwap_reversion_feasibility.py`. The launch
  actually used, the byte-for-byte rerun check and both output files' sha256 are recorded in
  `vwap_reversion_2026-10-11_b1_run.md` and `docs/STATUS.md` (queue A4), not here, because
  this file cannot know them when it is written.

## 2. Calendar: decision instants, blackouts and hold bounds (spec 6.1, 10.1)

From the calendar and `NewsBlackout` only (no prices). London wall clock. `removed` = closes
of the variant's timeframe in the session that fall in a news window; `excluded setups` =
setup closes whose bar touches a window (rule 6).

| session | week | variant | first | last | instants | removed (blackout) | excluded setups | session-dates |
|---|---|---|---|---|---|---|---|---|
| ldn_ny_overlap | mismatch | m15 | 14:15 | 16:15 | 9 | none | 14:00, 14:15 | 79 |
| ldn_ny_overlap | mismatch | m5 | 14:15 | 16:15 | 25 | 14:05, 14:10 | 14:00, 14:15 | 79 |
| ldn_ny_overlap | normal | m15 | 14:15 | 16:15 | 8 | 15:00 | 15:00, 15:15 | 1158 |
| ldn_ny_overlap | normal | m5 | 14:05 | 16:15 | 22 | 14:50, 14:55, 15:00, 15:05, 15:10 | 15:00, 15:15 | 1158 |
| london_morning | mismatch | m15 | 08:15 | 10:45 | 11 | none | none | 79 |
| london_morning | mismatch | m5 | 08:05 | 10:45 | 33 | none | none | 79 |
| london_morning | normal | m15 | 08:15 | 10:45 | 11 | none | none | 1158 |
| london_morning | normal | m5 | 08:05 | 10:45 | 33 | none | none | 1158 |

- Session-dates matching spec table 6.1 exactly (instants, removals, rule-6 exclusions): 2474 of 2474.
- Hold-bound check `E − 15 + 90 min ≤ 12:15 / 17:45 London` (section 10.1): 0 violations.
- Session ends clipped by an early close: 0. Closes in `(A, E − 15]` whose calendar state
  is not the session with entries allowed: 0.

## 3. Read-set completeness and data quality

A session-date's read set is complete when every M5 and M15 slot with `open_time ≥ A` and
`close_time ≤ E − 15` has a bar (the largest read set of trigger rule 6).

| session | session-dates | complete | incomplete | missing M5 slots | missing M15 slots | M5 bars with tick_count 0 |
|---|---|---|---|---|---|---|
| london_morning | 1237 | 1237 | 0 | 0 | 0 | 0 |
| ldn_ny_overlap | 1237 | 1237 | 0 | 0 | 0 | 0 |
| all | 2474 | 2474 | 0 | 0 | 0 | 0 |

- Setup-window bars without ATR(Ts): 0; without ER(Ts) (fewer than 13 H1 bars or a zero path): 0.

## 4. Setup-window bars (shared by both variants, ignoring the spend)

Every M15 bar with `A + 60 ≤ Ts ≤ E − 45`, classified per point by section 5 and ruling R5
(mutually exclusive; ambiguous regardless of the regime; regime off includes ER
unavailable). `ineligible` = rule 5 (read set, TWV, ATR) or rule 6 (blackout) fails;
`no side` = neither side's rules 3–4. Setups with `TWV(Ts) − X` (short: `Y − TWV(Ts)`)
below 9.75 USD cannot meet section 13.1 unless the mean moves away from the extreme by T.

| k_dev, er_max | bars | ineligible | no side | ambiguous | regime off | setups long / short | setups london / overlap | setups with gap ≥ 9.75 |
|---|---|---|---|---|---|---|---|---|
| 1.0, 0.30 | 22266 | 2474 | 12277 | 28 | 3302 | 2081 / 2104 | 2530 / 1655 | 409 (9.8%) |
| 1.0, 0.50 | 22266 | 2474 | 12277 | 28 | 1504 | 2989 / 2994 | 3600 / 2383 | 634 (10.6%) |
| 1.5, 0.30 | 22266 | 2474 | 14565 | 4 | 2337 | 1447 / 1439 | 1682 / 1204 | 394 (13.7%) |
| 1.5, 0.50 | 22266 | 2474 | 14565 | 4 | 1101 | 2068 / 2054 | 2385 / 1737 | 611 (14.8%) |
| 2.0, 0.30 | 22266 | 2474 | 16908 | 0 | 1379 | 772 / 733 | 844 / 661 | 344 (22.9%) |
| 2.0, 0.50 | 22266 | 2474 | 16908 | 0 | 666 | 1142 / 1076 | 1218 / 1000 | 534 (24.1%) |

Ineligible bars: read set incomplete 0, touching a news window 2474, both 0.

## 5. Setup life cycle per point (spec 6.5 counters, while the session-date is unspent)

Each counted setup has one terminal outcome (rulings R2–R4): triggered (= spent), broken
(`setups_broken`), replaced (`setups_replaced`: an unbroken active setup displaced by a
newer setup) or expired (`setups_expired`). At one instant breaking precedes replacement,
and at `Ts + 30` the old setup's last trigger and break are evaluated first. The three
*non-normative* columns are not section 6.5 counters: a newer setup arriving while the old
one was already broken (`same T`: broken at that instant) or at exactly `Ts + 30` (the old
one counts expired); adding them to `setups_replaced` gives the alternative “both” counts.

| variant | k_dev, er_max | setups long / short | triggered | broken | replaced | expired | non-normative: after break | same T | at expiry | `ambiguous_setup` | `setup_regime_off` | `data_gap` | `twv_undefined` | `zero_weight_bar` | `blackout_skipped` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| m15 | 1.0, 0.30 | 2005 / 2017 | 434 | 2537 | 52 | 999 | 1989 | 1987 | 74 | 28 | 3261 | 0 | 0 | 0 | 18 |
| m15 | 1.0, 0.50 | 2872 / 2866 | 591 | 3605 | 82 | 1460 | 2908 | 2905 | 111 | 28 | 1485 | 0 | 0 | 0 | 20 |
| m15 | 1.5, 0.30 | 1390 / 1392 | 391 | 1735 | 8 | 648 | 1249 | 1242 | 24 | 4 | 2306 | 0 | 0 | 0 | 16 |
| m15 | 1.5, 0.50 | 1987 / 1981 | 527 | 2475 | 11 | 955 | 1845 | 1831 | 32 | 4 | 1084 | 0 | 0 | 0 | 18 |
| m15 | 2.0, 0.30 | 755 / 714 | 242 | 931 | 1 | 295 | 596 | 586 | 2 | 0 | 1375 | 0 | 0 | 0 | 10 |
| m15 | 2.0, 0.50 | 1118 / 1053 | 340 | 1371 | 3 | 457 | 933 | 918 | 2 | 0 | 662 | 0 | 0 | 0 | 12 |
| m5 | 1.0, 0.30 | 1684 / 1709 | 971 | 1949 | 44 | 429 | 1560 | 137 | 50 | 28 | 3126 | 0 | 0 | 0 | 98 |
| m5 | 1.0, 0.50 | 2356 / 2366 | 1353 | 2702 | 72 | 595 | 2230 | 194 | 69 | 28 | 1410 | 0 | 0 | 0 | 132 |
| m5 | 1.5, 0.30 | 1216 / 1210 | 809 | 1378 | 6 | 233 | 1008 | 87 | 11 | 4 | 2208 | 0 | 0 | 0 | 92 |
| m5 | 1.5, 0.50 | 1703 / 1678 | 1137 | 1915 | 7 | 322 | 1459 | 124 | 15 | 4 | 1027 | 0 | 0 | 0 | 124 |
| m5 | 2.0, 0.30 | 698 / 656 | 461 | 789 | 0 | 104 | 512 | 47 | 2 | 0 | 1344 | 0 | 0 | 0 | 57 |
| m5 | 2.0, 0.50 | 1029 / 956 | 688 | 1151 | 0 | 146 | 805 | 73 | 2 | 0 | 628 | 0 | 0 | 0 | 82 |

## 6. Spend, geometry and session-date statuses

Statuses with the registered precedence (section 6.5). Ruling R1: at a spend the checks
are evaluated in the order minimum, cap, reward:risk; the first failure is the sole status
and increments only its official counter (`stop_below_minimum`, `stop_above_cap`,
`reward_risk_below_min` = the status columns). The second table shows the `would_fail_*`
combination flags (not section 6.5 counters), so other aggregations stay possible.

| variant | k_dev, er_max | session-dates | signal | stop_below_minimum | stop_above_cap | reward_risk_below_min | no_decision_time | data_gap | twv_undefined | no_trigger |
|---|---|---|---|---|---|---|---|---|---|---|
| m15 | 1.0, 0.30 | 2474 | 7 | 326 | 1 | 100 | 0 | 0 | 0 | 2040 |
| m15 | 1.0, 0.50 | 2474 | 8 | 443 | 1 | 139 | 0 | 0 | 0 | 1883 |
| m15 | 1.5, 0.30 | 2474 | 7 | 287 | 1 | 96 | 0 | 0 | 0 | 2083 |
| m15 | 1.5, 0.50 | 2474 | 8 | 385 | 1 | 133 | 0 | 0 | 0 | 1947 |
| m15 | 2.0, 0.30 | 2474 | 8 | 164 | 1 | 69 | 0 | 0 | 0 | 2232 |
| m15 | 2.0, 0.50 | 2474 | 9 | 231 | 1 | 99 | 0 | 0 | 0 | 2134 |
| m5 | 1.0, 0.30 | 2474 | 22 | 821 | 1 | 127 | 0 | 0 | 0 | 1503 |
| m5 | 1.0, 0.50 | 2474 | 38 | 1124 | 1 | 190 | 0 | 0 | 0 | 1121 |
| m5 | 1.5, 0.30 | 2474 | 23 | 665 | 1 | 120 | 0 | 0 | 0 | 1665 |
| m5 | 1.5, 0.50 | 2474 | 41 | 912 | 1 | 183 | 0 | 0 | 0 | 1337 |
| m5 | 2.0, 0.30 | 2474 | 21 | 356 | 1 | 83 | 0 | 0 | 0 | 2013 |
| m5 | 2.0, 0.50 | 2474 | 38 | 520 | 1 | 129 | 0 | 0 | 0 | 1786 |

| variant | k_dev, er_max | spends | d_S < 500: rr ok / rr would fail | d_S > 2,000: rr ok / rr would fail | in bounds, rr fail | signals | spends with TWV(T) gap ≥ 9.75 | signals / spends |
|---|---|---|---|---|---|---|---|---|
| m15 | 1.0, 0.30 | 434 | 33 / 293 | 0 / 1 | 100 | 7 | 52 (12.0%) | 1.6% |
| m15 | 1.0, 0.50 | 591 | 51 / 392 | 0 / 1 | 139 | 8 | 71 (12.0%) | 1.4% |
| m15 | 1.5, 0.30 | 391 | 32 / 255 | 0 / 1 | 96 | 7 | 52 (13.3%) | 1.8% |
| m15 | 1.5, 0.50 | 527 | 50 / 335 | 0 / 1 | 133 | 8 | 71 (13.5%) | 1.5% |
| m15 | 2.0, 0.30 | 242 | 31 / 133 | 0 / 1 | 69 | 8 | 44 (18.2%) | 3.3% |
| m15 | 2.0, 0.50 | 340 | 47 / 184 | 0 / 1 | 99 | 9 | 59 (17.4%) | 2.6% |
| m5 | 1.0, 0.30 | 971 | 221 / 600 | 0 / 1 | 127 | 22 | 96 (9.9%) | 2.3% |
| m5 | 1.0, 0.50 | 1353 | 310 / 814 | 0 / 1 | 190 | 38 | 151 (11.2%) | 2.8% |
| m5 | 1.5, 0.30 | 809 | 225 / 440 | 0 / 1 | 120 | 23 | 98 (12.1%) | 2.8% |
| m5 | 1.5, 0.50 | 1137 | 322 / 590 | 0 / 1 | 183 | 41 | 156 (13.7%) | 3.6% |
| m5 | 2.0, 0.30 | 461 | 187 / 169 | 0 / 1 | 83 | 21 | 86 (18.7%) | 4.6% |
| m5 | 2.0, 0.50 | 688 | 285 / 235 | 0 / 1 | 129 | 38 | 138 (20.1%) | 5.5% |

Funnel per session-date (R1 order): session-dates → at least one setup counted → spent →
passes the minimum → passes the cap → passes reward:risk (= geometry-eligible signal).

| variant | k_dev, er_max | session-dates | ≥ 1 setup | spent | ≥ minimum | ≤ cap | signals | signals / session-dates |
|---|---|---|---|---|---|---|---|---|
| m15 | 1.0, 0.30 | 2474 | 1493 | 434 | 108 | 107 | 7 | 0.3% |
| m15 | 1.0, 0.50 | 2474 | 1960 | 591 | 148 | 147 | 8 | 0.3% |
| m15 | 1.5, 0.30 | 2474 | 1254 | 391 | 104 | 103 | 7 | 0.3% |
| m15 | 1.5, 0.50 | 2474 | 1662 | 527 | 142 | 141 | 8 | 0.3% |
| m15 | 2.0, 0.30 | 2474 | 788 | 242 | 78 | 77 | 8 | 0.3% |
| m15 | 2.0, 0.50 | 2474 | 1096 | 340 | 109 | 108 | 9 | 0.4% |
| m5 | 1.0, 0.30 | 2474 | 1493 | 971 | 150 | 149 | 22 | 0.9% |
| m5 | 1.0, 0.50 | 2474 | 1960 | 1353 | 229 | 228 | 38 | 1.5% |
| m5 | 1.5, 0.30 | 2474 | 1254 | 809 | 144 | 143 | 23 | 0.9% |
| m5 | 1.5, 0.50 | 2474 | 1662 | 1137 | 225 | 224 | 41 | 1.7% |
| m5 | 2.0, 0.30 | 2474 | 788 | 461 | 105 | 104 | 21 | 0.8% |
| m5 | 2.0, 0.50 | 2474 | 1096 | 688 | 168 | 167 | 38 | 1.5% |

Float comparisons within 1e-09 of their threshold (deviation, new extreme, ER, break, rejection, room to the mean), summed over all points and variants; reported only, never used to change a classification: break 188, extreme 642, rejection 119.

## 7. Counts by fold, session and side

Signals (geometry-eligible) by fold of the decision date (UTC; equal to the London date for
every session instant), and over folds 1–4 by session and side. Gates 1, 8 and 9 need
≥ 30 closed trades per OOS fold, per side and per session, and ≥ 200 in total; signals
are an upper bound on trades.

| variant | k_dev, er_max | training: signals / spends | fold 1: signals / spends | fold 2: signals / spends | fold 3: signals / spends | fold 4: signals / spends | folds 1–4 signals | long / short | london / overlap |
|---|---|---|---|---|---|---|---|---|---|
| m15 | 1.0, 0.30 | 2 / 60 | 2 / 111 | 0 / 94 | 0 / 89 | 3 / 80 | 5 | 2 / 3 | 4 / 1 |
| m15 | 1.0, 0.50 | 2 / 94 | 2 / 143 | 1 / 124 | 1 / 118 | 2 / 112 | 6 | 2 / 4 | 3 / 3 |
| m15 | 1.5, 0.30 | 2 / 54 | 2 / 100 | 0 / 85 | 0 / 80 | 3 / 72 | 5 | 2 / 3 | 4 / 1 |
| m15 | 1.5, 0.50 | 2 / 84 | 2 / 125 | 1 / 112 | 1 / 107 | 2 / 99 | 6 | 2 / 4 | 3 / 3 |
| m15 | 2.0, 0.30 | 2 / 33 | 2 / 65 | 0 / 53 | 0 / 49 | 4 / 42 | 6 | 2 / 4 | 4 / 2 |
| m15 | 2.0, 0.50 | 2 / 54 | 2 / 84 | 1 / 75 | 1 / 68 | 3 / 59 | 7 | 2 / 5 | 3 / 4 |
| m5 | 1.0, 0.30 | 1 / 162 | 1 / 214 | 2 / 212 | 4 / 186 | 14 / 197 | 21 | 11 / 10 | 11 / 10 |
| m5 | 1.0, 0.50 | 2 / 226 | 2 / 290 | 3 / 283 | 9 / 269 | 22 / 285 | 36 | 19 / 17 | 15 / 21 |
| m5 | 1.5, 0.30 | 1 / 130 | 2 / 186 | 2 / 181 | 4 / 161 | 14 / 151 | 22 | 11 / 11 | 12 / 10 |
| m5 | 1.5, 0.50 | 3 / 186 | 3 / 245 | 3 / 247 | 9 / 233 | 23 / 226 | 38 | 20 / 18 | 17 / 21 |
| m5 | 2.0, 0.30 | 1 / 71 | 2 / 117 | 2 / 101 | 4 / 98 | 12 / 74 | 20 | 10 / 10 | 11 / 9 |
| m5 | 2.0, 0.50 | 3 / 114 | 3 / 159 | 3 / 148 | 9 / 151 | 20 / 116 | 35 | 18 / 17 | 16 / 19 |

Signals by calendar year (2020 and 2025 are partial):

| variant | k_dev, er_max | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|
| m15 | 1.0, 0.30 | 0 | 2 | 2 | 0 | 0 | 3 |
| m15 | 1.0, 0.50 | 0 | 2 | 2 | 1 | 1 | 2 |
| m15 | 1.5, 0.30 | 0 | 2 | 2 | 0 | 0 | 3 |
| m15 | 1.5, 0.50 | 0 | 2 | 2 | 1 | 1 | 2 |
| m15 | 2.0, 0.30 | 0 | 2 | 2 | 0 | 0 | 4 |
| m15 | 2.0, 0.50 | 0 | 2 | 2 | 1 | 1 | 3 |
| m5 | 1.0, 0.30 | 0 | 1 | 1 | 3 | 4 | 13 |
| m5 | 1.0, 0.50 | 0 | 2 | 2 | 4 | 9 | 21 |
| m5 | 1.5, 0.30 | 0 | 1 | 2 | 3 | 4 | 13 |
| m5 | 1.5, 0.50 | 0 | 3 | 3 | 4 | 9 | 22 |
| m5 | 2.0, 0.30 | 0 | 1 | 2 | 3 | 4 | 11 |
| m5 | 2.0, 0.50 | 0 | 3 | 3 | 4 | 9 | 19 |

## 8. Selection boundaries: ≥ 100 strictly prior geometry-eligible signals

Section 16: fold k selects among points with ≥ 100 development trades entered before the
fold starts (00:00 UTC); the frozen configuration needs ≥ 100 over the whole period.
Signals strictly before each boundary are an upper bound on those trades: below 100 means
the point cannot be selected there; 100 or more does not mean it can. ✓ = at least 100.

| variant | k_dev, er_max | before 2021-10-01 | before 2022-10-01 | before 2023-10-01 | before 2024-10-01 | whole period |
|---|---|---|---|---|---|---|
| m15 | 1.0, 0.30 | 2 ✗ | 4 ✗ | 4 ✗ | 4 ✗ | 7 ✗ |
| m15 | 1.0, 0.50 | 2 ✗ | 4 ✗ | 5 ✗ | 6 ✗ | 8 ✗ |
| m15 | 1.5, 0.30 | 2 ✗ | 4 ✗ | 4 ✗ | 4 ✗ | 7 ✗ |
| m15 | 1.5, 0.50 | 2 ✗ | 4 ✗ | 5 ✗ | 6 ✗ | 8 ✗ |
| m15 | 2.0, 0.30 | 2 ✗ | 4 ✗ | 4 ✗ | 4 ✗ | 8 ✗ |
| m15 | 2.0, 0.50 | 2 ✗ | 4 ✗ | 5 ✗ | 6 ✗ | 9 ✗ |
| m5 | 1.0, 0.30 | 1 ✗ | 2 ✗ | 4 ✗ | 8 ✗ | 22 ✗ |
| m5 | 1.0, 0.50 | 2 ✗ | 4 ✗ | 7 ✗ | 16 ✗ | 38 ✗ |
| m5 | 1.5, 0.30 | 1 ✗ | 3 ✗ | 5 ✗ | 9 ✗ | 23 ✗ |
| m5 | 1.5, 0.50 | 3 ✗ | 6 ✗ | 9 ✗ | 18 ✗ | 41 ✗ |
| m5 | 2.0, 0.30 | 1 ✗ | 3 ✗ | 5 ✗ | 9 ✗ | 21 ✗ |
| m5 | 2.0, 0.50 | 3 ✗ | 6 ✗ | 9 ✗ | 18 ✗ | 38 ✗ |

- m15: points with ≥ 100 at every boundary: 0 of 6; points with ≥ 100 before fold 1: 0 of 6.
- m5: points with ≥ 100 at every boundary: 0 of 6; points with ≥ 100 before fold 1: 0 of 6.

## 9. k_dev: geometry-eligible counts versus which setup spends

Per variant and `er_max`, each pair of `k_dev` values compared session-date by session-date.
`same` / `diff` = both spent (or both signalled) on the same / a different setup (Ts, side).
`pre-empted` = a signal only at the higher `k_dev` where the lower one spent earlier on a
setup that failed the geometry.

| variant | er_max | k_dev | signals | spent both: same / diff | spent only lower / higher | signal both: same / diff | signal only lower | signal only higher | of which pre-empted |
|---|---|---|---|---|---|---|---|---|---|
| m15 | 0.30 | 1.0 vs 1.5 | 7 / 7 | 385 / 6 | 43 / 0 | 7 / 0 | 0 | 0 | 0 |
| m15 | 0.30 | 1.5 vs 2.0 | 7 / 8 | 234 / 8 | 149 / 0 | 7 / 0 | 0 | 1 | 1 |
| m15 | 0.30 | 1.0 vs 2.0 | 7 / 8 | 232 / 10 | 192 / 0 | 7 / 0 | 0 | 1 | 1 |
| m15 | 0.50 | 1.0 vs 1.5 | 8 / 8 | 519 / 8 | 64 / 0 | 8 / 0 | 0 | 0 | 0 |
| m15 | 0.50 | 1.5 vs 2.0 | 8 / 9 | 331 / 9 | 187 / 0 | 8 / 0 | 0 | 1 | 1 |
| m15 | 0.50 | 1.0 vs 2.0 | 8 / 9 | 327 / 13 | 251 / 0 | 8 / 0 | 0 | 1 | 1 |
| m5 | 0.30 | 1.0 vs 1.5 | 22 / 23 | 745 / 64 | 162 / 0 | 22 / 0 | 0 | 1 | 1 |
| m5 | 0.30 | 1.5 vs 2.0 | 23 / 21 | 419 / 42 | 348 / 0 | 20 / 0 | 3 | 1 | 1 |
| m5 | 0.30 | 1.0 vs 2.0 | 22 / 21 | 391 / 70 | 510 / 0 | 19 / 0 | 3 | 2 | 2 |
| m5 | 0.50 | 1.0 vs 1.5 | 38 / 41 | 1032 / 104 | 217 / 1 | 38 / 0 | 0 | 3 | 3 |
| m5 | 0.50 | 1.5 vs 2.0 | 41 / 38 | 614 / 74 | 449 / 0 | 37 / 0 | 4 | 1 | 1 |
| m5 | 0.50 | 1.0 vs 2.0 | 38 / 38 | 569 / 119 | 665 / 0 | 34 / 0 | 4 | 4 | 4 |

## 10. m15 and m5: overlap and dependence (dates and setups, no returns)

Per point, session-date by session-date. `same setup` = both variants spent the same setup
(Ts, side); `m5 earlier` = of those, m5 spent at an earlier instant.

| k_dev, er_max | spent both | same setup | m5 earlier | spent only m15 / m5 | signal both (same setup) | signal only m15 / m5 | dates with a signal: both / either |
|---|---|---|---|---|---|---|---|
| 1.0, 0.30 | 415 | 312 | 267 | 19 / 556 | 2 (1) | 5 / 20 | 2 / 27 (7.4%) |
| 1.0, 0.50 | 568 | 408 | 347 | 23 / 785 | 4 (3) | 4 / 34 | 4 / 41 (9.8%) |
| 1.5, 0.30 | 373 | 305 | 262 | 18 / 436 | 2 (1) | 5 / 21 | 2 / 28 (7.1%) |
| 1.5, 0.50 | 505 | 399 | 344 | 22 / 632 | 4 (3) | 4 / 37 | 4 / 44 (9.1%) |
| 2.0, 0.30 | 231 | 211 | 192 | 11 / 230 | 2 (1) | 6 / 19 | 2 / 27 (7.4%) |
| 2.0, 0.50 | 325 | 292 | 263 | 15 / 363 | 4 (3) | 5 / 34 | 4 / 43 (9.3%) |

## 11. Reconciliation and invariants (all asserted by the script; a failure stops it)

- Statuses: exactly one per session-date; spent statuses = spends; per point, the fold,
  session and year splits sum to the totals (checked below for every point and variant).
- Terminal outcomes: per session-date, triggered + broken + replaced + expired = counted setups, and
  triggered = spends.
- No spend at a blackout instant; no spend at or after the first missing slot; every
  signal has `TWV(T) − X ≥ 9.75 USD` (section 13.1, necessary).
- Look-ahead guard: every bar read names its instant and is refused if it closes later.
- No mutation: every input file re-hashed after the run, unchanged. Modules never imported:
  `aurum.ledger`, `aurum.backtest`, `aurum.adapters.storage.specs`, `aurum.adapters.mt5`, `aurum.execution`, `aurum.risk`, `aurum.strategies`.

- counted setups = shared list up to the spend: 29,688 (variant, point, session-date) cases, all equal.
- identical on a store truncated at the decision: 29,688 (variant, point, session-date) cases, all equal.
- spend re-derived independently: 29,688 (variant, point, session-date) cases, all equal.

Reconciled for all 12 (variant, point) pairs over 2,474 session-dates.

