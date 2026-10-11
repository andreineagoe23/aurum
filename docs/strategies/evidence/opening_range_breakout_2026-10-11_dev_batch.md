# opening_range_breakout 0.1.0: development batch inventory (2026-10-11)

Generated read-only by `scripts/opening_range_breakout_dev_batch.py` from the trial ledger
(opened read-only) and the existing run files. No simulation was run to produce it, and
**no trade, equity or P&L figure is read or reported**: analysis is not authorised yet.

## Batches and frozen contexts

| batch id | context hash | frozen at |
|---|---|---|
| `opening_range_breakout/m15/2020-12-15..2025-09-30` | `9e5eee1997580ea2d94573fa6dce4b03168eda29d1a9d0aec429ded367841f30` | 2026-10-11T08:39:12.718907+00:00 |
| `opening_range_breakout/m5/2020-12-15..2025-09-30` | `fd9370cbb7089543acb39be960c64f653fe6ef5b147c12f4fd30dc37f232d444` | 2026-10-11T08:47:21.630970+00:00 |

Full contexts: `opening_range_breakout_2026-10-11_dev_contexts.json`. Context keys that differ between the batches: ['variant'].

| context part | value |
|---|---|
| code: source sha256 | `70fe4b3ab2d4ff9e6781073361e9572df1f09f59072b37218c8a086eb4d01c3a` |
| code: environment sha256 | `f63558cc3a9b48d4f72b1d8054227a8f3d506c9e3504904c7df69869b8f0ce8b` (equal to the trend_pullback batches') |
| code: Python | 3.12.15 |
| spec | `docs/strategies/opening_range_breakout.md` `413033b6d9d8e878a8c7c99579f6d942fa929dbea8d589b9e9f77c74c23faba9` |
| config hash | `7e7512c9483ff2ecaab77c77659a5d6992c77fb25ae15994681a7a2e3d8d290c` |
| costs | {"bar_spread_extra_points": 2, "commission_per_lot_side": 2.25, "latency_ms": 200, "max_quote_wait_seconds": 60.0, "slippage_points": 5.0} |
| risk engine | policy |
| account | {"currency": "USD", "initial_balance": 10000.0, "leverage": 500} |
| symbol snapshot | `20261010T000046Z.json` file `0426cf8caefd2faa93b5b3b24004db656e2768e139122d2cd34e394b0be0c7ee` |
| input data | `c436de451d509edb1935936cea0930333eb1e2c354ccf00621cd5e0b905ef3c3`, 7045 files from 2020-12-05 |
| history bars: strategy / engine / loaded | {} / {'M15': 100} / {'M15': 100} |
| declared grid | `c9545c7a81364982202fd5faa95e5b19662707cc2354a770236177dae0146bc3` |
| dates / purpose / sealed opened | 2020-12-15..2025-09-30 / evaluation / False |

## Points, attempts, trials and outcomes

|  | m15 | m5 |
|---|---|---|
| grid points (declared) | 6 | 6 |
| attempts | 6 | 6 |
| succeeded / failed / unfinished | 6/0/0 | 6/0/0 |
| retries | 0 | 0 |
| trials | 6 | 6 |
| declared points without exactly one trial | 0 | 0 |
| published runs | 6 | 6 |
| first attempt started | 2026-10-11T08:39:26.611061+00:00 | 2026-10-11T08:47:34.822668+00:00 |
| last outcome | 2026-10-11T08:46:39.697766+00:00 | 2026-10-11T08:55:35.823170+00:00 |
| seconds per point: min / median / max | 56 / 61 / 62 | 64 / 70 / 74 |

- **12 grid points, 12 attempts, 12 trials**; ORB attempts outside the two batches: 0.
- Failed attempts: 0. Unfinished: 0. Retries: 0 (none requested: no `--retry-failed`).
- Recorded trials whose run is not published (artifacts pending): 0. Staging directory entries for this strategy: none.
- Grid logs (git-ignored), last lines:
  - `reports/grid_logs/orb_m15.log`: `6 points: 6 resolved, 0 unresolved, 0 with artifacts pending` / `exit 0`
  - `reports/grid_logs/orb_m5.log`: `6 points: 6 resolved, 0 unresolved, 0 with artifacts pending` / `exit 0`

## Execution

- Reviewed state `b5ea565`, clean worktree; m15 then m5, one process at a time, the
  approved commands without `--retry-failed`.
- `uv run` cannot start from the agent's process on this VM (os error 448, a junction in
  the venv's interpreter path). The commands ran as the venv's own interpreter build
  (`cpython-3.12.15`) with `-S` and only `C:\venvs\aurum\Lib\site-packages` on the path,
  then `python -m aurum.cli backtest grid ...`: nothing synced or installed. The recorded
  environment sha256 equals the one the trend_pullback batches recorded under
  `uv run --no-sync`; without `-S` it would not have.
- The source sha256 hashes this checkout's working-tree bytes. Git content equals `b5ea565`;
  two source files (`backtest/parity.py`, `strategies/opening_range_breakout.py`) have CRLF
  line endings in this checkout (`core.autocrlf`), so a fresh LF checkout hashes differently.
- The live tick collector was not running (last heartbeat 2026-10-10 16:42Z); no input
  file of the period can change, and it was not started or touched.

## Range-date status coverage

Expected: every one of the 1,237 trading dates has both ranges, so 2,474 range-dates per
trial, with exactly the statuses and counters of the feasibility CSV for the trial's variant
and `or_bars` (the same rules; `target_r` changes no status). Observed: the strategy's
diagnostics in each run's `summary.json`. These are strategy statuses before the risk engine.

| variant | or_bars | target_r | range-dates | signal | stop_below_minimum | stop_above_cap | no_break | data_gap | range_missing | no_decision_time | Σ data_gap | Σ blackout_skipped | = feasibility |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| m15 | 2 | 1.0 | 2474 | 1383 | 637 | 48 | 406 | 0 | 0 | 0 | 0 | 0 | yes |
| m15 | 2 | 1.5 | 2474 | 1383 | 637 | 48 | 406 | 0 | 0 | 0 | 0 | 0 | yes |
| m15 | 2 | 2.0 | 2474 | 1383 | 637 | 48 | 406 | 0 | 0 | 0 | 0 | 0 | yes |
| m15 | 4 | 1.0 | 2474 | 1086 | 309 | 85 | 994 | 0 | 0 | 0 | 0 | 0 | yes |
| m15 | 4 | 1.5 | 2474 | 1086 | 309 | 85 | 994 | 0 | 0 | 0 | 0 | 0 | yes |
| m15 | 4 | 2.0 | 2474 | 1086 | 309 | 85 | 994 | 0 | 0 | 0 | 0 | 0 | yes |
| m5 | 2 | 1.0 | 2474 | 1379 | 760 | 50 | 285 | 0 | 0 | 0 | 0 | 725 | yes |
| m5 | 2 | 1.5 | 2474 | 1379 | 760 | 50 | 285 | 0 | 0 | 0 | 0 | 725 | yes |
| m5 | 2 | 2.0 | 2474 | 1379 | 760 | 50 | 285 | 0 | 0 | 0 | 0 | 725 | yes |
| m5 | 4 | 1.0 | 2474 | 1265 | 404 | 77 | 728 | 0 | 0 | 0 | 0 | 0 | yes |
| m5 | 4 | 1.5 | 2474 | 1265 | 404 | 77 | 728 | 0 | 0 | 0 | 0 | 0 | yes |
| m5 | 4 | 2.0 | 2474 | 1265 | 404 | 77 | 728 | 0 | 0 | 0 | 0 | 0 | yes |

Trials whose coverage differs from the feasibility CSV: 0.

## Ledger verification

- `PRAGMA integrity_check`: ok.
- Every trial row re-hashed against its `record_hash` (read-only): 0 mismatches of 76.
- Ledger tables: {'trials': 76, 'attempts': 66, 'attempt_outcomes': 66, 'grid_batches': 4, 'sealed_openings': 0, 'parity_accesses': 0}.
- Sealed openings: 0 in all, 0 by opening_range_breakout. Parity accesses: 0 in all, 0 by opening_range_breakout.
- opening_range_breakout trials: 12, latest `data_end` 2025-09-30T23:00:00+00:00 (not after London midnight of 2025-10-01; no bar dated 2025-10-01 or later is read).

## Artifact verification

For each trial: the three Parquet outputs re-hashed and compared with the hashes recorded in
the trial and in the manifest; manifest trial id, attempt id, data hash and context hash
compared with the ledger and the frozen batch; outcome = succeeded with this trial; grid id.
Problems: 0.

Index of all 12 trials (variant, point, attempt, trial, run, context, timings,
output and record hashes): `opening_range_breakout_2026-10-11_dev_index.csv`. Run files: `reports/runs/<run_id>/` (git-ignored).

## Backups

| backup (C:\aurum-data\ledger\backups) | sha256 | taken |
|---|---|---|
| trials_20261011T0838Z_before_opening_range_breakout_dev_grid.sqlite | `1a140ad38ffea3fa82a7f0614ffb21cdbcc86efbb511ec935d9caf537806e694` | 2026-10-11 08:38Z, before any ORB attempt (64 trials, 54 attempts) |
| trials_20261011T0855Z_after_opening_range_breakout_dev_grid.sqlite | `f30f9870a8e43eae2681e9be047d012efe938047a15430ce5edc02540895a8b6` | after both batches (76 trials, 66 attempts) |

Both taken with the SQLite backup API (consistent copy); `integrity_check` ok. The pre-run
backup's sha256 equals the post-run backup of the trend_pullback batches (2026-10-10 16:00Z):
the ledger did not change between the two batches.
