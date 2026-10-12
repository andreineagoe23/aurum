# Collected XAUUSD ticks: first data-quality checks (2026-10-12)

Read-only checks the owner asked for on 2026-10-12, limited to UTC dates ≥ 2026-10-09 (the
sealed period ends 2026-10-08). No strategy, backtest, trial, daily task or Dukascopy work.

## 1. Collector restart: first chunks written

The restarted collector (2026-10-12 15:31:56Z) first flushed two chunks:

| File (under `C:\aurum-data`) | Rows | Ticks |
|---|---|---|
| `raw_ticks/mt5_live/XAUUSD/2026-10-11/1791756060083_1791763199779_13610.parquet` | 13,610 | 22:01:00.083Z … 23:59:59.779Z |
| `raw_ticks/mt5_live/XAUUSD/2026-10-12/1791763200031_1791819416603_186494.parquet` | 186,494 | 00:00:00.031Z … 15:36:56.603Z |

These 200,104 rows equal the heartbeat's `rows_written`. Both chunks were fetched from the
terminal's history at restart, covering the outage. Near-real-time chunks follow from there.

## 2. `aurum data qa --from 2026-10-09 --strict`: not run

The command would breach the date limit (`src/aurum/data/qa.py`):

- `check_span` starts at the previous session's close, so a run from 2026-10-09 opens the
  canonical M1 file dated **2026-10-08** (line 301);
- the stale-quote loop reads normalized ticks from `start − 1 day`, i.e. **2026-10-08**
  (line 322);
- `_fidelity_by_day` reads the whole day manifest, sealed days included.

It would also say little: canonical bars and normalized ticks for ≥ 2026-10-09 do not exist,
because `aurum data resample` was not run (it rebuilds every day, sealed ones too; corrected
below). So market-hour gaps, stale quotes and QA's spread flags are **not reported here**.
Section 4 covers spreads directly.

> **Correction (2026-10-12, post-closure review).** The parenthesis above is inaccurate.
> `aurum data resample` accepts `--from/--to`, and each day's build (`pipeline.build_day`)
> reads only that day's raw files. Without a range it does rebuild every day. Even with a
> range, it reads and rewrites the whole day manifest (`canonical_bars/days/<symbol>.parquet`,
> `write_day_manifest`), which holds metadata rows for sealed days (fidelity and mismatch
> counts, no prices), and it reads the newest spec snapshot for the symbol and point. No raw
> broker bars exist for dates ≥ 2026-10-09 (the backfill in section 3 used `--no-bars`), so
> those days would come out with tick fidelity `unverified`. Under the owner's ruling of
> 2026-10-12 the seal also covers pipeline processing: no `resample` or `qa` runs over sealed
> days and no sealed-day manifest row is rewritten until a date-bounded pipeline is designed
> and approved (`docs/STATUS.md`, queue D, S3). The conclusion of this section is unchanged.

**Collector journal** (`collector/XAUUSD/events.jsonl`, not market data; it starts
2026-10-09 08:48:09Z): 2 `started`, 1 `stopped`, 1 `outage`, 20 `pause` (46,882 s in all,
longest 18,910 s), 15 `disconnected`, 17 `connected`, 16 `connect_failed`, **0 `gap`**. A
pause is the VM sleeping or freezing; the collector re-fetches the missed span from history
when it resumes.

## 3. Live vs history, 2026-10-09

**What the backfill can touch**, confirmed in the code before running it:

- `aurum data backfill --no-bars --from 2026-10-09 --to 2026-10-09` asks MT5 for ticks in
  [2026-10-09 00:00Z, 2026-10-10 00:00Z] (+1 ms) and keeps only `start ≤ time < end`
  (`MT5Broker.tick_frame`).
- It writes one file, `raw_ticks/mt5_history/XAUUSD/2026-10-09.parquet`, with
  `write_parquet(overwrite=False)` (an atomic hard link that fails if the file exists).
- Live chunks live in another directory (`raw_ticks/mt5_live/…`), so they cannot be
  overwritten.
- No local file of another day is opened. Only the target path's existence is checked
  (`Backfiller.ticks`).

**Run** (2026-10-12 15:45:35Z, venv interpreter via the recorded `venv_exec.py` wrapper,
sha256 `7714dc0e…`; the venv's `aurum.exe` cannot start from the agent's process):

- 1 file written, 230,284 rows, 0 skipped, 0 empty days. Report:
  `quality_reports/backfill/20261012T154537742373Z.json`.
- The 2026-10-08 history file kept its 2026-10-09 08:55Z write time. The collector kept
  running throughout.

**Comparison** on (time, bid, ask), as in `aurum.data.qa.live_vs_history`:

| | rows |
|---|---|
| collected (first run) | 230,271 |
| history (backfill) | 230,284 |
| only in collected | **0** |
| only in history | **13** (UTC hours 02: 1, 08: 2, 09: 9, 11: 1) |
| flags differ on matched rows | 194,564 (expected: history ticks carry flag bit 128) |

- Both sources span 00:00:00.016Z … 20:58:59.071Z.
- Everything the collector stored is in history. History holds 13 more rows (0.006%).
- "Collected" is not a pure live stream. The first run started at 08:48:09Z and fetched
  00:00–08:48 from history at start-up; it also re-fetched three pause spans on reconnect.
  The rest it polled in near real time (`copy_ticks_range`, cursor to now).
- Reading: history fetched later is at least as complete as near-real-time collection. On
  this one day, re-fetching an outage from history (as on 2026-10-12) loses nothing.

## 4. Fusion spread by session

Points of 0.01 USD (pinned snapshot `20261010T000046Z.json`); `sessions.yaml` windows;
tick-weighted; ticks before 2026-10-12 15:30Z. 0 invalid, 0 crossed.

| Segment | Session | Ticks | Median | Mean | p90 | p99 | Max | > 20 pts |
|---|---|---|---|---|---|---|---|---|
| 2026-10-09 | london_morning | 42,320 | 6 | 6.54 | 10 | 13 | 24 | 0.04% |
| 2026-10-09 | ldn_ny_overlap | 58,124 | 7 | 7.18 | 11 | 20 | 79 | 0.98% |
| 2026-10-09 | outside sessions | 129,827 | 7 | 6.98 | 10 | 20 | 85 | 0.78% |
| 2026-10-11/12 | london_morning | 45,049 | 7 | 6.89 | 10 | 19 | 77 | 0.84% |
| 2026-10-11/12 | ldn_ny_overlap | 49,489 | 6 | 6.73 | 10 | 17 | 68 | 0.54% |
| 2026-10-11/12 | outside sessions | 104,319 | 6 | 6.74 | 10 | 16 | 73 | 0.52% |

The minimum is 3 points everywhere. The typical spread is 6–7 points (0.06–0.07 USD/oz);
90% of ticks are at 11 points or less. `risk.max_spread_points` = 20 sits around the 99th
percentile: under 1% of ticks in any session exceed it.

**Limits.** About 38 market hours: 21 h on Friday 2026-10-09 and 17.5 h from Sunday's
reopen to Monday 15:30Z. Tick-weighted, so busy minutes
count more than quiet ones. No release-day sample. Not enough to re-derive
`max_spread_points` (Phase 3 asks for one month); no config was changed.

## Reproduce

`python scripts/collected_ticks_check.py` (launched as above) prints sections 3 and 4 with
every input's sha256. Two runs gave byte-identical output (sha256 `5ef1ddc8…`). Inputs:

- history file `fd54567d…`;
- live chunk digests: 2026-10-09 `66270168…` (113 chunks), 2026-10-11 `a3b0c7cf…`,
  2026-10-12 `c8f4748a…`.
