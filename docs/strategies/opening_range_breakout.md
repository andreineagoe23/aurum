# opening_range_breakout — strategy spec

> **Status: DRAFT 0.1.0, for review. Not approved, not pre-registered.** Written by Claude on
> 2026-10-11 at the reviewer's request ("draft the specification only"). There is no strategy
> code, no backtest, no P&L and no ledger row for this family.
>
> - Written from `docs/STRATEGY_SPEC_TEMPLATE.md`. The only data used are descriptive counts
>   from development-period bars (bar dates ≤ 2025-09-30), in sections 8a and 15, and the trace
>   in section 17: opening-range widths and the stop distance at the first close outside the
>   range. **No price after a breakout close was read**, and nothing in the sealed period.
> - **Written after the trend_pullback 0.2.0 results were seen** (rejected 2026-10-11;
>   `docs/strategies/evidence/trend_pullback_2026-10-10_dev_analysis.md`). This family is not a
>   revision of trend_pullback, and no rule here was chosen from those results. The choices that
>   used data are feasibility choices, each stated where it is made (O2, O3, O4). Section 18
>   records the disclosure.
> - Section 0 lists the decisions the reviewer must make before approval. On approval the
>   rules freeze, and nothing changes after the first P&L-producing run. If every trial fails,
>   the result is "opening_range_breakout 0.1.0 failed".

## 0. Decisions for the reviewer (proposed, not approved)

| # | Decision | Proposed choice | Alternative considered |
|---|---|---|---|
| O1 | Range anchors | London range at **08:00 London**, New York range at **09:30 New York** (section 5) | Session starts from `sessions.yaml` (07:00 and 13:00 London) |
| O2 | Stop basis | **Opposite side of the range**, 20 points beyond it (section 8) | Range midpoint: 82–90% of London 30-minute breakouts fall under the 500-point minimum (section 8a), so it is not feasible |
| O3 | News blackout | A close at an instant inside a news blackout window **neither signals nor spends** the range (section 12) | The close spends the range and the risk engine rejects the signal: 46% of New York 30-minute M5 first breakouts (487 of 1,055) would then be lost by construction |
| O4 | Stop bounds | Minimum **500 points** (as trend_pullback D3), cap **2,000 points** (ceiling 2,490) (section 8a) | A lower minimum raises bar-resolution ambiguity (reports/phase_3.md §4) |
| O5 | Search grid | 2 variants × `or_bars` {2, 4} × `target_r` {1.0, 1.5, 2.0} = **12 trials** (section 14) | Searching the stop basis or the window length: more trials, and the midpoint stop is not feasible |
| O6 | DSR trial count | **N = 66**: all Phase 5 research trials so far (54 trend_pullback + 12 here), V over the 66 trials' daily Sharpe ratios (section 16) | N = 12, this family's search only |
| O7 | Period, folds, thresholds, order | Same evaluated period and folds as trend_pullback A1, and the same gates adapted (section 16). Evaluation stops at the first mandatory failure | — |
| O8 | H1 regime | **None** in 0.1.0 (section 4) | Trade only in the direction of an H1 EMA slope: that brings in trend_pullback's regime rule, whose family was just rejected |

## 1. Identity

- Name: `opening_range_breakout`
- Family: `opening_range_breakout`
- Variant: `m15` (enter on the M15 close outside the range) and `m5` (the same M15 range,
  entry on the M5 close outside it). These are two variants of one declared family search;
  only the decision timeframe differs (sections 6 and 7).
- Version: `0.1.0` (draft)
- Author: Claude (draft) for Andrei Neagoe
- Date: 2026-10-11

## 2. Hypothesis

- **Economic rationale.**
  - Two opens bring a block of new participants into gold:
    - **London**: the London OTC gold market, the LBMA dealers and the European equity open,
      at 08:00 London.
    - **New York**: the US cash equity open at 09:30 New York. US institutional and gold ETF
      flows start then, after the 08:30 data releases.
  - Orders that built up while those participants were away are worked in the first 30–60
    minutes. That two-sided auction sets the opening range: dealers and short-term
    mean-reversion traders sell its top and buy its bottom.
  - A close outside the range means one side's flow is larger than the liquidity offered at
    the edge. Large orders are executed over hours (TWAP/VWAP), not minutes, and the stops of
    traders who faded the edge sit just beyond it. Both push price further the same way.
  - The trade is on the side of the break, against the range faders whose stops are hit.
- **Why it should persist after costs.**
  - Costs are the same as for trend_pullback: about 16–21 points per round trip at 0.01 lot.
    That is the median decision-time spread of 6–7 points (reports/phase_2.md §5), commission
    of 4.50 USD per lot (4.5 points), and 5 points of slippage on the market entry and on a
    stop exit.
  - Stops are 500–2,000 points (section 8), so costs are about 1–4% of the risk (R). The idea
    needs an average gross result above about +0.04R per trade. This is a hypothesis, not an
    expected number.
- **Falsification.** Concatenated out-of-sample net expectancy ≤ 0 USD/trade, or ≤ 0 in 2 or
  more of the 4 out-of-sample folds (section 16). Either kills version 0.x.

## 3. Data, time and indicator semantics

All bars are canonical bid bars, keyed by open time and visible at
`close_time = open_time + timeframe`. A bar is **completed at T** if `close_time ≤ T`. Nothing
with `close_time > T` is used at T.

| Timeframe | Fields used | Bars used at decision time T |
|---|---|---|
| H1 | none | not used (section 4) |
| M15 | high, low (range); close (variant m15) | the `or_bars` range bars (section 5); variant m15: every M15 bar closing in `(range_end, T]` |
| M5 | close (variant m5 only) | every M5 bar closing in `(range_end, T]` |
| Ticks | bid, ask | Execution only (simulator, broker) |

**Notation and conventions** (as in trend_pullback section 3):

- Rules are written for a **long**. A short mirrors every comparison: `>` ↔ `<`, high ↔ low,
  round down ↔ round up.
- All comparisons are exact float64 comparisons of unrounded values, with no tolerance.
  "Strictly" means equality fails.
- **Rounding to the price grid** (stop, target) removes float noise first:
  `q = round(x / point, 6)`, then `floor(q)` or `ceil(q)` as the rule says, × `point`.
  Distances in points are `round((a − b) / point)`, an integer.
- **Times.** London times are Europe/London wall clock and New York times are
  America/New_York wall clock, converted to UTC with `zoneinfo` (DST-aware). Each anchor
  follows its own city's daylight time. In the weeks when US and UK daylight time differ,
  09:30 New York is 13:30 London instead of 14:30.
- **Searched parameters** appear by name: `or_bars` (default 2, grid {2, 4}) and `target_r`
  (default 1.5, grid {1.0, 1.5, 2.0}). The worked example (section 17) uses the defaults.

**Indicators:** none. The rules use bar highs, lows and closes only.

**Warm-up: none.** Every value a decision reads comes from bars of the same day, after the
range anchor. The strategy declares `warmup_bars = {}`. The first decision of the evaluated
period needs no earlier history, so the period starts where trend_pullback's did (O7) for
comparability, not because of a warm-up need.

**Stateless.** Every rule below is a function of the bars and the calendar at T. Nothing is
stored, and a restart re-derives the same state, including whether a range is spent.

## 4. H1 context (regime rule)

- **None in 0.1.0 (O8).** The hypothesis is about order flow at two opens, and the break sets
  the direction. A trend filter would add a second hypothesis (trend_pullback's), which the
  development evaluation could not separate from this one.
- The "no high-impact news" part of the master prompt's regime row is handled by section 12
  (rule 12.3) and by the risk engine's blackout, unchanged.
- Volatility is bounded only through the stop rules in section 8.

## 5. M15 setup: the opening range

There are two ranges per trading day:

| Range | Anchor A (local) | Session (`sessions.yaml`) | Anchor in UTC |
|---|---|---|---|
| `london` | 08:00 Europe/London | `london_morning` (07:00–11:00 London) | 08:00Z in winter, 07:00Z in summer |
| `newyork` | 09:30 America/New_York | `ldn_ny_overlap` (13:00–16:30 London) | 14:30Z in winter, 13:30Z in summer |

- **Why these anchors (O1).**
  - 08:00 London is the London open: the OTC market and LBMA dealers, and the European equity
    open (LSE). From 07:00 to 08:00 London only continental Europe is open (08:00 CET),
    without London's participants.
  - 09:30 New York is the US cash open. It is after the 08:30 New York releases, so those
    releases are not inside the range.
  - Both anchors lie inside their session in every DST combination: 09:30 New York is 14:30
    London, or 13:30 London in the mismatch weeks.
- **Range bars.** The `or_bars` M15 bars with `open_time` = A, A + 15 min, …,
  A + 15 × (`or_bars` − 1) min. `or_bars` = 2 is a 30-minute range and 4 is a 60-minute
  range.
- **`range_end`** = A + 15 × `or_bars` min, the close of the last range bar.
- **Range exists** only if all `or_bars` bars exist. Otherwise there is no setup for that range
  that day, and each decision time of its window counts one `data_gap` (section 16).
- **`H`** = max high of the range bars; **`L`** = min low; **`W`** = `round((H − L) / point)`
  points. `W` is reported (section 15), not filtered.
- The range is fixed at `range_end`. It never moves afterwards.

## 6. Decision times, breakout and the M5 trigger

**Entry window.** For each range, the instants T with `range_end < T ≤ range_end + 120 min`.

**Eligible decision time.** T is eligible for a range when all of these hold:

1. T is a bar close of the variant's decision timeframe: M15 for `m15`, M5 for `m5`.
2. T is in the entry window.
3. T is a decision time of the range's session: `session start ≤ T < session end` with
   entries allowed (`aurum.sessions.clock`, `SessionPhase.entries_allowed`). A bar closing
   exactly at 11:00 or 16:30 London is not a decision time.
4. T is not inside a news blackout window (rule 12.3).

**Read set at T:** the range bars, plus every bar of the decision timeframe with
`close_time` in `(range_end, T]`.

- If any of these bars is missing, there is no signal at T, counted once as `data_gap`.
- Because every later read set contains the same missing bar, the rest of that window is also
  `data_gap`.

**Breakout (long)** at an eligible T, with `c(T)` the close of the decision-timeframe bar
closing at T:

1. `c(T) > H` strictly; and
2. **first close outside:** for every earlier eligible decision time T′ of the same range and
   day, `L ≤ c(T′) ≤ H` (equality counts as inside).

A short mirrors this: `c(T) < L` strictly.

**One use per range per day.**

- The range is **spent** at the first eligible decision time whose close is outside `[L, H]`.
  This holds whether that close produces a signal (section 8 bounds) and whether the risk
  engine approves it.
- After that, the range gives no signal that day.
- A close outside the range at an instant that is **not** eligible (rule 12.3, or a session
  phase without entries) neither signals nor spends the range. It is counted as
  `blackout_skipped` when the reason is the blackout.

**Variant m15:** decision times are M15 closes; `c(T)` = `close(b0)`, the M15 bar closing at T.

**Variant m5:** the setup is the same M15 range. Decision times are M5 closes; `c(T)` =
`close(m0)`, the M5 bar closing at T. The M5 trigger needs nothing else: no previous-bar
condition and no M15 close after the range.

**Window lengths** (eligible M15 closes before rule 12.3; M5 has three times as many):

| Range, `or_bars` | Window (local) | M15 decision times |
|---|---|---|
| `london`, 2 | 08:45 … 10:30 London | 8 |
| `london`, 4 | 09:15 … 10:45 London (11:00 is the session end) | 7 |
| `newyork`, 2 | 10:15 … 11:15 New York (16:15 London; 16:30 is the session end) | 5 (8 in mismatch weeks, until 12:00 New York) |
| `newyork`, 4 | 10:45 … 11:15 New York | 3 (7 in mismatch weeks, until 12:15 New York) |

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

- **Price rule (O2)**, long: `x = L − 20 × point`, `stop = floor(round(x / point, 6)) × point`.
  Short: `x = H + 20 × point`, rounded up with `ceil`.
  - **The stop is the opposite side of the range.** A long that falls back through the whole
    range has disproved the breakout.
  - **The 20-point buffer** equals `max_spread_points` (`risk.yaml`). A short closes at the
    ask, while the range is measured on bid bars. Without the buffer, a short's stop could be
    hit by the spread alone while the bid is still inside the range. The buffer is applied to
    both sides so the rule stays symmetric.
- **Distance:** `d = round((ref − stop) / point)` points.
- **Minimum (O4):** `d ≥ max(500, trade_stops_level + 10)` (`trade_stops_level` is 0 today).
  Otherwise there is no signal, counted `stop_below_minimum`, and the range is spent.
  - The reason is the same as trend_pullback D3: a lower minimum raises bar-resolution
    ambiguity (reports/phase_3.md §4). It does **not** guarantee that the Phase 3b
    flagged-trade share stays at or under 1%.
- **Always attached broker-side** at order submission.

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

- **Stop above the cap → no signal.** If `d > 2,000`, there is no signal, counted
  `stop_above_cap`, and the range is spent. The stop is never clamped, shortened or moved.
- `stop_below_minimum`, `stop_above_cap` and risk-engine rejections are separate counters.
- Changing the cap, the minimum, the buffer or any input above is a version bump.

**Descriptive counts** (development period, trading dates 2020-12-15 … 2025-09-30, weekdays
with the range bars present). Each row is the first close outside the range in the entry
window, under rules 6.1–6.4 with the stop of this section. These are counts from a scratch
script, not a backtest. The risk engine has not been applied (one position at a time, spread,
loss limits), and no price after the breakout close was read.

| Range, `or_bars`, variant | Range width `W` p10 / p50 / p90 (points) | First break in window | `d` p10 / p50 / p90 / p99 (points) | `d` < 500 | `d` > 2,000 | Within bounds |
|---|---|---|---|---|---|---|
| london, 2, m15 | 213 / 383 / 739 | 1,115 of 1,237 (90%) | 291 / 499 / 982 / 1,759 | 50% | < 0.5% | 552 |
| london, 2, m5 | 213 / 383 / 739 | 1,153 (93%) | 272 / 458 / 866 / 1,601 | 57% | < 0.5% | 486 |
| london, 4, m15 | 288 / 529 / 1,046 | 907 (73%) | 348 / 619 / 1,186 / 2,073 | 32% | 1% | 602 |
| london, 4, m5 | 288 / 529 / 1,046 | 1,011 (82%) | 339 / 584 / 1,107 / 2,073 | 37% | 1% | 620 |
| newyork, 2, m15 | 414 / 716 / 1,329 | 953 (77%) | 524 / 906 / 1,634 / 2,881 | 8% | 5% | 831 |
| newyork, 2, m5 | 414 / 716 / 1,329 | 1,036 (84%) | 510 / 860 / 1,590 / 2,758 | 9% | 4% | 893 |
| newyork, 4, m15 | 569 / 988 / 1,898 | 573 (46%) | 668 / 1,181 / 2,107 / 4,188 | 3% | 13% | 484 |
| newyork, 4, m5 | 569 / 988 / 1,898 | 735 (59%) | 656 / 1,066 / 1,940 / 3,913 | 3% | 9% | 645 |

- 1,251 weekdays. On 14 of them a range or window bar is missing (market holidays and
  missing data), which leaves 1,237 per range. Shares are rounded to whole percent.
- **Volatility drift.** Shares change by year, the same drift trend_pullback section 18
  describes.
  - `london`, 2, m15: `d` < 500 for 46–71% of breaks in 2021–2024, 10% in 2025 (Jan–Sep).
  - `newyork`, 4, m15: `d` > 2,000 for 6–14% in 2021–2024, 31% in 2025.
- **Why O2.** With the stop at the range midpoint (`L + W/2`), 82% (m15) and 90% (m5) of the
  `london`, 2 breaks were below 500 points, against 50% and 57% with the opposite side.
- **Why O3.** For `newyork`, 2, m5, the M5 closes at 10:05 and 10:10 New York lie inside the
  09:50–10:15 blackout. Under O3, 468 of the 1,036 first breaks come at or after 10:15 New
  York, following a close outside the range that rule 12.3 skipped. Under the alternative,
  those ranges would have been spent by a signal the risk engine must reject. No other row
  is affected: no other window contains a blackout instant.

## 9. Target

- Long: `x = ref + target_r × (ref − stop)`, `target = floor(round(x / point, 6)) × point`
  (toward the entry). Short: `x = ref − target_r × (stop − ref)`, rounded up with `ceil`.
  `target_r` defaults to 1.5 (grid {1.0, 1.5, 2.0}).
- It is set broker-side with the stop at submission. Because the fill is not at `ref`, the
  realised reward-to-risk is slightly below R. That is accepted and measured, not corrected.

## 10. Timeout (signal-to-exit)

- `max_hold = 120 minutes`, from `Signal.time` (the engine's existing rule, trend_pullback
  D6 b). The deadline is `Signal.time + 120 min`, rebuilt from the persisted signal after a
  restart, and never restarted. At expiry the engine closes at market (TIMEOUT), filled on the
  next quote after latency.
- **Why 120.** It equals the entry window, the hours after an open in which the hypothesis says
  the flow is worked.
  - The latest `london` signal (10:45 London) is closed by 12:45 London. That is before the
    `ldn_ny_overlap` session (13:00) and before the earliest `newyork` range (13:30 London in
    mismatch weeks).
  - The latest `newyork` signal (16:15 London) is closed by 18:15 London, before force-flat.
- Market orders only, so no fill-based timeout is needed (trend_pullback D6 note).
- Force-flat (section 12) applies first if it comes earlier.

## 11. Invalidation and exit precedence

- **No invalidation rule in 0.1.0.** The exits are stop, target, timeout and force-flat. The
  stop sits beyond the far side of the range, so a failed breakout exits there; an earlier
  "close back inside the range" exit would be a second, untested rule.
- **Simultaneous or ambiguous exits:** delegated unchanged to the simulator (ADR-0009 §5) and,
  on demo, to the broker. The strategy adds no exit rule of its own.

## 12. Session constraints and signal priority

1. **Decision times:** section 6 rules 1–3. `london` uses `london_morning`; `newyork` uses
   `ldn_ny_overlap`.
2. **Risk engine gates, unchanged:** cutoff (no entries within 45 min of `force_flat_at`),
   news blackout, spread caps (`max_spread_points` 20, `max_spread_atr_fraction` 0.15),
   `max_trades_per_day: 3`, loss budgets, consecutive-loss cooldown, one position at a time.
3. **News blackout in the strategy (O3).** An instant T with
   `aurum.sessions.news.NewsBlackout.reason_at(T)` not None is not an eligible decision time.
   - This is the same object and the same `risk.yaml` `news_blackout` windows the risk engine
     uses: `[start, end)`, New York time, plus the FOMC window on statement dates.
   - A close at such an instant neither signals nor spends the range (section 6).
   - The reason: the strategy never emits a signal that the risk engine must reject by
     construction, and m5 then first evaluates the `newyork`, 2 range at 10:15 New York, the
     same instant as m15.
   - 10:15 New York is eligible, because the window's end is exclusive.
   - Inside the windows of section 6 only the 09:50–10:15 New York window can occur. The
     08:20–08:45 window and the FOMC window (13:30–15:00 New York) lie outside every entry
     window.
4. **Force-flat** at `min(20:00 London, market close − 15 min)`. Open positions are **not**
   closed at a session end.
5. **Signal priority:**
   - Each variant runs as its own strategy, in its own backtest run and account.
   - Within a variant there is at most one signal per range per day, so at most two per day.
     The `london` position is always closed (by 12:45 London) before the `newyork` window
     opens, so the two never compete.
   - `max_trades_per_day: 3` never binds. A signal while a position or pending order exists
     is rejected by the risk engine (exposure), with no queueing and no reversal.

## 13. Cost assumptions

- **Spread:** real bid/ask from ticks on exact days. On bar-only days, the bar's minimum
  spread + `bar_spread_extra_points` (2), per ADR-0009. Never a constant.
- **Commission:** 2.25 USD per side per lot (4.50 round trip), from `configs/broker.yaml`.
- **Slippage:** 5 points per side, from `configs/broker.yaml` (provisional).
- **Stress:** spread ×1.5 and ×2.0 re-runs are mandatory before any pass; both are reported
  (section 16, gate 5).

## 14. Parameters and search budget (O5)

| Name | Unit | Default | Justified range | Grid | Why |
|---|---|---|---|---|---|
| `or_bars` | M15 bars | 2 | 2–4 (30–60 min) | {2, 4} | The two standard opening-range lengths. 1 bar (15 min) is mostly under the 500-point minimum in London (`W` p50 is 383 points even at 2 bars). More than 4 leaves under an hour of the New York window |
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
| `entry_window_minutes` | 120 | Section 6; equal to `max_hold_minutes` (section 10) |
| `stop_basis` | opposite side of the range | Section 8 (O2) |
| `stop_buffer_points` | 20 | Section 8, equals `max_spread_points` |
| `max_hold_minutes` | 120 | Section 10 |
| `min_stop_points` | 500 | Section 8 (O4) |
| `stop_cap_points` | 2,000 | Section 8a (O4) |
| signals per range per day | 1 (first eligible close outside) | Section 6 |

## 15. Expected trade count

- **Feasible first breaks** (the "within bounds" column of section 8a, before the risk engine),
  averaged over the full years 2021–2024:

  | Variant, `or_bars` | `london` per year | `newyork` per year | Total per year | Per week |
  |---|---|---|---|---|
  | m15, 2 | 99 | 175 | 274 | 5.3 |
  | m15, 4 | 118 | 104 | 221 | 4.3 |
  | m5, 2 | 81 | 188 | 270 | 5.2 |
  | m5, 4 | 118 | 138 | 257 | 4.9 |

- Actual trades will be fewer: spread caps, loss limits and the cooldown apply. At most 2 per
  day.
- **Minimum for evaluation:** ≥ 30 closed trades in each out-of-sample fold and ≥ 200 in the
  concatenated out-of-sample set, and ≥ 30 per session (gate 9). Fewer → "insufficient
  sample", which is not a pass.
- About 80–100 feasible `london` breaks per year at `or_bars` 2 give room above 30 per fold;
  the per-session minimum is the tighter one.

## 16. Pre-registered evaluation (O6, O7)

**Periods and sealing** (unchanged from trend_pullback D1 and A1):

- Development data available: 2020-11-12 … 2025-09-30. Sealed: 2025-10-01 … 2026-10-08 (bar
  dates, UTC), refused by the loader before reading.
- **Evaluated trading period: 2020-12-15 … 2025-09-30**, 10,000 USD at 2020-12-15, the same
  as trend_pullback A1. No warm-up is needed (section 3); the same start keeps the two
  families' windows identical.
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
  development trades entered before fold k, among points with ≥ 100 such trades. Ties are
  compared exactly (integer cents) and go to the default point (`or_bars` 2, `target_r` 1.5),
  then to the lexicographically smallest (`or_bars`, `target_r`).
- **Concatenated OOS** = each fold's trades from its selected point, from that point's own run
  (no rescaling).
- **Frozen configuration** for Phase 6 = the same rule over the whole development period.
- No purge or embargo is needed: no trade spans midnight UTC, and folds start at 00:00 UTC.

**Reported** for each variant:

- net and gross expectancy (USD/trade and R/trade), net profit factor, max drawdown, trade
  count, holding times;
- spread / commission / slippage attribution;
- splits by fold, year, month, session (`london_morning` = `london` range, `ldn_ny_overlap`
  = `newyork` range), direction and `or_bars`;
- `stop_below_minimum`, `stop_above_cap`, `blackout_skipped`, `data_gap` and risk-rejection
  counts. One `data_gap` = an eligible decision time whose read set (section 6) has a missing
  bar; diagnostic only;
- range width `W` distribution per range;
- the Phase 3b summary (worst/best replay);
- the total trial count, for this family and cumulative;
- the null baseline (`reference_fixed_time`), side by side only once a like-for-like rerun over
  2020-12-15 … 2025-09-30 is authorised. Otherwise it is cited, not compared.

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
    - **V** = the sample variance (ddof 1) of those 66 trials' daily Sharpe ratios, each
      computed the same way over the folds 1–4 market days. The trend_pullback values come
      from its existing runs, read-only.
    - If O6 is decided as N = 12, V is the variance over the 12 trials of this family.
13. **Sealed period untouched** (candidate-specific, the corrected trend_pullback reporting):
    - no opening_range_breakout trial's data range touches 2025-10-01 or later;
    - no sealed opening and no parity access by an opening_range_breakout run.
    - Historical ledger rows of other strategies that touch the period are listed separately.
      Today these are the 10 `reference_fixed_time` trials recorded before the seal existed.
      They are not a candidate failure, and no ledger row is deleted or rewritten.

**Evaluation order** (O7). Gates 1–4, 6–9, 12 and 13 are evaluated first, read-only, on the
12 trial runs.

- If any of them fails, the variant is rejected and evaluation stops. Gates 5, 10 and 11 are
  then not run, and no rerun or change rescues the version.
- If all of them pass, Claude stops for review. Gates 5 and 11 need new simulations (the
  stress re-run and the best-case replay), and gate 10 needs the frozen configuration; each
  is run only once the reviewer authorises it.

M15 vs M5 are compared on OOS net expectancy and max drawdown after costs, never on win rate.
If both pass, both go to Phase 6.

**Sealed-test threshold** (Phase 6, opened once, frozen configuration, definitions as above):

- net expectancy > 0, net profit factor ≥ 1.05, and ≥ 50 trades. Fewer than 50 trades is
  insufficient sample = **fail**;
- the exact-tick subset (2026-06-26 onward) is reported separately; its result is
  information, not a separate gate.

## 17. Decision trace (worked example)

T = **2024-03-01T08:45:00Z**, variant m15, `london` range, defaults (`or_bars` 2, `target_r`
1.5). On that Friday the UK is on GMT (summer time from 2024-03-31), so 08:00 London =
08:00Z. 08:45 London is in `london_morning`. In New York it is 03:45, outside every blackout
window.

**Causality:** every bar used closes at or before T, and `data_as_of = T`. Only the bars below
were read; nothing after 08:45Z.

| Condition | Values | Result |
|---|---|---|
| Anchor A | 08:00 London = 2024-03-01T08:00Z | |
| Range bar 1 (08:00–08:15Z) | H 2045.71, L 2043.25 | |
| Range bar 2 (08:15–08:30Z) | H 2043.89, L 2042.04 | |
| Range exists (`or_bars` = 2 bars present) | both present | true |
| H, L, W | H 2045.71, L 2042.04, W = 367 points; `range_end` 08:30Z | |
| T eligible: M15 close, in (08:30Z, 10:30Z], in session, no blackout | 08:45Z | true |
| Earlier eligible closes in the window | none (08:45Z is the first) | first close outside holds |
| Breakout short: `close(b0) < L` strictly | 2040.58 < 2042.04 | true → short |
| Stop: x = H + 20 × point; ceil(round(x / point, 6)) × point | 2045.71 + 0.20 = 2045.91 → 2045.91 | |
| d = (stop − ref) / point | (2045.91 − 2040.58) / 0.01 = 533 | |
| 500 ≤ d ≤ 2,000 | 533 | true |
| Target: x = ref − target_r (1.5) × 5.33; ceil(round(x / point, 6)) × point | 2040.58 − 7.995 = 2032.585 → 2032.59 | |
| Final decision | | `Signal(SELL, market, time 08:45:00Z, stop 2045.91, target 2032.59, max_hold 120 min)` |
| Range spent | no further `london` signal on 2024-03-01 | |
| Fill | first quote at or after 08:45:00.200Z, at its bid − 5 points slippage (not 2040.58) | |
| Timeout (signal-to-exit) | 08:45Z + 120 min = 10:45Z | |

**Same day, `newyork` range, m15.** US daylight time starts on 2024-03-10, so 09:30 New
York = 14:30Z.

- The range bars close at 14:45Z and 15:00Z: H 2054.85, L 2045.62, W = 923 points.
- The first eligible M15 close is 15:15Z = 10:15 New York. It is eligible because the
  09:50–10:15 blackout ends exclusively.
- Its close is 2055.71 > 2054.85, so the trade is long.
- Stop = 2045.62 − 0.20 = 2045.42, d = 1,029 points, within bounds.
- Target = floor(2055.71 + 1.5 × 10.29 = 2071.145) = 2071.14.
- Decision: `Signal(BUY, time 15:15:00Z, stop 2045.42, target 2071.14, max_hold 120 min)`.
- For variant m5, the M5 closes at 15:05Z and 15:10Z (10:05 and 10:10 New York) are not
  eligible (rule 12.3), whatever their closes.

## 18. Known risks / failure modes

- **Post-results authorship (disclosure).** This draft was written after the trend_pullback
  0.2.0 development results were seen. Those results include session, direction, year and
  variant splits: `london_morning` lost, `ldn_ny_overlap` gained, and m5 beat m15.
  - Nothing here uses them. Both sessions and both variants are kept, and the anchors come
    from exchange opens.
  - The development period is not unseen market history to the author, only unseen for this
    rule set. The DSR proposal (O6) counts the earlier trials for that reason.
- **Selection by range width in London.** The 500-point minimum removes about half of the
  `london`, 2 breaks: those with narrow ranges and small extensions. London trades are
  therefore taken mostly on wider-range days. This is mechanical and stated in advance, not a
  filter added later.
- **Volatility drift.** In 2025 London ranges widened, so fewer fall below the minimum, and
  New York ranges more often exceed the cap (section 8a). The mix of sessions changes over
  the folds, and the Phase 6 period may differ again.
- **Short New York windows.** In normal weeks the `newyork` window has 5 (`or_bars` 2) or 3
  (`or_bars` 4) M15 decisions before the session ends at 16:30 London. 54% of `newyork`, 4,
  m15 ranges have no break in that window.
- **News.**
  - The 10:00 New York releases fall inside or right after the `newyork` range.
  - Under O3, an m5 entry at 10:15 New York or later can follow a release-driven move that
    the blackout skipped (468 of 1,036 `newyork`, 2, m5 first breaks).
  - Open positions are not protected by the blackout.
    - In normal weeks no position is open at a daily release slot. A `london` position is
      closed by 12:45 London, before 08:30 New York (13:30 London). A `newyork` position
      opens at 10:15 New York or later and is closed by 13:15 New York.
    - In the DST-mismatch weeks both can be. 08:30 New York is then 12:30 London, while a
      `london` position can last until 12:45 London. A `newyork` position can last until
      14:15 New York, past a 14:00 FOMC statement (e.g. 2024-03-20 fell in such a week).
- **US holidays.** The `newyork` range is formed on every day of the market calendar,
  including US exchange holidays when there is no cash open. Those days have thin
  participation and are not excluded in 0.1.0.
- **Bar-only development history.** Intra-bar flags are expected to be rare at stops ≥ 500
  points, not guaranteed. Phase 3b is decided per candidate from the backtests.
- **Sample dependence.** At most two trades per day, one per session; trades on the same day
  share a macro backdrop.
- **Implementation prerequisites** (not rules): `warmup_bars = {}` must be accepted by the
  pipeline, and the strategy must read the risk engine's `NewsBlackout` (allowed:
  `aurum.strategies` may import `aurum.sessions`).

## 19. Change log

| Version | Date | Change | Reason |
|---|---|---|---|
| 0.1.0 (draft) | 2026-10-11 | Initial draft: two opening ranges (08:00 London, 09:30 New York), first close outside the range at M15 or M5, opposite-side stop + 20 points, 500/2,000-point bounds, 120-minute window and timeout, blackout instants not eligible, 12-trial grid, gates adapted from trend_pullback with the corrected gate 13 and an explicit evaluation order. Decisions O1–O8 open | Reviewer authorised the spec draft only (2026-10-11), after trend_pullback 0.2.0 was rejected |
