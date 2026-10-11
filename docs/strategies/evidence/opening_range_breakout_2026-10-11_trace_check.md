# opening_range_breakout 0.1.0: implementation against the feasibility traces (2026-10-11)

Generated read-only by `scripts/opening_range_breakout_trace_check.py`. **No P&L**: no fill,
exit, return or P&L is computed and nothing is written to the ledger. Whole development bar
files are loaded; the strategy sees only bars completed at each call. This is not a backtest:
there is no broker, risk engine or account, so every signal counted here is before the risk
engine.

## Inputs

- Trading dates 2020-12-15 … 2025-09-30 from the market calendar: 1237. Bar files dated
  2020-12-14 … 2025-09-30, selected by name; none in the sealed period was opened.
- Canonical M15 bars: 1752 files, digest of (name, sha256) `9bb6a7d499b08997e4f513287c2ba21d1a7662c2dbf953d04ab2ea62bba97900`.
- Canonical M5 bars: 1752 files, digest of (name, sha256) `4132cb869b1f994a00f88befb114c2ae0f6fefc9e990209df561c3dfbc9ce89e`.
- Expected values: `opening_range_breakout_2026-10-11_feasibility.csv` (the draft's per-range-date
  rows). Compared per range-date: status, `W`, spending time, `d`, eligible decision times,
  `data_gap` and `blackout_skipped`. `target_r` 1.5 (it changes no status).
- Calls: every decision-timeframe close with entries allowed (as the engine calls the strategy).

## Result

| variant | or_bars | range-dates | feasibility rows | identical | signals emitted | signal | stop_below_minimum | stop_above_cap | no_break | data_gap | range_missing | no_decision_time | Σ data_gap instants | Σ blackout_skipped |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| m15 | 2 | 2474 | 2474 | 2474 | 1383 | 1383 | 637 | 48 | 406 | 0 | 0 | 0 | 0 | 0 |
| m15 | 4 | 2474 | 2474 | 2474 | 1086 | 1086 | 309 | 85 | 994 | 0 | 0 | 0 | 0 | 0 |
| m5 | 2 | 2474 | 2474 | 2474 | 1379 | 1379 | 760 | 50 | 285 | 0 | 0 | 0 | 0 | 725 |
| m5 | 4 | 2474 | 2474 | 2474 | 1265 | 1265 | 404 | 77 | 728 | 0 | 0 | 0 | 0 | 0 |

**Mismatches: 0.**

Signals emitted equal the `signal` statuses, each at its range's spending time with the same
side and stop.

## Trace, 2024-03-01 (spec section 17)

- m15, or_bars 2: `SELL` at 08:45Z, stop 2045.91, target 2032.59, max_hold 2:00:00
- m15, or_bars 2: `BUY` at 15:15Z, stop 2045.42, target 2071.14, max_hold 2:00:00
- m5, or_bars 2: `BUY` at 15:15Z, stop 2045.42, target 2071.14, max_hold 2:00:00
