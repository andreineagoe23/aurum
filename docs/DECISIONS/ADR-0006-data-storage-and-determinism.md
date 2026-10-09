# ADR-0006: Immutable raw Parquet, deterministic derived data, one file per UTC day

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Phase 2 stores broker market data so that research can be replayed exactly. The master prompt
asks for immutable raw data partitioned by symbol and date, a separate normalised and canonical
layer, and an exit gate: re-running ingestion and aggregation must yield identical file hashes.

## Decision

1. **Layout** (`aurum.adapters.storage.layout`, documented in DATA_DICTIONARY.md): one Parquet
   file per symbol and **UTC** date for raw history ticks, raw broker bars per timeframe,
   normalized ticks and canonical bars. UTC dates, not server or London dates: nothing outside
   the MT5 adapter sees server time (ADR-0004), and every M1..H1 bar falls inside one UTC day.
2. **Raw is immutable.** Raw files are written with an atomic create (temp file + hard link), so
   an existing file is never replaced. The only exception: `--refetch-empty` may replace a day
   stored *empty* once the broker serves data for it. Every calendar day in a backfilled range
   gets a file, an empty one if the broker has nothing, so "fetched, nothing there" is
   distinguishable from "not fetched".
3. **Complete days only** in the history datasets. The current UTC day belongs to the live
   collector (`raw_ticks/mt5_live`, many small immutable chunks).
4. **Derived data is rebuilt, not edited.** `aurum data resample` rewrites normalized ticks and
   canonical bars atomically. They are pure functions of raw data.
5. **Determinism.** pyarrow writes with fixed options (zstd 9, dictionary on, statistics on,
   format 2.6, data page v1, 1M-row groups) and no time-varying metadata. Bar building sorts
   before grouping, selects first/last/min/max values, and computes means as integer sums over
   integer counts. So identical input gives byte-identical files. The hash manifest records the
   library versions, because a pyarrow upgrade may change bytes without changing values.
6. **Live collector resume point comes from disk.** The cursor (newest stored tick time plus the
   number of stored rows at that millisecond) is read from the chunk files, never from a separate
   state file. A crash between writing data and writing state therefore cannot cause duplicates
   or a skipped range.

## Consequences

- Re-running backfill is cheap and safe (existing days are skipped). Re-running resample is
  idempotent, which `aurum data hashes --compare` verifies.
- Raw history ticks are kept exactly as the broker served them on the day they were fetched.
  If the broker later thins its history (ADR-0007), our copy keeps whatever we fetched first.
- Live chunks are not reproducible byte for byte (their boundaries depend on flush timing). They
  are excluded from the hash manifest; data QA compares their content with history instead.
