# opening_range_breakout — strategy spec

> **Status: DRAFT 0.1.0 (revision 2), for final spec review. Not frozen, not approved for
> implementation.** The reviewer accepted decisions O1–O8 in principle on 2026-10-11 and asked
> for this revision. There is no strategy code, no backtest, no P&L and no ledger row for this
> family.
>
> - Written by Claude from `docs/STRATEGY_SPEC_TEMPLATE.md` at the reviewer's request ("draft
>   the specification only").
> - **Data used:** descriptive feasibility counts from development-period bars (trading dates
>   2020-12-15 … 2025-09-30), reproduced by `scripts/opening_range_breakout_feasibility.py`
>   (read-only). The script writes
>   `docs/strategies/evidence/opening_range_breakout_2026-10-11_feasibility.md` and its `.csv`.
>   Counts run up to the close that spends each range: **no price after that close was read**,
>   no fill, exit or P&L was computed, and no sealed-period file was opened.
> - **Written after the trend_pullback 0.2.0 results were seen** (rejected 2026-10-11). This
>   family is not a revision of trend_pullback. Section 18 lists every choice the counts
>   informed and states what the author knew.
> - On final approval the rules freeze; nothing changes after the first P&L-producing run. If
>   every trial fails, the result is "opening_range_breakout 0.1.0 failed".

## 0. Decisions (accepted in principle 2026-10-11, not frozen)

| # | Decision | Accepted choice | Data-informed? |
|---|---|---|---|
| O1 | Range anchors | London range at **08:00 London**, New York range at **09:30 New York**, both research anchors (section 5) | No. Fixed before any count; counts exist only for these anchors |
| O2 | Stop basis | **Opposite boundary of the range + a fixed 20 points** (section 8) | Yes: the range midpoint was compared and rejected as infeasible (evidence §3) |
| O3 | News blackout | A close at a blackout instant **neither signals nor spends** the range (rule 12.3) | Yes: both treatments were counted (evidence §4) |
| O4 | Stop bounds | Minimum **500 points**, cap **2,000 points** (ceiling 2,490) (section 8a) | No. Carried over from trend_pullback D2/D3; the counts were looked at but did not change them |
| O5 | Search grid | 2 variants × `or_bars` {2, 4} × `target_r` {1.0, 1.5, 2.0} = **12 trials** (section 14) | Partly: the exclusion of a 1-bar range cites the 2-bar widths (section 14) |
| O6 | DSR trial count | **N = 66** cumulative (54 trend_pullback + 12 here), V over those 66 trials (section 16) | No |
| O7 | Period, folds, gates, order | trend_pullback A1's evaluated period and folds, its gates adapted, evaluation stopping at the first mandatory failure (section 16) | No |
| O8 | H1 regime | **None** in 0.1.0 (section 4) | No |

## 1. Identity

- Name: `opening_range_breakout`
- Family: `opening_range_breakout`
- Variant: `m15` (enter on the M15 close outside the range) and `m5` (the same M15 range,
  entry on the M5 close outside it). These are two variants of one declared family search;
  only the decision timeframe differs (sections 6 and 7).
- Version: `0.1.0` (draft, revision 2)
- Author: Claude (draft) for Andrei Neagoe
- Date: 2026-10-11

## 2. Hypothesis

- **Economic rationale.**
  - Spot gold trades over the counter around the clock: it has no opening bell. Participation
    is still uneven through the day. It rises when a large group of participants starts its
    working day.
  - The spec uses two **research anchors** for those moments. Neither is a gold-market event:
    - **08:00 London**: the start of the London business day and of the London equity session
      (LSE). From then on, London-based dealers and their clients trade, the largest centre
      for spot gold.
    - **09:30 New York**: the US cash equity open (NYSE). US institutional and gold-ETF
      activity concentrates around it. It falls after the 08:30 New York data releases.
  - The hypothesis is that orders accumulated while those participants were absent are worked
    in the first 30–60 minutes after the anchor. That two-sided trading sets an opening range,
    with liquidity providers and short-term mean-reversion traders selling its top and buying
    its bottom.
  - A close outside the range means one side's flow exceeded the liquidity offered at the edge.
    Large orders are executed over hours (TWAP/VWAP), and the stops of traders who faded the
    edge sit beyond it. Both push further the same way.
  - The trade is on the side of the break.
- **Why it should persist after costs.**
  - Costs are about 16–21 points per round trip at 0.01 lot: the median decision-time spread of
    6–7 points (reports/phase_2.md §5), commission of 4.50 USD per lot (4.5 points), and 5
    points of slippage on the market entry and on a stop exit.
  - Stops are 500–2,000 points (section 8), so costs are about 1–4% of the risk (R). The idea
    needs an average gross result above about +0.04R per trade. This is a hypothesis, not an
    expected number.
- **Falsification.** Concatenated out-of-sample net expectancy ≤ 0 USD/trade, or ≤ 0 in 2 or
  more of the 4 out-of-sample folds (section 16). Either kills version 0.x.

## 3. Data, time, warm-up and history requirements

All bars are canonical bid bars, keyed by open time and visible at
`close_time = open_time + timeframe`. A bar is **completed at T** if `close_time ≤ T`. Nothing
with `close_time > T` is used at T.

| Timeframe | Fields used by the strategy | Bars the strategy reads at decision time T |
|---|---|---|
| H1 | none | none (section 4) |
| M15 | high, low (range); close (variant m15) | the `or_bars` range bars (section 5); variant m15: every M15 bar closing in `(range_end, T]` |
| M5 | close (variant m5 only) | every M5 bar closing in `(range_end, T]` |
| Ticks | bid, ask | none; execution only (simulator, broker) |

**Notation and conventions** (as in trend_pullback section 3):

- Rules are written for a **long**. A short mirrors every comparison (`>` ↔ `<`, high ↔ low,
  round down ↔ round up), except where section 8 writes the short out in full.
- All comparisons are exact float64 comparisons of unrounded values, with no tolerance.
  "Strictly" means equality fails.
- **Rounding to the price grid** (stop, target) removes float noise first:
  `q = round(x / point, 6)`, then `floor(q)` or `ceil(q)` as the rule says, × `point`.
  Distances in points are `round((a − b) / point)`, an integer.
- **Times.** London times are Europe/London wall clock and New York times are
  America/New_York wall clock, converted to UTC with `zoneinfo` (DST-aware); each anchor
  follows its own city's daylight time.
  - **Normal weeks**: 09:30 New York = 14:30 London.
  - **Mismatch weeks**: when US and UK daylight time differ, 09:30 New York = 13:30 London.
    That is 79 of the 1,237 trading dates in the evaluated period (evidence §5).
- **Searched parameters** appear by name: `or_bars` (default 2, grid {2, 4}) and `target_r`
  (default 1.5, grid {1.0, 1.5, 2.0}). The worked example (section 17) uses the defaults.

**Indicators used by the strategy:** none.

**Strategy warm-up: none.** Every value a decision reads comes from bars of the same day,
after the range anchor. The strategy declares `warmup_bars = {}`.

**Risk-engine history requirement (separate from the strategy).** This is an input of the
unchanged risk engine, not a strategy rule.

- The spread gate `spread ≤ max_spread_atr_fraction (0.15) × M15 ATR` uses Wilder's ATR
  (`risk.yaml` `atr_period` 14). It is computed over the newest **100 completed M15 bars**
  (`aurum.execution.engine.ATR_BARS`) at every decision.
- With fewer than 15 M15 bars the ATR does not exist and every entry is rejected ("M15 ATR
  unavailable"). With 15–99 bars its value depends on how much history happens to be loaded.
- **Requirement:** every run loads at least **100 completed M15 bars before its first decision
  instant**, counted by bars, not calendar days. Later decisions inherit the history of the run.
- **Today this is met only incidentally.** With `warmup_bars = {}` the loader falls back to
  `backtest.warmup_days` (10 calendar days, 2020-12-05 … 12-14 for this period). That is
  several hundred M15 bars, but it is a calendar rule.
- **Implementation requirement, when authorised:** the pipeline must declare the engine's
  M15 need (100 bars) by bar count, apart from the strategy's `warmup_bars`, with a test.
  Trend_pullback never exposed this, because its own 509-bar M15 warm-up covered it.
- These 100 bars are never strategy inputs, and no decision, signal or metric is produced
  from them.

**Evaluated period start.** The period starts on 2020-12-15, as trend_pullback A1 did (O7). The
reason is comparability, not a warm-up need.

**Stateless.** Every rule below is a function of the bars and the calendar at T. Nothing is
stored, and a restart re-derives the same state, including whether a range is spent.

## 4. H1 context (regime rule)

- **None in 0.1.0 (O8).** The hypothesis is about participation after two anchors, and the
  break sets the direction. Adding a trend filter would test two hypotheses at once, and the
  development evaluation could not separate them.
- **This is a scoping choice, not evidence against H1 filters.** The rejection of
  trend_pullback 0.2.0 was a verdict on that family's whole rule set. It says nothing about any
  of its individual components (the H1 regime, the M15 pullback or the M5 trigger) on its own.
- The "no high-impact news" part of the master prompt's regime row is handled by rule 12.3 and
  by the risk engine's blackout, unchanged.
- Volatility is bounded only through the stop rules in section 8.

## 5. M15 setup: the opening range

Two ranges per trading date:

| Range | Anchor A (local) | Session (`sessions.yaml`) | A in UTC |
|---|---|---|---|
| `london` | 08:00 Europe/London | `london_morning` (07:00–11:00 London) | 08:00Z (UK winter), 07:00Z (UK summer) |
| `newyork` | 09:30 America/New_York | `ldn_ny_overlap` (13:00–16:30 London) | 14:30Z (US winter), 13:30Z (US summer) |

- **The anchors are research anchors (O1), fixed by convention** (section 2), not opening
  bells of the gold market. Both lie inside their session in normal and mismatch weeks.
- **Range bars.** The `or_bars` M15 bars with `open_time` = A, A + 15 min, …,
  A + 15 × (`or_bars` − 1) min. `or_bars` 2 is a 30-minute range and 4 a 60-minute range.
- **`range_end`** = A + 15 × `or_bars` min, the close of the last range bar.
- **The range exists** only if all `or_bars` bars exist. Otherwise every eligible decision time
  of its window counts one `data_gap` (section 6), and there is no signal that day.
- **`H`** = max high of the range bars; **`L`** = min low; **`W`** = `round((H − L) / point)`
  points. `W` is reported, not filtered.
- The range is fixed at `range_end` and never moves afterwards.

## 6. Decision times, breakout, M5 trigger and diagnostic counting

**Entry window.** For each range, the instants T with `range_end < T ≤ range_end + 120 min`.

**Eligible decision time.** T is eligible for a range when all of these hold:

1. T is a bar close of the variant's decision timeframe: M15 for `m15`, M5 for `m5`.
2. T is in the entry window.
3. T is a decision time of the range's session. At T, `TradingCalendar.state_at(T)` has
   `session` equal to that range's session and `entries_allowed` true. That means
   `session start ≤ T < session end`, early closes applied; a bar closing exactly at 11:00 or
   16:30 London is not a decision time.
4. T is not inside a news blackout window (rule 12.3).

**Eligible decision times, all trading dates of the evaluated period** (calendar only; evidence
§5). The first and last are local times. The timeout is the latest `max_hold` deadline, that
of a signal at the last time.

| Range, `or_bars` | Weeks | m15: first … last (count) | m5: first … last (count) | Latest timeout (London / New York) |
|---|---|---|---|---|
| `london`, 2 | all | 08:45 … 10:30 London (8) | 08:35 … 10:30 London (24) | 12:30 / 07:30, or 08:30 in mismatch weeks |
| `london`, 4 | all | 09:15 … 10:45 London (7) | 09:05 … 10:55 London (23) | m15 12:45, m5 **12:55** London / m5 07:55, or 08:55 in mismatch weeks |
| `newyork`, 2 | normal | 10:15 … 11:15 New York (5) | 10:15 … 11:25 New York (15; 10:05 and 10:10 skipped) | m15 18:15 / 13:15, m5 18:25 / 13:25 |
| `newyork`, 2 | mismatch | 10:15 … 12:00 New York (8) | 10:15 … 12:00 New York (22; 2 skipped) | 18:00 / **14:00** |
| `newyork`, 4 | normal | 10:45 … 11:15 New York (3) | 10:35 … 11:25 New York (11) | m15 18:15 / 13:15, m5 18:25 / 13:25 |
| `newyork`, 4 | mismatch | 10:45 … 12:15 New York (7) | 10:35 … 12:25 New York (23) | m15 18:15 / 14:15, m5 **18:25 / 14:25** |

- The latest decision of any variant is 10:55 London (`london`, 4, m5) and 12:25 New York
  (`newyork`, 4, m5, mismatch weeks), or 11:25 New York in normal weeks.
- No early close in the period clipped a window.

**Read set at an eligible T:** the range bars, plus every bar of the decision timeframe with
`close_time` in `(range_end, T]`. This includes the bars closing at instants that are not
eligible (blackout instants).

**Breakout (long)** at an eligible T, with `c(T)` the close of the decision-timeframe bar
closing at T:

1. the read set is complete;
2. `c(T) > H` strictly; and
3. **first close outside:** for every earlier eligible decision time T′ of the same range and
   date, `L ≤ c(T′) ≤ H` (equality counts as inside).

A short mirrors this: `c(T) < L` strictly.

**One use per range per date.**

- The range is **spent** at the first eligible decision time with a complete read set whose
  close is outside `[L, H]`. This holds whether that close produces a signal (section 8
  bounds) and whether the risk engine approves it.
- After that, the range gives no signal that date.
- A close outside the range at an instant that is not eligible (rule 6.3 or 6.4) neither
  signals nor spends the range.

**Variant m15:** decision times are M15 closes; `c(T)` = `close(b0)`, the M15 bar closing at T.

**Variant m5:** the setup is the same M15 range. Decision times are M5 closes; `c(T)` =
`close(m0)`, the M5 bar closing at T. The M5 trigger needs nothing else: no previous-bar
condition and no M15 close after the range.

**Diagnostic counters** (exact; counted per variant run, summed over all range-dates, each
instant counted once however often the engine calls the strategy at it):

| Counter | +1 for each | Only while |
|---|---|---|
| `data_gap` | eligible decision time whose read set is incomplete. A missing range bar makes every eligible time of that window count. A missing bar after `range_end` makes every later eligible time count, because it stays in the read set; that range cannot be spent that date | the range is unspent |
| `blackout_skipped` | instant meeting rules 6.1–6.3 but not 6.4 whose bar exists and closes outside `[L, H]`, with the range existing and no read-set bar missing so far. Several per window are possible (10:05 and 10:10 New York) | the range is unspent |
| `stop_below_minimum` | range-date whose spending close gives `d < 500` (section 8) | at the spend |
| `stop_above_cap` | range-date whose spending close gives `d > 2,000` (section 8a) | at the spend |

- Instants that fail rule 6.3 are not counted anywhere.
- Signals emitted, and the risk engine's rejections by reason, come from the run's journal.
  These are not strategy counters.
- Each range-date ends in exactly one status: `signal`, `stop_below_minimum`,
  `stop_above_cap`, `no_break` (complete window, no close outside), `data_gap` (range exists,
  read set incomplete before any spend), `range_missing`, or `no_decision_time` (no eligible
  instant). The feasibility evidence uses these statuses.

## 7. Entry

- **Order type:** market. `Signal.time = T`, `data_as_of = T`, `Signal.entry_price = None`.
- **Variant m15:** at the M15 close T of a breakout (section 6).
- **Variant m5:** at the M5 close T of a breakout (section 6).
- **Fill:** as trend_pullback section 7, unchanged.
  - A market buy fills at the **ask** (a sell at the bid) of the first quote at or after
    `T + latency`, plus entry slippage (ADR-0009 §1–2).
  - On bar-only days, the next M1 open at or after arrival: bid = the open, ask = open + the
    bar's minimum spread + `bar_spread_extra_points` (ADR-0009 §4).
  - On demo, the broker fills. The fill is **never** the decision bar's close.
- **`ref`** = `c(T)` (bid). It is used only for the stop distance, the min/cap checks and the
  target. The risk engine sizes from the executable quote (ADR-0010).

## 8. Stop loss

**Buffer.** `stop_buffer_points` = **20 points = 0.20 USD/oz**, a fixed constant of this spec.

- It was chosen equal to today's `risk.yaml` `max_spread_points` (20), but it is **not linked**
  to it. A change to `risk.yaml` does not change the buffer, and changing the buffer is a
  version bump.
- **Why:** a short closes at the ask, while the range is measured on bid bars. The buffer keeps
  a short's stop from being hit by a spread of up to 20 points while the bid is still inside
  the range. It is applied to both sides so the rule is symmetric.

**Price rule (O2): the opposite boundary of the range, plus the buffer.**

| | Long (breakout above `H`) | Short (breakout below `L`) |
|---|---|---|
| Unrounded stop | `x = L − 20 × point` | `x = H + 20 × point` |
| Stop | `floor(round(x / point, 6)) × point` | `ceil(round(x / point, 6)) × point` |
| Distance `d` (points) | `round((ref − stop) / point)` | `round((stop − ref) / point)` |
| With `H`, `L` on the price grid | `stop = L − 0.20`; `d = round((ref − L) / point) + 20` | `stop = H + 0.20`; `d = round((H − ref) / point) + 20` |
| In terms of the range | `d = W + round((ref − H) / point) + 20`, with `ref > H` | `d = W + round((L − ref) / point) + 20`, with `ref < L` |

So `d > W + 20` always. A long that falls back through the whole range has disproved the
breakout; so has a short that rises back through it.

- **Minimum (O4):** `d ≥ max(500, trade_stops_level + 10)` (`trade_stops_level` is 0 today).
  Otherwise there is no signal, the range is spent, and `stop_below_minimum` counts one.
  - The reason is the same as trend_pullback D3: a lower minimum raises bar-resolution
    ambiguity (reports/phase_3.md §4). It does **not** guarantee that the Phase 3b
    flagged-trade share stays at or under 1%.
- **Always attached broker-side** at order submission. Never clamped, shortened or moved.

### 8a. Stop-size cap

The inputs are those of trend_pullback section 8a. They were re-read from the pinned snapshot,
so the ceiling is the same.

| Input | Value | Unit | Source |
|---|---|---|---|
| Reference equity | 10,000.00 | USD | `configs/backtest.yaml` `initial_balance` |
| Account currency | USD | | `configs/broker.yaml` `account_currency` |
| Risk per trade | 0.25 | % of equity | `configs/risk.yaml` `risk_per_trade_pct` |
| Risk budget | 25.00 | USD | 10,000 × 0.25 / 100 |
| Symbol point | 0.01 | price | spec snapshot `20261010T000046Z.json` (pinned in `backtest.yaml`, sha256 `0426cf8c…be0c7ee`) |
| `volume_min` | 0.01 | lots | same snapshot |
| `volume_step` | 0.01 | lots | same snapshot |
| Money per price unit per lot | 100.00 | USD per 1.00 price per lot | same snapshot: `tick_value` 1.0 ÷ `tick_size` 0.01 |
| Commission per lot | 2.25 per side / 4.50 round trip | USD | `configs/broker.yaml` |
| Stop slippage | 5 points = 0.05 price | | `configs/broker.yaml` `slippage.points_per_side` (provisional) |
| **Ceiling** | **2,490 points = 24.90 price** | | (25.00 / 0.01 − 4.50 − 0.05 × 100) / 100 = 24.905 → floor |
| **Strategy cap** (O4) | **2,000 points = 20.00 price** | | Leaves room for equity to fall about 20% before cap-sized stops become unaffordable |
| Risk at `volume_min`, stop at the cap | 0.01 × (20.00 × 100 + 4.50 + 0.05 × 100) = 20.095 ≤ 25.00 | USD | |
| Equity floor of the cap | 20.095 / 0.0025 = 8,038.00 | USD | Below it, the risk engine rejects cap-sized stops |

- **Stop above the cap → no signal.** If `d > 2,000`, there is no signal, the range is spent,
  and `stop_above_cap` counts one. The stop is never clamped, shortened or moved.
- `stop_below_minimum`, `stop_above_cap` and risk-engine rejections are separate counters.
- Changing the cap, the minimum, the buffer or any input above is a version bump.

**Feasibility counts** (evidence §1). The population is the 1,237 trading dates of the
evaluated period, one range-date per range. There were no `data_gap`, `range_missing` or
`no_decision_time` range-dates. "Spent" = the first eligible close outside the range within
the window. The risk engine has not been applied.

| Range, `or_bars`, variant | `W` p10 / p50 / p90 (points) | Spent | `d` p10 / p50 / p90 / p99 (points) | `d` < 500 | `d` > 2,000 | Signals (within bounds) |
|---|---|---|---|---|---|---|
| london, 2, m15 | 213 / 383 / 739 | 1,115 (90.1%) | 291 / 499 / 982 / 1,759 | 558 (50.0%) | 5 (0.4%) | 552 |
| london, 2, m5 | 213 / 383 / 739 | 1,153 (93.2%) | 272 / 458 / 866 / 1,601 | 662 (57.4%) | 5 (0.4%) | 486 |
| london, 4, m15 | 288 / 529 / 1,046 | 907 (73.3%) | 348 / 619 / 1,186 / 2,073 | 292 (32.2%) | 13 (1.4%) | 602 |
| london, 4, m5 | 288 / 529 / 1,046 | 1,011 (81.7%) | 339 / 584 / 1,107 / 2,073 | 379 (37.5%) | 12 (1.2%) | 620 |
| newyork, 2, m15 | 414 / 716 / 1,329 | 953 (77.0%) | 524 / 906 / 1,634 / 2,881 | 79 (8.3%) | 43 (4.5%) | 831 |
| newyork, 2, m5 | 414 / 716 / 1,329 | 1,036 (83.8%) | 510 / 860 / 1,590 / 2,758 | 98 (9.5%) | 45 (4.3%) | 893 |
| newyork, 4, m15 | 569 / 988 / 1,898 | 573 (46.3%) | 668 / 1,181 / 2,107 / 4,188 | 17 (3.0%) | 72 (12.6%) | 484 |
| newyork, 4, m5 | 569 / 988 / 1,898 | 735 (59.4%) | 656 / 1,066 / 1,940 / 3,913 | 25 (3.4%) | 65 (8.8%) | 645 |

- **Volatility drift** (evidence §2). Shares change by year, the same drift trend_pullback
  section 18 describes.
  - `london`, 2, m15: `d` < 500 for 45.6–71.1% of spent ranges in 2021–2024, 10.0% in 2025
    (Jan–Sep).
  - `newyork`, 4, m15: `d` > 2,000 for 5.9–14.2% in 2021–2024, 31.2% in 2025.
- **O2 evidence** (evidence §3). At the same spending closes, a midpoint stop (`H − W/2`, ± 20
  points) gives `d` < 500 for 81.9% (m15) and 89.9% (m5) of `london`, 2 ranges. The opposite
  boundary gives 50.0% and 57.4%.
- **O3 evidence, one population** (evidence §4). Only `newyork`, 2, m5 differs between the two
  treatments: its window holds the blackout instants 10:05 and 10:10 New York.

  | Treatment | Ranges spent | Of which |
  |---|---|---|
  | (a) O3, accepted: blackout closes neither signal nor spend | 1,036 | 468 (45.2%) spent at 10:15 New York or later, after a close outside at 10:05 or 10:10 that rule 12.3 skipped |
  | (b) alternative: blackout closes spend | 1,055 | 487 (46.2%) spent at 10:05 or 10:10 New York, by a signal the risk engine must reject |

  - The 19-range difference: under (b), 19 ranges with a close outside at 10:05 or 10:10 were
    back inside the range for the rest of the window. Under (a) they are `no_break`.
  - The earlier draft quoted "487 of 1,055" for (b) and "468 of 1,036" for (a) as if they
    were one population. They are the two treatments on the same 1,237 range-dates.
  - They came from scratch scripts that used weekdays (1,251, of which 14 market holidays
    had no range) instead of the market calendar. The committed script reproduces those
    counts exactly on the 1,237 trading dates.

## 9. Target

- Long: `x = ref + target_r × (ref − stop)`, `target = floor(round(x / point, 6)) × point`
  (toward the entry). Short: `x = ref − target_r × (stop − ref)`, `target = ceil(round(x /
  point, 6)) × point`. `target_r` defaults to 1.5 (grid {1.0, 1.5, 2.0}).
- It is set broker-side with the stop at submission. Because the fill is not at `ref`, the
  realised reward-to-risk is slightly below R. That is accepted and measured, not corrected.

## 10. Timeout (signal-to-exit)

- `max_hold = 120 minutes`, from `Signal.time` (the engine's existing rule, trend_pullback
  D6 b). The deadline is `Signal.time + 120 min`, rebuilt from the persisted signal after a
  restart, and never restarted. At expiry the engine closes at market (TIMEOUT), filled on the
  next quote after latency.
- **Why 120.** It equals the entry window: the hours after an anchor in which the hypothesis
  says accumulated orders are worked.
- **Latest deadlines** (section 6 table):
  - `london`: **12:55 London** (m5 signal at 10:55). That is before `ldn_ny_overlap` starts
    (13:00 London) and before the earliest `newyork` range (13:30 London, mismatch weeks), so
    a `london` position never meets a `newyork` signal.
  - `newyork`: **18:25 London** (m5 signal at 16:25 London). That is 13:25 New York in normal
    weeks and 14:25 New York in mismatch weeks. It is before force-flat at 20:00 London.
  - On an early close, force-flat at `market close − 15 min` comes first if it is earlier.
- Market orders only, so no fill-based timeout is needed (trend_pullback D6 note).

## 11. Invalidation and exit precedence

- **No invalidation rule in 0.1.0.** The exits are stop, target, timeout and force-flat. The
  stop sits beyond the far side of the range, so a failed breakout exits there. An earlier
  "close back inside the range" exit would be a second, untested rule.
- **Simultaneous or ambiguous exits:** delegated unchanged to the simulator (ADR-0009 §5) and,
  on demo, to the broker. The strategy adds no exit rule of its own.

## 12. Session constraints and signal priority

1. **Decision times:** section 6 rules 1–3. `london` uses `london_morning`; `newyork` uses
   `ldn_ny_overlap`.
2. **Risk engine gates, unchanged:** cutoff (no entries within 45 min of `force_flat_at`),
   news blackout, spread caps (`max_spread_points` 20, `max_spread_atr_fraction` 0.15 with the
   history requirement of section 3), `max_trades_per_day: 3`, loss budgets, the
   consecutive-loss cooldown, and one position at a time.
3. **News blackout in the strategy (O3).** An instant T with
   `aurum.sessions.news.NewsBlackout.reason_at(T)` not None is not an eligible decision time.
   - It is the same object, on the same `risk.yaml` `news_blackout` windows, that the risk
     engine uses: `[start, end)` New York time, plus the FOMC window on statement dates.
   - A close at such an instant neither signals nor spends the range. The strategy then never
     emits a signal that the risk engine must reject by construction.
   - It also makes m5 first evaluate the `newyork`, 2 range at 10:15 New York, the same
     instant as m15. 10:15 is eligible because the window end is exclusive.
   - Inside the windows of section 6 only 09:50–10:15 New York occurs (evidence §5: 2 skipped
     instants per date for `newyork`, 2, m5; none elsewhere).
4. **Force-flat** at `min(20:00 London, market close − 15 min)`. Open positions are **not**
   closed at a session end.
5. **Signal priority:**
   - Each variant runs as its own strategy, in its own backtest run and account.
   - Within a variant there is at most one signal per range per date, so at most two per date,
     and the `london` position is always gone before the `newyork` window opens (section 10).
   - `max_trades_per_day: 3` never binds. A signal while a position or pending order exists is
     rejected by the risk engine (exposure), with no queueing and no reversal.

## 13. Cost assumptions

- **Spread:** real bid/ask from ticks on exact days. On bar-only days, the bar's minimum spread
  + `bar_spread_extra_points` (2), per ADR-0009. Never a constant.
- **Commission:** 2.25 USD per side per lot (4.50 round trip), from `configs/broker.yaml`.
- **Slippage:** 5 points per side, from `configs/broker.yaml` (provisional).
- **Stress:** spread ×1.5 and ×2.0 re-runs are mandatory before any pass; both are reported
  (section 16, gate 5).

## 14. Parameters and search budget (O5)

| Name | Unit | Default | Justified range | Grid | Why |
|---|---|---|---|---|---|
| `or_bars` | M15 bars | 2 | 2–4 (30–60 min) | {2, 4} | The two conventional opening-range lengths. **1 bar is excluded partly on data:** the 2-bar `london` range already has `W` p50 383 points and 50% of its spending closes under the 500-point minimum (section 8a), so a 1-bar range would fall under it more often. No 1-bar count was made. More than 4 bars leaves under an hour of the `newyork` window in normal weeks |
| `target_r` | R | 1.5 | 1.0–2.0 | {1.0, 1.5, 2.0} | Must be reachable within 120 minutes; above 2R rarely is intraday |

- **Trials:** 2 variants × 2 `or_bars` × 3 `target_r` = **12**.
- **One trial** = one backtest run of one grid point of one variant over the evaluated trading
  period 2020-12-15 … 2025-09-30, recorded in the ledger. Fold statistics are slices of that
  run by entry date, not extra trials.
- **Not counted as trials:** the stress re-runs and the best-case intra-bar replay of a
  selected configuration. They are linked to their trial in the ledger.
- **Frozen:** no value, filter or alternate definition is added after results are seen. Any
  change is a new version and restarts the development evaluation, with the new trials added
  to the count.

Fixed (not searched; a change is a version bump):

| Name | Value | Why |
|---|---|---|
| `london_anchor` | 08:00 Europe/London | Section 5 (O1) |
| `newyork_anchor` | 09:30 America/New_York | Section 5 (O1) |
| `entry_window_minutes` | 120 | Section 6; equal to `max_hold_minutes` |
| `stop_basis` | opposite boundary of the range | Section 8 (O2) |
| `stop_buffer_points` | 20 (fixed; not linked to `risk.yaml`) | Section 8 |
| `max_hold_minutes` | 120 | Section 10 |
| `min_stop_points` | 500 | Section 8 (O4) |
| `stop_cap_points` | 2,000 | Section 8a (O4) |
| signals per range per date | 1 (first eligible close outside) | Section 6 |

## 15. Expected trade count

- **Feasible signals** per year, before the risk engine, averaged over the full years
  2021–2024 (evidence §2):

  | Variant, `or_bars` | `london` per year | `newyork` per year | Total per year | Per week |
  |---|---|---|---|---|
  | m15, 2 | 99.0 | 175.0 | 274.0 | 5.3 |
  | m5, 2 | 81.2 | 188.2 | 269.5 | 5.2 |
  | m15, 4 | 117.5 | 103.5 | 221.0 | 4.2 |
  | m5, 4 | 118.2 | 138.2 | 256.5 | 4.9 |

- Actual trades will be fewer: spread caps, loss limits and the cooldown apply. At most 2 per
  date.
- **Minimum for evaluation:** ≥ 30 closed trades in each OOS fold and ≥ 200 in the concatenated
  OOS set, and ≥ 30 per session and per direction (gates 8 and 9). Fewer → "insufficient
  sample", which is a fail (section 16).
- The tightest is `london` at `or_bars` 2, m5: 57–107 feasible signals per full year (evidence
  §2), before the risk engine.

## 16. Pre-registered evaluation (O6, O7)

**Periods and sealing** (unchanged from trend_pullback D1 and A1):

- Development data available: 2020-11-12 … 2025-09-30. Sealed: 2025-10-01 … 2026-10-08 (bar
  dates, UTC), refused by the loader before reading.
- **Evaluated trading period: 2020-12-15 … 2025-09-30**, 10,000 USD at 2020-12-15.
- During Phase 5 nothing inside the sealed period is inspected. It is opened once, with
  `--open-sealed --by --reason`, only after a candidate and its parameters are frozen and
  approved. The exact-tick subset (2026-06-26 onward) is reported separately.

**Folds.** Anchored, by entry date (UTC), identical to trend_pullback:

| Window | Dates |
|---|---|
| Training only | 2020-12-15 … 2021-09-30 |
| OOS fold 1 | 2021-10-01 … 2022-09-30 |
| OOS fold 2 | 2022-10-01 … 2023-09-30 |
| OOS fold 3 | 2023-10-01 … 2024-09-30 |
| OOS fold 4 | 2024-10-01 … 2025-09-30 |

- **Selection:** fold k uses the grid point with the highest net expectancy per trade over all
  development trades entered before fold k, among points with **≥ 100 such trades**. Ties are
  compared exactly (integer cents) and go to the default point (`or_bars` 2, `target_r` 1.5),
  then to the lexicographically smallest (`or_bars`, `target_r`). This is
  `aurum.validation.walkforward.select_point`.
- **Concatenated OOS** = each fold's trades from its selected point, from that point's own run
  (no rescaling).
- **Frozen configuration** for Phase 6 = the same rule over the whole development period.
- No purge or embargo is needed: no trade spans midnight UTC, and folds start at 00:00 UTC.

**Missing or insufficient samples** (no fallback anywhere):

- **No eligible point for a fold.** If no grid point of a variant has ≥ 100 development trades
  entered before fold k, fold k has no selected point and contributes **0 OOS trades**.
  - Gate 1 then fails ("insufficient sample") and the variant is rejected.
  - The 100-trade minimum is not lowered, the default point is not substituted, and no trade
    from fold k or later is used to select for fold k.
- **No frozen configuration.** If no point has ≥ 100 trades over the whole development period,
  there is no frozen configuration and the variant cannot go to Phase 6.
- **Thin sides and sessions.** A fold, side or session below its minimum (gates 1, 8, 9) is a
  fail, never "not evaluated".
- **Neighbours.** A neighbour with no trade in folds 1–4 has no net expectancy and counts as
  not > 0 (gate 10).
- **DSR inputs.** A trial whose daily-return series over the folds 1–4 market days has zero
  sample variance (for example, no exit in folds 1–4) has no defined Sharpe ratio.
  - It is left out of V and listed by name, but it still counts in N.
  - A candidate in that state has no OOS trades and has already failed gate 1.

**Reported** for each variant:

- net and gross expectancy (USD/trade and R/trade), net profit factor, max drawdown, trade
  count, holding times;
- spread / commission / slippage attribution;
- splits by fold, year, month, session (`london_morning` = `london` range, `ldn_ny_overlap`
  = `newyork` range), direction, `or_bars` and DST regime;
- the section 6 counters (`data_gap`, `blackout_skipped`, `stop_below_minimum`,
  `stop_above_cap`), range-date statuses, and risk-engine rejections by reason;
- the range width `W` distribution per range;
- the Phase 3b summary (worst/best replay);
- the total trial count, for this family and cumulative (66);
- the null baseline (`reference_fixed_time`) side by side, only once a like-for-like rerun over
  2020-12-15 … 2025-09-30 is authorised. Until then it is cited, not compared.

Win rate is reported but is **not** a criterion.

**Measurement definitions:** as trend_pullback section 16, unchanged.

- **Trade P&L** = the booked net P&L in USD, with all costs, from the trial run's trades table.
- **Slicing** is by entry decision time (UTC).
- **Net expectancy** = Σ P&L ÷ closed trades. **Net profit factor** = Σ wins ÷ |Σ losses|.
- **Max drawdown** is closed-trade: trades ordered by exit time, cumulative from 0, in USD.

**Pass thresholds**, per variant, on the concatenated OOS trades. **All** must hold;
"insufficient sample" anywhere is a **fail**.

1. **Sample:** ≥ 30 closed trades in every OOS fold and ≥ 200 in total.
2. **Positive after costs:** net expectancy > 0 USD/trade AND aggregate net P&L > 0.
3. **Chronological stability:** net expectancy > 0 in ≥ 3 of the 4 OOS folds.
4. **Profit factor:** net profit factor ≥ 1.10.
5. **Cost stress:** net expectancy > 0 in the spread ×1.5 re-run of the same selected points.
   The ×2.0 re-run is reported, not gated.
6. **Drawdown:** max drawdown ≤ 1,000 USD (10% of reference equity).
7. **No single month:** aggregate net P&L > 0 after removing all trades of the calendar month
   (UTC, by entry) with the largest net P&L.
8. **Not one direction:** long and short each have ≥ 30 trades and net expectancy ≥ 0.
9. **Not one session:** `london_morning` and `ldn_ny_overlap` each have ≥ 30 trades and net
   expectancy ≥ 0. For 8 and 9, ≥ 0 is deliberate: a flat side passes, a losing side fails.
10. **Neighbourhood:**
    - The frozen point's one-step neighbours are the grid points ±1 step on exactly one
      parameter. In the 2 × 3 grid there are 3 (`target_r` 1.5) or 2 (`target_r` at an edge).
    - Each neighbour is judged on its own trial run, at a fixed point, using its trades in
      folds 1–4.
    - At least ⌈2/3 × n⌉ must have net expectancy > 0: 2 of 3, or 2 of 2.
11. **Intra-bar:** `phase_3b_required` is false for the frozen configuration (its worst-case run
    and linked best-case replay; ADR-0009 §9).
12. **Deflated Sharpe ≥ 0.95** for the walk-forward OOS result. Formula and daily-return
    definition as trend_pullback gate 12 (Bailey & López de Prado, 2014), with (O6):
    - **N = 66**: every Phase 5 research trial so far (54 trend_pullback, 12 here);
    - **V** = the sample variance (ddof 1) of those trials' daily Sharpe ratios, each computed
      the same way over the folds 1–4 market days, with the zero-variance rule above. The
      trend_pullback values come from its existing runs, read-only.
13. **Sealed period untouched** (candidate-specific, as in the corrected trend_pullback report):
    - no opening_range_breakout trial's data range touches 2025-10-01 or later;
    - no sealed opening and no parity access by an opening_range_breakout run;
    - historical ledger rows of other strategies that touch the period are listed separately.
      Today these are the 10 `reference_fixed_time` trials recorded before the seal existed.
      They are not a candidate failure, and no ledger row is deleted or rewritten.

**Evaluation order** (O7). Gates 1–4, 6–9, 12 and 13 are evaluated first, read-only, on the
12 trial runs.

- **Any failure** rejects the variant and stops evaluation. Gates 5, 10 and 11 are then not
  run, and no rerun or change rescues the version.
- **All pass:** Claude stops for review. Gate 5 needs a stress re-run and gate 11 a best-case
  replay, both new simulations; gate 10 needs the frozen configuration. Each runs only once
  the reviewer authorises it.

M15 vs M5 are compared on OOS net expectancy and max drawdown after costs, never on win rate.
If both pass, both go to Phase 6.

**Sealed-test threshold** (Phase 6, opened once, frozen configuration, definitions as above):

- net expectancy > 0, net profit factor ≥ 1.05, and ≥ 50 trades. Fewer than 50 trades is
  insufficient sample = **fail**;
- the exact-tick subset (2026-06-26 onward) is reported separately; its result is
  information, not a separate gate.

## 17. Decision trace (worked example)

T = **2024-03-01T08:45:00Z**, variant m15, `london` range, defaults (`or_bars` 2, `target_r`
1.5). On that Friday the UK is on GMT (summer time from 2024-03-31), so 08:00 London = 08:00Z.
08:45 London is in `london_morning`, and 03:45 New York is outside every blackout window. All
values are reproduced in evidence §6, which reads nothing after each spending close.

| Condition | Values | Result |
|---|---|---|
| Anchor A | 08:00 London = 2024-03-01T08:00Z | |
| Range bar 1 (08:00–08:15Z) | H 2045.71, L 2043.25 | |
| Range bar 2 (08:15–08:30Z) | H 2043.89, L 2042.04 | |
| Range exists | both bars present | true |
| H, L, W | H 2045.71, L 2042.04, W = 367 points; `range_end` 08:30Z | |
| T eligible (rules 6.1–6.4) | M15 close in (08:30Z, 10:30Z], session open, no blackout | true |
| Read set complete | range bars + the M15 bar closing at 08:45Z | true |
| Earlier eligible closes | none (08:45Z is the first) | first close outside holds |
| Breakout short: `close(b0) < L` strictly | 2040.58 < 2042.04 | true → short |
| Stop: x = H + 20 × point; ceil(round(x / point, 6)) × point | 2045.71 + 0.20 = 2045.91 → 2045.91 | |
| d = round((stop − ref) / point) | (2045.91 − 2040.58) / 0.01 = 533 = W 367 + (L − ref) 146 + 20 | |
| 500 ≤ d ≤ 2,000 | 533 | true |
| Target: x = ref − target_r (1.5) × 5.33; ceil(round(x / point, 6)) × point | 2040.58 − 7.995 = 2032.585 → 2032.59 | |
| Final decision | | `Signal(SELL, market, time 08:45:00Z, stop 2045.91, target 2032.59, max_hold 120 min)` |
| Range spent | no further `london` signal on 2024-03-01 | |
| Fill | first quote at or after 08:45:00.200Z, at its bid − 5 points slippage (not 2040.58) | |
| Timeout (signal-to-exit) | 08:45Z + 120 min = 10:45Z | |

**Same date, variant m5, `london` (rejected).**

- The first eligible M5 close is 08:35Z, at 2041.32 < L, so the range is spent.
- d = round((2045.91 − 2041.32) / 0.01) = 459 < 500: no signal, and `stop_below_minimum`
  counts one.
- There is no further `london` signal for m5 that date.

**Same date, `newyork` range.** US daylight time starts on 2024-03-10, so 09:30 New York =
14:30Z.

- The range bars close at 14:45Z and 15:00Z: H 2054.85, L 2045.62, W = 923 points.
- **m15:** the first eligible close is 15:15Z = 10:15 New York (the blackout ends
  exclusively). Its close is 2055.71 > H, so the trade is long.
  - Stop = 2045.62 − 0.20 = 2045.42; d = 1,029 = 923 + 86 + 20.
  - Target = floor(2055.71 + 1.5 × 10.29 = 2071.145) = 2071.14.
  - Decision: `Signal(BUY, time 15:15:00Z, stop 2045.42, target 2071.14, max_hold 120 min)`.
- **m5:** 15:05Z and 15:10Z (10:05 and 10:10 New York) are blackout instants. Their closes
  (2050.71, 2052.63) are inside the range, so nothing is counted.
  - 15:15Z is the first eligible close: 2055.71 > H, the same signal as m15.

## 18. Known risks, failure modes and disclosure

- **Disclosure: what the author knew and which choices used data.**
  - **Prior knowledge.** The author had seen the trend_pullback 0.2.0 development results,
    including splits by session, direction, year and variant: `london_morning` lost,
    `ldn_ny_overlap` gained, m5 beat m15. No rule here was chosen to follow or avoid those
    splits: both sessions and both variants are kept.
  - **Data-informed choices.** All counts are reproduced in the evidence file, and nothing
    after a spending close was read.
    - **O2:** the opposite-boundary stop was chosen over the midpoint, which fails the 500-point
      minimum for 72–90% of `london` spending closes (evidence §3).
    - **O3:** the blackout treatment was chosen after counting both treatments (evidence §4).
    - **O5:** the exclusion of a 1-bar range cites the 2-bar `london` widths (section 14).
  - **Looked at, but not changed by the data:** O4 bounds, carried over from trend_pullback.
  - **Not data-informed:** O1 anchors, the 120-minute window and timeout, the `target_r`
    grid, O6, O7, O8.
  - **Scratch history.** On 2026-10-11 two scratch runs approximated the calendar with
    weekdays; the committed script supersedes them. They produced the first draft's counts.
    The committed script reproduces them exactly on the 1,237 market trading dates, and the
    O3 populations are reconciled in section 8a.
  - **Not unseen history.** The development period is unseen only for this rule set; the
    author has seen trend_pullback's P&L over the same dates. The cumulative DSR (O6) counts
    the earlier trials for that reason. A trend_pullback component that is not used here is
    not thereby judged: a rejected family does not disprove its individual filters
    (section 4).
- **Selection by range width in London.** The 500-point minimum removes 50–57% of the
  `london`, 2 spending closes, those with narrow ranges and small extensions. London trades
  are therefore taken mostly on wider-range dates. This is mechanical and stated in advance.
- **Volatility drift.** In 2025 London ranges widened, so fewer fall below the minimum, while
  New York ranges more often exceed the cap (section 8a). The session mix changes over the
  folds, and the Phase 6 period may differ again.
- **Short New York windows.** In normal weeks the `newyork` window has 5 (`or_bars` 2) or 3
  (`or_bars` 4) M15 decisions before the session ends at 16:30 London. 664 of 1,237
  `newyork`, 4, m15 ranges are `no_break`.
- **News exposure of open positions.** The blackout blocks entries only; open positions keep
  their stops. The latest timeouts in section 6 are:
  - **Normal weeks: no daily release slot is reached.**
    - A `london` position ends by 12:55 London = 07:55 New York, before 08:20.
    - A `newyork` position opens at 10:15 New York or later (after the 10:00 slot) and ends
      by 13:25 New York, before the 14:00 FOMC statement.
  - **Mismatch weeks (79 dates): both can be reached.**
    - 08:30 New York is then 12:30 London. A `london` signal after 10:30 London (only
      `or_bars` 4: m15 10:45, m5 10:35–10:55) has its deadline after 12:30 London.
      `london`, 2's latest deadline is exactly 12:30 London.
    - A `newyork` signal at 12:00 New York or later has its deadline at or after 14:00 New
      York. That is the FOMC statement on 8 dates of the period: 2021-03-17, 2021-11-03,
      2022-03-16, 2022-11-02, 2023-03-22, 2023-11-01, 2024-03-20 and 2025-03-19.
  - Under O3, an m5 entry at 10:15 New York or later can follow a 10:00 release move that
    the blackout skipped (468 of the 1,036 spent `newyork`, 2, m5 ranges).
- **US holidays.** The `newyork` range forms on every date of the market calendar, including
  US exchange holidays with no cash open. Participation on those dates is thin, and they are
  not excluded in 0.1.0.
- **Bar-only development history.** Intra-bar flags are expected to be rare at stops ≥ 500
  points, not guaranteed. Phase 3b is decided per candidate from the backtests.
- **Sample dependence.** At most two trades per date, one per session, sharing a macro
  backdrop.
- **Implementation prerequisites** (not rules, to be built and tested only when authorised):
  - `warmup_bars = {}` accepted by the pipeline;
  - the risk engine's 100-bar M15 history declared by bar count (section 3);
  - the strategy reading the risk engine's `NewsBlackout` (allowed: `aurum.strategies` may
    import `aurum.sessions`);
  - the section 6 counters implemented exactly as tabled.

## 19. Change log

| Version | Date | Change | Reason |
|---|---|---|---|
| 0.1.0 (draft) | 2026-10-11 | Initial draft: two opening ranges (08:00 London, 09:30 New York), first close outside the range at M15 or M5, opposite-side stop + 20 points, 500/2,000-point bounds, 120-minute window and timeout, blackout instants not eligible, 12-trial grid, gates adapted from trend_pullback with the corrected gate 13 and an explicit evaluation order. Decisions O1–O8 open | Reviewer authorised the spec draft only (2026-10-11), after trend_pullback 0.2.0 was rejected |
| 0.1.0 (draft, revision 2) | 2026-10-11 | O1–O8 recorded as accepted in principle. **No rule changed.** Corrects the m5 latest decision times (10:55 London; 11:25 or 12:25 New York) and the timeout and news-exposure examples, mismatch weeks included. Separates strategy warm-up (none) from the risk engine's 100-bar M15 ATR history. Commits the feasibility script and evidence, reconciles the two O3 populations and lists every data-informed choice. Describes the anchors as research anchors and states that a rejected family does not disprove its filters. Defines behaviour with missing training samples and the exact diagnostic counters. Writes out the short stop arithmetic and the fixed, unlinked 20-point buffer | Reviewer's revision request (2026-10-11) |
