# Phase 2 report: data foundation

- **Date:** 2026-10-09
- **Exit gate:** **PASSED** (§8). Phase 3 has not started.
- Section 1 was written and committed (cc75c1d) before any backfill start date was chosen.

## 1. Measured history depth (FusionMarkets-Demo, account 504691, 2026-10-09 ~08:10 UTC)

Terminal build 6249, `terminal_info().maxbars = 100000000` (no terminal-side bar cap). All
times below are UTC after the ADR-0004 conversion.

### Bars

| Timeframe | Bars returned | First bar | Continuous since | Bars/year once continuous |
|---|---|---|---|---|
| M1 | 2,101,945 | 2010-06-14 21:00 | **2020-11-12** | ~354,000 |
| M5 | 428,034 | 2010-06-14 21:00 | **2020-11-12** | ~70,900 |
| M15 | 148,764 | 2010-06-14 21:00 | **2020-11-12** | ~23,600 |
| H1 | 44,026 | 2010-06-14 21:00 | **2020-11-12** | ~5,910 |

- 2010-06 to 2019-09: exactly one bar per trading day on *every* timeframe (≈258/year). These
  are placeholders, not history.
- 2019-09-25 to 2020-11-11: about 23 bars per day on M1/M5/M15 (one per hour), so effectively
  H1 resolution only.
- From **2020-11-12** M1 is complete: 26,000–31,800 bars per month, every month through
  2026-10. That is **≈5.9 years** of minute data.

### Ticks

- First tick: **2026-03-29 22:00:02** (the Sunday open). Every trading day after that has
  250,000–376,000 ticks (`COPY_TICKS_ALL`). 2026-04-03 (Good Friday) has none.
- Before 2026-03-29: **zero ticks** for every day probed (2015-01 to 2026-03-27). That is
  **≈6.3 months** of tick history.
- It's unknown whether this is a rolling window (it moves forward over time) or a fixed start.
  Either way, ticks must be backfilled now and then collected continuously, or they may be
  lost. Re-measure in a few weeks to find out.

### Attempts to force a deeper download

| Attempt | Result |
|---|---|
| `copy_rates_from_pos(…, 0, N)` for N = 10k, 100k, 1M, 5M | Returns everything up to the 2010 placeholders, then stops (M1: 2,101,945 for N = 5M) |
| `copy_rates_range(2000-01-01 → now)` | Same bars as above |
| `copy_ticks_range` one-hour windows on Wednesdays, monthly back to 2025-01, **6 tries, 3 s apart** each | 0 rows before 2026-03-29 every time. The first calls into old dates took 5–10 s, so the terminal does ask the server, but it gets nothing back |
| `copy_ticks_from(2015-01-01 / 2025-01-01 / 2026-03-01, count=1000)` | Always starts at 2026-03-29 22:00:02 |

Conclusion: the depth limit is the broker server's, not the terminal's. A manual request from
the MT5 GUI (Symbols → Ticks → Request) is unlikely to do better, but the owner can try it.
The Python API cannot trigger it.

### Choice of backfill start (made after the measurements above)

- **Bars (M1/M5/M15/H1): from 2020-11-12**, the first day of continuous M1. Earlier
  placeholder bars are not research data and are not backfilled.
- **Ticks: from 2026-03-29**, the earliest tick the server has.
- Minute history (≈5.9 years) meets the ~2–3 year research threshold. **Tick history
  (≈6.3 months) does not.** See ADR-0008 (proposed, not implemented) for a separate,
  clearly labelled Dukascopy tick source.

## 2. Account change and commission

- New demo account **504691**, **USD**, server `FusionMarkets-Demo` (unchanged, so ADR-0004
  still applies). `configs/broker.yaml` now says `account_currency: USD`; connecting to an
  account in any other currency is refused (unit-tested).
- Read-only integration tests against the new account: **9 passed** (the M5 bar-boundary test
  skipped once at a bar boundary and passed on re-run). Server clock **VERIFIED**: measured
  offset +10799.7…+10799.9 s against expected +10800 s (UTC+3). The daily-break test (break at
  21:00 UTC in both US/EU DST-mismatch weeks) passes, so ADR-0004 holds.
- **Commission: not confirmed.** The round trip in the account history is **0.01 lot**, not
  1.00 lot: deal 403003840 (IN, BUY 0.01 @ 4194.08) and deal 403008958 (OUT, SELL 0.01 @
  4195.65), each with commission **−$0.02**. $2.25/lot/side × 0.01 = $0.0225, which the
  broker rounds to $0.02. Any rate from about $1.50 to $2.49 per side would book the same −$0.02.
  `broker.yaml` keeps **2.25** (the advertised value). No ADR, because nothing measured differs
  from it. **Action for the owner:** a 1.00-lot round trip would book the exact per-side amount.

## 3. What was built

| Area | Contents |
|---|---|
| `configs/data.yaml` + `settings.py` | Market hours (measured), the holiday calendar (17 closed dates, 50 early closes, each with a reason), backfill, collector and QA settings, `qa.known_gaps`. All validated; unknown keys are errors. |
| `aurum data collect` (`apps/collector.py`) | Live tick collector. Repeatedly fetches `copy_ticks_range(cursor → now)` into immutable chunks in `raw_ticks/mt5_live`. Resume point derived from the files on disk (newest tick + rows at that ms), so there are no duplicates after a crash. Heartbeat file + log every minute. Reconnect with exponential backoff (1 → 300 s). Settles 5 s after connecting so the terminal can sync history. Detects VM sleep or freeze (`pause`), records collector downtime (`outage`) and every gap > 60 s of expected trading (`gap`) in `events.jsonl`. Graceful stop on Ctrl+C / SIGTERM / SIGBREAK / a `STOP` file (final poll + flush). Spec snapshot on connect and daily, only if the contract changed. JSON logs to a file. **Running on the VM since 08:48 UTC** (PID 6396). |
| `aurum data backfill` (`data/backfill.py`) | Complete UTC days only. Ticks one day per request, bars in 31-day windows split per day. One immutable file per calendar day (empty files included). Resumable. Weekdays that come back empty are retried with backoff; `--refetch-empty` replaces only empty days. Run report in `quality_reports/backfill/`. |
| Storage (`adapters/storage/`) | Layout under `AURUM_DATA_DIR`: `raw_ticks/`, `raw_broker_bars/`, `normalized_ticks/`, `canonical_bars/{M1,M5,M15,H1}/`, `features/`, `quality_reports/`. Atomic, deterministic pyarrow writes; create-only for raw (ADR-0006). |
| Normalisation (`data/normalize.py`) | Drops invalid rows, counts out-of-order rows and sorts stably, drops consecutive exact duplicates, adds integer-point spread, flags crossed and outlier spreads (>5× the day median). |
| Resampling (`data/resample.py`, `pipeline.py`) | Ticks → M1 → M5/M15/H1 (bid OHLC, `tick_count` with MT5 semantics, `quote_count`, spread open/min/max/mean/close). Broker M1 fallback. Every day compared with the broker's bars at all four timeframes. Tick fidelity rule and per-day manifest (ADR-0007). |
| `aurum data qa` (`data/qa.py`) | Expected-minute grid per trading date versus canonical M1: missing days, gaps in trading sessions (gate) and elsewhere in market hours, bars outside market hours (daily break / weekend / closed dates), stale quotes by session and fidelity, crossed and outlier counts, live vs history, collector journal. `--strict` exits 1 on anything unexplained. |
| `aurum data spreads` (`data/spreads.py`) | DuckDB over `normalized_ticks`: spread per quote by session, by London hour and at every M5 decision instant; news-like spikes; share of decisions blocked by candidate caps; a proposal for `max_spread_points`. |
| `aurum data hashes` / `aurum data depth` | SHA-256 manifest and comparison (exit gate); a reproducible depth probe with a re-fetch check of stored days. |
| Spec snapshots | `specs/XAUUSD/<stamp>.json`, written when contract fields (anything except quotes and session statistics) change, or once per UTC day. |
| Platform | polars needs its SSE4.2 "compat" runtime under x64 emulation on Windows ARM (the default build dies with an illegal instruction). Selected in `aurum/_platform.py`. |
| Dependencies | polars (resampling), pyarrow (deterministic Parquet), duckdb (SQL over partitions), numpy (MT5 arrays). Reasons are in `pyproject.toml`. |
| Docs | `DATA_DICTIONARY.md` (every dataset and column), ADR-0006 (storage/determinism), ADR-0007 (tick fidelity), ADR-0008 (Dukascopy, **proposed**), VM_SETUP §13 (running the collector, scheduled tasks), `scripts/daily_data.ps1`, CLAUDE.md. |

## 4. Data on disk (C:\aurum-data, 2026-10-09)

| Dataset | Content |
|---|---|
| raw ticks (history) | 194 UTC days, 2026-03-29 … 2026-10-08, **39,913,871 ticks**, 241 MB |
| raw broker bars | 2,157 days per timeframe from 2020-11-12: M1 2,092,038 · M5 418,782 · M15 139,621 · H1 34,925 bars, 91 MB |
| canonical bars | 2,157 days × 4 timeframes, **all identical to the broker's bars** (0 OHLC differences, 0 missing or extra bars) |
| tick fidelity | **exact: 90 days (2026-06-26 … 2026-10-08)**, incomplete: 76 days (2026-03-29 … 2026-06-25) |
| live ticks | collector running; ~100k ticks so far today, 0 gaps recorded |

### Finding: older tick history is thinned (ADR-0007)

Ticks from 2026-06-26 onward rebuild the broker's M1 bars exactly. Before that, 5.3% of minutes
differ (up to $19.72), the broker's `tick_volume` exceeds the ticks it now serves (e.g. 471 vs
280), and 340 broker minutes have no ticks at all. So the server's stored tick history for those
months is a thinned subset of the feed its bars were built from. For those days the canonical
bars come from the broker's M1; normalized ticks are kept but labelled `incomplete`. Ticks only
stay at full fidelity if they are captured while fresh, which the collector and the daily
backfill now do.

Also measured: the broker bar `spread` field is the bar's **minimum** spread, and `tick_volume`
counts bid-changing ticks.

## 5. Spread statistics (exact days 2026-06-26 … 2026-10-08, points of $0.01)

| Measure | Session | n | median | p90 | p95 | p99 | p99.9 | max |
|---|---|---|---|---|---|---|---|---|
| per quote | london_morning | 3,613,744 | 7 | 12 | 13 | 20 | 42 | 500 |
| per quote | ldn_ny_overlap | 4,778,400 | 7 | 12 | 14 | 24 | 76 | 624 |
| per quote | all market hours | 21,014,185 | 7 | 12 | 13 | 22 | 52 | 797 |
| at M5 close | london_morning | 3,600 | 6 | 11 | 12 | 16 | 30 | 84 |
| at M5 close | ldn_ny_overlap | 3,150 | 6 | 11 | 13 | 22 | 323 | 500 |
| at M5 close | both sessions | 6,750 | 6 | 11 | 12 | 19 | 89 | 500 |

Share of M5 decision instants a cap would block: 15 → 1.76%, **20 → 0.76%**, 25 → 0.46%,
30 → 0.27%, 50 → 0.16%.

News-like spikes (minute mean spread > 3× session median): 68 minutes, concentrated at
13:29–13:30 and 15:00 London (the 08:30 and 10:00 New York US data releases). Spreads there reach
500–624 points; 500 appears repeatedly, probably a broker-side widened quote.

For comparison, the thinned period (2026-03-29 … 06-25) shows wider spreads: median 8, decision
p99 26 (it would propose 30). That could be the thinning or a more volatile spring; it cannot be
told apart yet.

**Proposal: `risk.max_spread_points: 20`** (p99 of decision-time spreads, rounded up to 5). It
blocks the news spikes and about 0.8% of normal decisions. **Not written to `risk.yaml`**:
`max_spread_points` stays `null`, so DEMO stays disabled, as asked. Re-check after a few more
weeks of exact data. The ATR-relative cap (`max_spread_atr_fraction: 0.15`) is the second line
of defence.

## 6. Data QA (`aurum data qa --strict`, trading dates 2020-11-12 … 2026-10-08)

| Check | Result |
|---|---|
| Trading dates | 1,539 |
| Missing trading dates | **0** (17 closed dates, all US exchange holidays: Christmas, New Year, Good Friday) |
| Early closes | 50, all US holidays (MLK, Presidents', Memorial, Juneteenth, Independence, Labor, Thanksgiving + Friday, Christmas Eve). Close 13:00 New York up to 2021, 14:30 from 2022, 13:45 on Thanksgiving Friday / Christmas Eve |
| Gaps inside trading sessions | **0 unexplained**; 13 known source gaps listed in `qa.known_gaps` with reasons: 2025-11-28 London morning (sparse feed on the day of the CME Globex outage), 2026-02-11 13:34–13:38 UTC (no quotes right after the 08:30 New York release), and three isolated 1–2-minute gaps (2024-02-14, 2025-02-28, 2025-07-02, cause unknown). In each case the broker's own bars are missing too, and canonical bars miss nothing the broker has |
| Gaps elsewhere in market hours | 125, reported, not gating: mostly 1–5 minutes around the daily close and reopen and in Asian hours, plus a few longer feed gaps (e.g. 2025-08-27 23:25–01:49 New York, 2025-07-30 13:32–14:14 New York) |
| Bars outside market hours (daily break, weekends) | **0** |
| Stale quotes > 10 s in sessions (exact days) | london_morning 20 intervals (longest 35.6 s), overlap 1 (11.8 s) |
| Crossed / outlier-spread ticks | 0 / 133,638 (0.33%) |

## 7. Tests

`ruff check`, `ruff format --check`, `mypy --strict` (whole `src/`, 62 files): clean.
`uv run pytest`: **420 passed, 5 skipped** (3 macOS-only checks and the 2 gated order tests).
New: collector fault suite (13 tests: restarts, same-millisecond rows, IPC failure, terminal
restart with backoff, gaps vs daily break, VM sleep, crash leftovers, UTC midnight, STOP file),
backfill, storage determinism, normalisation and resampling, including hypothesis properties
(normalisation is idempotent; ticks → M1 → M5/M15/H1 equals ticks → M5/M15/H1 directly; every
tick lies inside its bar), market hours, QA, spreads, hashes, depth, the vectorised server-time
conversion (property-tested against the scalar one, including DST edges).
Read-only MT5 integration tests: 9 passed on the new account.

## 8. Exit gate

| Criterion | Result |
|---|---|
| Re-running aggregation gives identical file hashes | ✅ `aurum data resample` re-run in place: **17,617 / 17,617** files identical (raw 8,822, normalized 166, canonical 8,629) |
| Re-running ingestion + aggregation gives identical file hashes | ✅ Fresh `AURUM_DATA_DIR`: `mt5 symbols` → `backfill` → `resample` → `hashes --compare`: **17,617 / 17,617** files identical to the baseline, raw included (the server re-served identical ticks and bars ~10 minutes later) |
| No unexplained gaps inside trading sessions | ✅ `aurum data qa --strict`: 0 unexplained (every closure, early close and source gap is listed with a reason) |

**Exit gate: PASSED.** Phase 3 has not started and waits for owner sign-off.

## 9. Known issues and risks

1. **Commission rate unconfirmed** (§2): needs a 1.00-lot round trip.
2. **Tick depth ≈ 6.3 months, exact ticks ≈ 3.5 months.** Tick-level research (intra-bar
   SL/TP ordering) is limited to 90 exact days for now. ADR-0008 (Dukascopy) awaits a decision.
3. **Unknown whether the tick window is rolling.** Re-run `aurum data depth` in 2–4 weeks: if
   the first tick or the fidelity boundary moves, it is a rolling window.
4. **The collector is not yet a scheduled task.** It was started by hand and will not survive a
   reboot. See VM_SETUP §13 (Task Scheduler commands, plus `scripts/daily_data.ps1`).
5. **Live vs history comparison has no data yet** (the first full collected day is today, so it
   compares from tomorrow). Known by design: history tick flags carry bit 128 and live ones do
   not; the comparison uses (time, bid, ask).
6. Holiday calendar entries are measured for the past; **future holidays must be added before
   they happen** (next: 2026-11-26/27 Thanksgiving, 2026-12-24/25, 2027-01-01).
7. Hash reproducibility is per library version (pyarrow/polars upgrades may change bytes); the
   manifest records versions.
8. The test suite now takes ~40 s; most of it is the collector fault suite's simulated polling.

## 10. Next steps

- Owner decisions: commission round trip (1.00 lot); ADR-0008 (Dukascopy) yes/no;
  `max_spread_points` (proposed 20); register the collector and the daily task.
- Re-measure depth and fidelity (`aurum data depth`) in 2–4 weeks.
- Phase 3 (simulator) inputs: canonical bars (all days) and tick replay only on `exact` days.
  Without ticks, resolve intra-bar SL/TP worst case, flagged. Costs from Fusion data only.

### Notes for Phase 4 (do NOT implement now)

- The order manager must **reject OrderIntents older than ~60 seconds**. The idempotency
  history lookback is only 1 hour, so a stale intent replayed after a restart could reopen a
  closed trade.
- Ownership checks (reconciliation, force-flat, duplicate detection) must require
  **`magic == 7020261` as well as the `"AU"` comment prefix**, so manual trades are never
  treated as Aurum's.
- **Early-close days end before `force_flat_at`** (e.g. 14:30 New York = 19:30 London on US
  holidays). Force-flat and "no new entries" must use the earlier of the session cutoff and
  `MarketHours` close minus a margin.
- News-time spreads reach 500+ points at 08:30/10:00 New York; the spread check must run on
  the live quote at submission, not at signal time.
