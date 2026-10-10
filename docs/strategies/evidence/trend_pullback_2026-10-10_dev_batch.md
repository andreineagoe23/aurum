# trend_pullback 0.2.0 + A1: development batch evidence (2026-10-10)

Generated read-only by `scripts/trend_pullback_dev_analysis.py` from the trial ledger and
the existing run files. No simulation was run to produce it.

## Batches and frozen contexts

| batch id | context hash | frozen at |
|---|---|---|
| `trend_pullback/m15/2020-12-15..2025-09-30` | `2c9968b70f65f8dcb291c4d1ab6b6f0c0185d3d6c45d069bacf813192fb40b63` | 2026-10-10T14:41:47.816879+00:00 |
| `trend_pullback/m5/2020-12-15..2025-09-30` | `51552400ee4a359e349c5090856b4146807ba6473a4e5d72adbdf1a044947ad8` | 2026-10-10T15:09:12.437573+00:00 |

Full contexts: `trend_pullback_2026-10-10_dev_contexts.json`. Both contexts are identical except `variant`:

| context part | value |
|---|---|
| code: source sha256 | `d724e5149010dd4ef9befa74d812218dbd7131174ab27ab0cdc597de40ec5f9e` |
| code: environment sha256 | `f63558cc3a9b48d4f72b1d8054227a8f3d506c9e3504904c7df69869b8f0ce8b` |
| code: Python | 3.12.15 |
| spec | `docs/strategies/trend_pullback.md` `23ce32d10d1311da39baa75f70893bea2ece4ba8f5d5da9fce9287f62cfa0331` |
| config hash | `5ffc264f71a2f67d30db456188e5d6a336310fc2d7cabae70121192136164b6b` |
| symbol snapshot | `20261010T000046Z.json` file `0426cf8caefd2faa93b5b3b24004db656e2768e139122d2cd34e394b0be0c7ee` |
| input data | `5de288f8f47e5627429f9699d1cf6e55ba4a116b392100ad1b95e0dcc6b4bf05`, 7137 files from 2020-11-12 (warm-up included) |
| declared grid | `770cf9222525c8b64c3b61840426fab9913328061cb5401d7f2c7c23c709bb87` |
| dates / purpose / sealed opened | 2020-12-15..2025-09-30 / evaluation / False |

Context keys that differ between the batches: ['variant'].

## Counts and timings

|  | m15 | m5 |
|---|---|---|
| grid points (declared) | 27 | 27 |
| attempts | 27 | 27 |
| succeeded / failed / unfinished | 27/0/0 | 27/0/0 |
| retries | 0 | 0 |
| trials (one per point) | 27 | 27 |
| distinct points with a trial | 27 | 27 |
| first attempt started | 2026-10-10T14:41:58.658020+00:00 | 2026-10-10T15:09:23.867036+00:00 |
| last outcome | 2026-10-10T15:08:46.312690+00:00 | 2026-10-10T15:43:36.804316+00:00 |
| seconds per point: min / median / max | 44 / 47 / 59 | 61 / 65 / 71 |

- **54 grid points, 54 attempts, 54 trials**; attempts outside the two batches: 0.
- Ledger tables: {'trials': 64, 'attempts': 54, 'attempt_outcomes': 54, 'grid_batches': 2, 'sealed_openings': 0, 'parity_accesses': 0}.
- Grid logs (git-ignored): `reports/grid_logs/m15_a1.log`, `reports/grid_logs/m5_a1.log`
  (both end `27 points: 27 resolved, 0 unresolved, 0 with artifacts pending`, exit 0).

## Ledger verification

- `PRAGMA integrity_check`: ok.
- Every trial row re-hashed against its `record_hash` (read-only): 0 mismatches of 64.
- Sealed openings: 0. Parity accesses: 0.
- trend_pullback trials: 54, latest `data_end` 2025-09-30T23:00:00+00:00 (London midnight
  of 2025-10-01 = 23:00 UTC on 2025-09-30; no bar dated 2025-10-01 or later is read).
- Trials of any strategy whose `data_end` is after 2025-10-01 00:00 UTC: 10
  (`tr_20261009T221629964444_3c472c4b` reference_fixed_time 2026-09-13..2026-10-08, `tr_20261009T221818318925_c0ca0f25` reference_fixed_time 2020-11-12..2026-10-08, `tr_20261009T221917546708_32c86b28` reference_fixed_time 2020-11-12..2026-10-08, `tr_20261009T222312946438_ded3b3af` reference_fixed_time 2026-09-13..2026-10-08, `tr_20261009T231143063137_524c5051` reference_fixed_time 2026-09-13..2026-10-08, `tr_20261009T231320639210_7398d837` reference_fixed_time 2020-11-12..2026-10-08, `tr_20261009T232942016281_599861b8` reference_fixed_time 2026-09-13..2026-10-08, `tr_20261009T233213662768_daa59b30` reference_fixed_time 2020-11-12..2026-10-08, `tr_20261009T233343400292_605b5945` reference_fixed_time 2020-11-12..2026-10-08, `tr_20261010T002444363991_d57c1e14` reference_fixed_time 2020-11-12..2026-10-08).
  These are `reference_fixed_time` runs recorded on 2026-10-09/10 before the sealed period
  was defined (D1); none is a trend_pullback trial.

## Artifact verification

For each of the 54 trials: the three Parquet outputs re-hashed and compared with the hashes
recorded in the trial and in the manifest; manifest trial id, attempt id, data hash and
context hash compared with the ledger; outcome = succeeded with this trial.
Problems: 0.
Staging directory: [].

Index of all 54 trials (variant, point, attempt, trial, run, context, timings, output and
record hashes): `trend_pullback_2026-10-10_dev_index.csv`. Run files: `reports/runs/<run_id>/` (git-ignored).

## Backups

| backup (C:\aurum-data\ledger\backups) | sha256 | taken |
|---|---|---|
| trials_20261010T1422Z_before_trend_pullback_dev_grid.sqlite | `0c0171cac0e284aaadf7698157be0c03dda511d6d319c31f5bbcb810e6329076` | 2026-10-10 14:22Z, before any trend_pullback attempt (10 trials) |
| trials_20261010T1600Z_after_trend_pullback_dev_grid.sqlite | `1a140ad38ffea3fa82a7f0614ffb21cdbcc86efbb511ec935d9caf537806e694` | 2026-10-10 16:00Z, after both batches (64 trials, 54 attempts) |

Both taken with the SQLite backup API (consistent copy); `integrity_check` ok. The pre-run
backup is unchanged.
