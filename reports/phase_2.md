# Phase 2 report: data foundation

- **Status:** in progress. This section was written and committed before any backfill start
  date was chosen.

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
  (≈6.3 months) does not.** See ADR-0007 (proposed, not implemented) for a separate,
  clearly labelled Dukascopy tick source.
