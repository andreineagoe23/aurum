# trend_pullback — strategy spec

> **Status: APPROVED 0.2.0, pre-registered** by the owner on 2026-10-10. The approved text is
> commit `b9baf4c`; later edits are labelled, not rule changes. The owner's approval was of
> the reported specification, not a line-by-line review of that diff.
>
> - Written from `docs/STRATEGY_SPEC_TEMPLATE.md`. No backtest, P&L or outcome of these rules
>   had been computed when it was approved. The only data used were the descriptive counts in
>   sections 8a and 15 and the trace in section 17, all from the development period (bar
>   close ≤ 2025-09-30).
> - **Frozen.** No rule, parameter, threshold or grid change is accepted after the first
>   P&L-producing run. Any change is a new version that restarts the development evaluation.
>   If all 54 trials fail, the result is "trend_pullback 0.2.0 failed"; the thresholds are not
>   adjusted.

## 0. Decisions (approved 2026-10-10)

| # | Decision | Approved choice |
|---|---|---|
| D1 | Sealed final-test period | 2025-10-01 … 2026-10-08, with the sealing conditions in section 16 |
| D2 | Stop-size cap | 2,000 points (ceiling 2,490): reject the setup, never shorten the stop |
| D3 | Minimum stop | 500 points: a fixed strategy rule, not proof that Phase 3b stays under 1% |
| D4 | Search grid | 2 variants × 3 × 3 × 3 = 54 trials (section 14) |
| D5 | Pass thresholds | Section 16, unchanged after results |
| D6 | Timeout start | **(b) `Signal.time`**: deadline = `Signal.time + 180 min` |

**D6 (b): signal-to-exit timeout.**

- `max_hold` is a **maximum decision age**: the position is closed once 180 minutes have
  passed since the decision instant `Signal.time`. It is not strictly a holding time from the
  fill. This is the engine's existing rule for every strategy and mode
  (`aurum/execution/engine.py`), and the MT5 tester mirror uses it too.
- **The signal timestamp is persisted** with the intent (`execution/intents.py`, SQLite).
  After a restart the deadline is rebuilt from it. A position found past its deadline is
  closed at the first step; it never gets a fresh 180 minutes.
- **An entry cannot fill late.** `OrderManager` refuses intents older than
  `max_intent_age_seconds` (60 s), and a market order with no quote within
  `max_quote_wait_seconds` (60 s) is rejected.
- **Not changed per strategy.** A future strategy with pending orders that can stay unfilled
  for meaningful time must specify a separate, fill-based position timeout. This global rule
  is not changed retrospectively.
- Option (a), measuring from the fill, was not chosen.

## 1. Identity

- Name: `trend_pullback`
- Family: `trend_pullback`
- Variant: `m15` (enter on the M15 close) and `m5` (M15 setup + M5 trigger). These are two
  variants of one declared family search; sections 6 and 7 differ, everything else is the
  same.
- Version: `0.2.0` (approved 2026-10-10, commit `b9baf4c`)
- Author: Claude (draft) for Andrei Neagoe
- Date: 2026-10-10

## 2. Hypothesis

- **Economic rationale.** During the London morning and the London–New York overlap, gold's
  hourly trend comes from flows that last for hours: macro repositioning after data and rates
  moves, systematic trend followers, and large orders executed over time (TWAP/VWAP).
  Short-term counter-trend traders and liquidity providers sell into strength (buy into
  weakness), which produces 15-minute pullbacks inside the hourly trend that the slower flow
  then resumes. The trade is on the slower flow's side, entered after the pullback has shown
  it resumed.
- **Why it should persist after costs.**
  - Cost per round trip at 0.01 lot is about 16–21 points: median decision-time spread 6–7
    points (reports/phase_2.md §5), commission 4.50 USD per lot = 4.5 points, and 5 points of
    slippage on the market entry and on a stop exit.
  - Stops are 500–2,000 points (section 8), so costs are about 1–4% of the risk (R). The idea
    needs an average gross result above about +0.04R per trade. This is a hypothesis, not an
    expected number.
- **Falsification.** Concatenated out-of-sample net expectancy ≤ 0 USD/trade, or ≤ 0 in 2 or
  more of the 4 out-of-sample folds (section 16). Either kills version 0.x.

## 3. Data, time and indicator semantics

All bars are canonical bid bars, keyed by open time and visible at
`close_time = open_time + timeframe`. A bar is **completed at T** if `close_time ≤ T`. Nothing
with `close_time > T` is used at T. Higher timeframes are as-of joined on close time.

| Timeframe | Fields used | Bar used at decision time T |
|---|---|---|
| H1 | close | `h0` = the H1 bar with the largest `close_time ≤ T` |
| M15 | high, low, close | `b0` = the M15 bar with the largest `close_time ≤ T` |
| M5 | high, low, close (variant m5 only) | `m0` = the M5 bar with `close_time = T` |
| Ticks | bid, ask | Execution only (simulator, broker) |

**Notation.**

- `bk` (`hk`, `mk`) is the bar k positions before `b0` (`h0`, `m0`) in that timeframe's bar
  sequence.
- **"Previous bar"** = the bar whose `open_time` equals this bar's `open_time` minus one
  timeframe. If that bar does not exist (data gap), any rule that needs it is false.
- **"Consecutive"** = each bar is the previous bar of the next.
- Rules are written for a **long**. A short mirrors every comparison: `>` ↔ `<`, `≤` ↔ `≥`,
  low ↔ high, round down ↔ round up.
- All comparisons are exact float64 comparisons of unrounded values, with no tolerance.
  "Strictly" means equality fails.
- **Rounding to the price grid** (stop, target) removes float noise first:
  `q = round(x / point, 6)`, then `floor(q)` or `ceil(q)` as the rule says, × `point`.
  Distances in points are `round((a − b) / point)`, an integer.
- **Searched parameters** appear by name:
  - `EMA_H1` = EMA(`h1_ema`) of H1 closes (default 50, grid {30, 50, 80});
  - `stop_atr_mult` (default 0.5);
  - `target_r` (default 1.5).
  The worked example (section 17) uses the defaults.

**Indicators.** Each value is computed at a given bar over the **500 completed bars of that
timeframe ending at that bar**:

- `EMA(n)`: seeded with the simple mean of the first n closes of the window, then
  `e = e_prev + 2/(n+1) × (close − e_prev)` up to the window's last bar.
- `ATR(14)`: Wilder's ATR as `aurum.features.indicators.atr` defines it (true range from the
  window's second bar, seeded with the mean of the first 14 true ranges, then
  `atr = (atr × 13 + tr) / 14`), on **M15** bars.
- A value whose window has fewer than 500 bars does not exist, and a rule that needs it is
  false.

**Warm-up, by bar count.** Every indicator window needs 500 bars (`h1_ema` ≤ 80 < 500):

- **m15:** 503 completed H1 bars (500 for `EMA_H1(h3)` + 3 for the slope) and 507 M15 bars
  (500 for `EMA50(b7)` + `b0…b7`).
- **m5:** the setup bar `b_s` can be up to `b2` at the trigger time, and its regime can use the
  H1 bar before `h0`. So 504 H1, 509 M15, and 3 M5 bars (`m0`, `m1`, and up to 2 M5 bars since
  the last M15 close).
- **Both variants load the larger counts:** 504 H1, 509 M15 and 3 M5 bars before the first
  decision.

The backtest must load warm-up **by bar count**. Today the runner uses calendar
`backtest.warmup_days`; switching to bar count is an implementation requirement. Warm-up bars
produce no decision, signal, trade or metric. Evaluation starts at the period's first decision
time.

## 4. H1 context (regime rule)

- `slope = EMA_H1(h0) − EMA_H1(h3)`, in price units (USD/oz) over 3 H1 bars in the bar
  sequence.
- **Long regime** at T: `close(h0) > EMA_H1(h0)` strictly AND `slope > 0.00` strictly.
- **Short regime:** `close(h0) < EMA_H1(h0)` AND `slope < 0.00`.
- Otherwise there is no regime: equal values count as no regime, and there is no signal.
- Volatility is bounded only through the stop rules in section 8.

## 5. M15 setup

Evaluated at an M15 close T (`b0` closes at T). `b0` is called the **setup bar**. A **long
setup** needs all of:

1. **Consecutive:** `b0…b7` are 8 consecutive M15 bars. Otherwise there is no setup, counted
   `data_gap`.
2. **Touch** (the wick counts, equality counts): `low(bk) ≤ EMA20(bk)` for at least one k in
   0…3. The window includes the setup bar `b0`.
3. **No close past structural invalidation:** `close(bk) > EMA50(bk)` strictly for every k in
   0…7. The window includes `b0` and is the same 8 bars as the swing in section 8.
4. **Resumed:** `close(b0) > EMA20(b0)` strictly.
5. **Regime:** long regime at T (section 4).

`EMA20` and `EMA50` are M15 EMAs of close. Only one direction can qualify at T, because the
regime has one direction.

## 6. M5 trigger (variant m5 only; variant m15: not applicable)

- **Arming.** A setup whose setup bar closes at T0 arms a trigger window over the M5 bars with
  `T0 < close_time ≤ T0 + 30 min` (closes at T0+5 … T0+30; the bar closing exactly at T0+30
  qualifies).
- **Session limit.** The window is truncated at the session end: an M5 bar can trigger only if
  its close T is a decision time in session (section 12). An armed setup expires at the
  session end and never carries over to another session or day.
- **Setup bars are decision times.** A setup bar's close T0 must itself be a decision time in
  session (`session start ≤ T0 < session end`, section 12).
- **Replacement.** A newer setup (any direction) whose setup bar closes inside the window
  replaces the armed one for M5 bars closing **after** its close; its own window starts there.
  At an instant T that is both an M5 close and a new setup's close, the old setup is still the
  one evaluated at T (expiry, then trigger), and the new setup cannot trigger at T. An M15
  close that is not a setup does not cancel or replace anything.
- **At each M5 close T inside the window, in this order:**
  1. **Expiry:** if `close(m0) ≤ EMA50(b_s)`, the setup expires and `m0` cannot trigger.
     `b_s` is the armed setup bar; its EMA values are fixed at its close.
  2. **Trigger:** `m1` is the previous M5 bar and exists, `close(m0) > high(m1)` strictly,
     `close(m0) > EMA20(b_s)` strictly, and the H1 regime as of T (section 4, with `h0` the
     H1 bar used at T) is still long.
- **One use only.** A setup triggers at most once: at the first M5 bar that meets the trigger,
  whether or not the risk engine approves the signal.
- **Stateless.** The armed setup is derived from bars only, never stored. At T it is the newest
  setup bar with `close_time` in `[T − 30 min, T − 5 min]`, provided no M5 bar between its close
  and T expired it or triggered it. A restart re-derives the same state.

## 7. Entry

- **Order type:** market. `Signal.time = T`, `data_as_of = T`, `Signal.entry_price = None`.
- **Variant m15:** at an M15 close T where a setup holds at `b0` AND `b1` is the previous bar
  AND `close(b0) > high(b1)` strictly.
- **Variant m5:** at the close T of the trigger M5 bar (section 6).
- **Fill:** a market buy fills at the **ask** (a sell at the bid) of the first quote at or after
  `T + latency`, plus entry slippage (ADR-0009 §1–2).
  - On bar-only days the quote is the next M1 open at or after arrival: bid = the open, ask =
    open + the bar's minimum spread + `bar_spread_extra_points` (ADR-0009 §4).
  - On demo, the broker fills.
  - The fill is **never** the decision bar's close.
- **`ref`** = the decision bar's close (bid: `close(b0)` for m15, `close(m0)` for m5). It is
  used only to compute the stop distance, the min/cap checks and the target. The risk engine
  sizes from its own reference, the executable quote (ADR-0010).

## 8. Stop loss

- **Swing window:**
  - Variant m15: the M15 bars `b0…b7`, including the setup/confirmation bar `b0`.
  - Variant m5: the 8 M15 bars `b0…b7` completed at T, **plus** every M5 bar with
    `close_time(b0) < close_time ≤ T`, which includes the trigger bar `m0`. These cover the
    part of the current M15 bar that has elapsed, so the stop is beyond all price seen in the
    window.
- **Price rule** (long): `x = min low over the swing window − stop_atr_mult × ATR14_M15(b0)`,
  `stop = floor(round(x / point, 6)) × point`.
  - `ATR14_M15(b0)` is the ATR of the latest completed M15 bar at T.
  - Short: max high + `stop_atr_mult` × ATR, rounded up with `ceil`.
- **Distance:** `d = round((ref − stop) / point)` points (an integer; both are multiples of
  `point` up to float noise).
- **Minimum:** `d ≥ max(500, trade_stops_level + 10)` (`trade_stops_level` is 0 today).
  Otherwise no signal, counted `stop_below_minimum`.
  - Why 500: the minimum is expected to reduce bar-resolution ambiguity. Measured shares of
    ambiguous level exits were 200 points → 6.9%, 300 → 2.9%, 500 → 0.5% on 2026 bar-only
    days, and 500 → 0.11% over 2020–2026 bar-only days (reports/phase_3.md §4, hypothetical
    entries).
  - **It does not guarantee** that the Phase 3b flagged-trade share stays at or under 1%.
    `entry_bar`, `close_bar` and `cancel_bar` flags also count, and only the completed
    worst-/best-case backtests establish the actual share.
- **Always attached broker-side** at order submission.

### 8a. Stop-size cap

| Input | Value | Unit | Source |
|---|---|---|---|
| Reference equity | 10,000.00 | USD | `configs/backtest.yaml` `initial_balance` |
| Account currency | USD | | `configs/broker.yaml` `account_currency` |
| Risk per trade | 0.25 | % of equity | `configs/risk.yaml` `risk_per_trade_pct` |
| Risk budget | 25.00 | USD | 10,000 × 0.25 / 100 |
| Symbol point | 0.01 | price | spec snapshot `20261010T000046Z.json` (USD demo account) |
| `volume_min` | 0.01 | lots | same snapshot |
| `volume_step` | 0.01 | lots | same snapshot |
| Money per price unit per lot | 100.00 | USD per 1.00 price per lot | same snapshot: `tick_value` 1.0 ÷ `tick_size` 0.01 |
| Commission per lot | 2.25 per side / 4.50 round trip | USD | `configs/broker.yaml` |
| Stop slippage | 5 points = 0.05 price | | `configs/broker.yaml` `slippage.points_per_side` (provisional) |
| **Ceiling** | **2,490 points = 24.90 price** | | (25.00 / 0.01 − 4.50 − 0.05 × 100) / 100 = 24.905 → floor |
| **Strategy cap** (D2) | **2,000 points = 20.00 price** | | Leaves room for equity to fall about 20% before cap-sized stops become unaffordable |
| Risk at `volume_min`, stop at the cap | 0.01 × (20.00 × 100 + 4.50 + 0.05 × 100) = 20.095 ≤ 25.00 | USD | |
| Equity floor of the cap | 20.095 / 0.0025 = 8,038.00 | USD | Below it, the risk engine rejects cap-sized stops |

- **Stop above the cap → no signal.** If `d > 2,000`, there is no signal, counted
  `stop_above_cap`. The stop is never clamped, shortened or moved.
- `stop_below_minimum` and `stop_above_cap` are separate counters, and both are separate from
  risk-engine rejections.
- Changing the cap or any input above is a version bump.

Descriptive, development period, version 0.1.0 rules (m5 swing without the M5 bars), before
the position and daily limits:

| Variant | Stop distance (points) p10 / p50 / p90 / p99 | Below 500 points | Above the cap |
|---|---|---|---|
| m15 | 344 / 699 / 1,545 / 2,896 | 28% | 4.5% |
| m5 | 320 / 651 / 1,501 / 2,950 | 33% | 4.2% |

M15 ATR(14) median was 201–286 points in 2021–2024 and 443 in 2025. Expect more
`stop_above_cap` and fewer `stop_below_minimum` if volatility stays at the 2025 level (section 18).

## 9. Target

- Long: `x = ref + target_r × (ref − stop)`, `target = floor(round(x / point, 6)) × point`
  (toward the entry). Short: `x = ref − target_r × (stop − ref)`, rounded up with `ceil`.
  `target_r` defaults to 1.5 (grid {1.0, 1.5, 2.0}).
- It is set broker-side with the stop at submission. Because the fill is at the ask
  (bid + spread + slippage) and not at `ref`, the realised reward-to-risk is slightly below R.
  That is accepted and measured, not corrected.

## 10. Timeout (signal-to-exit, D6 b)

- `max_hold = 180 minutes`. At expiry the engine closes the position at market (TIMEOUT); the
  close fills on the next quote after latency.
- **Start of the clock:** `Signal.time` (D6 b). The deadline is `Signal.time + 180 min`, rebuilt
  from the persisted signal after a restart, and never restarted (section 0).
- Force-flat (section 12) applies first if it comes earlier.

## 11. Invalidation and exit precedence

- **Open position (long):** invalid when an H1 bar with `close_time > T` (T = the entry decision
  time) is completed with `close(h) < EMA_H1(h)` strictly. It is evaluated only on completed
  H1 bars, never intrabar.
  - The engine wakes at every M5 close, which includes every H1 close, so it closes at market
    at the step of that H1 close, filled on the next quote after latency (INVALIDATION).
  - The slope turning flat does not invalidate.
- **Pending setup (m5):** the expiry rules in section 6.
- **Simultaneous or ambiguous exits:** delegated unchanged to the simulator (ADR-0009 §5:
  stop first when a bar reaches stop and target, `close_bar`/`entry_bar` rules, flags) and,
  on demo, to the broker. The strategy adds no exit rule of its own.

## 12. Session constraints and signal priority

- **Decision times:** T with `session start ≤ T < session end` (`aurum.sessions.clock`) in
  `london_morning` (07:00–11:00) or `ldn_ny_overlap` (13:00–16:30) London, with entries
  allowed. A bar closing exactly at 11:00 or 16:30 is not a decision time.
- **Risk engine gates, unchanged:** cutoff (no entries within 45 min of `force_flat_at`), news
  blackout, spread caps, `max_trades_per_day: 3`, loss budgets, one position at a time.
- **Force-flat** at `min(20:00 London, market close − 15 min)`. Open positions are **not**
  closed at a session end; they exit by stop, target, timeout, invalidation or force-flat.
- **Signal priority:**
  - Each variant runs as its own strategy, in its own backtest run and account. The two
    variants never trade at the same time.
  - Within a variant there is at most one signal per decision time: the regime allows one
    direction, and m5 has one armed setup.
  - While Aurum has a position or pending order, the risk engine rejects any new signal
    (exposure, counted as a risk rejection). No queueing, no reversal.

## 13. Cost assumptions

- **Spread:** real bid/ask from ticks on exact days. On bar-only days, the bar's minimum
  spread + `bar_spread_extra_points` (2), per ADR-0009. Never a constant.
- **Commission:** 2.25 USD per side per lot (4.50 round trip), from `configs/broker.yaml`.
- **Slippage:** 5 points per side, from `configs/broker.yaml` (provisional).
- **Stress:** spread ×1.5 and ×2.0 re-runs are mandatory and both are reported (section 16).

## 14. Parameters and search budget (D4)

| Name | Unit | Default | Justified range | Grid | Why |
|---|---|---|---|---|---|
| `h1_ema` | H1 bars | 50 | 30–80 | {30, 50, 80} | About 1.5–3.5 trading days of trend; shorter is noise, longer reacts after the intraday move |
| `stop_atr_mult` | × M15 ATR(14) | 0.5 | 0.25–1.0 | {0.25, 0.5, 1.0} | Buffer beyond the swing; below 0.25 sits inside normal M15 noise, above 1.0 pushes stops past the cap |
| `target_r` | R | 1.5 | 1.0–2.0 | {1.0, 1.5, 2.0} | Must be reachable within 180 minutes; above 2R rarely is intraday |

- **Trials:** 2 variants × 3 `h1_ema` × 3 `stop_atr_mult` × 3 `target_r` = **54**.
- **One trial** = one backtest run of one grid point of one variant over the whole development
  period (warm-up excluded), recorded in the ledger. Fold statistics are slices of that run by
  entry date, not extra trials.
- **Not counted as trials:** the stress re-runs and the best-case intra-bar replay of a
  selected configuration. They are linked to their trial in the ledger.
- **Frozen:** no value, filter or alternate definition is added after results are seen. Any
  change is a new version and restarts the development evaluation, with the new trials added
  to the count.

Fixed (not searched; a change is a version bump):

| Name | Value | Why |
|---|---|---|
| `h1_slope_bars` | 3 | Three hours of EMA direction |
| `m15_fast_ema` / `m15_slow_ema` | 20 / 50 | Trend zone / structural invalidation |
| `touch_bars` | 4 (b0…b3) | The pullback must be within the last hour |
| `swing_bars` | 8 (b0…b7) | Two hours of structure; the same bars as the invalidation check |
| `atr_period` | 14 (M15, Wilder) | Convention |
| `indicator_window_bars` | 500 | Deterministic EMA/ATR whatever the history length |
| `max_hold_minutes` | 180 | Fits inside a session plus the gap to force-flat |
| `m5_trigger_bars` | 6 (T0 < close ≤ T0 + 30 min) | Two M15 bars |
| `min_stop_points` | 500 | Section 8 (D3) |
| `stop_cap_points` | 2,000 | Section 8a (D2) |

## 15. Expected trade count

- **Raw rule firings** (development period, version 0.1.0 rules, after the stop bounds, before
  one position at a time and 3 trades per day):
  - m15: 1,478 over 253 weeks, about **5.8 per week**;
  - m5: 2,950, about **11.7 per week** (overlapping windows counted separately; 0.2.0's
    replacement and one-use rules lower this).
- These come from a scratch count, not a backtest. Actual trades will be fewer; the daily limit
  caps them at 15 per week.
- **Minimum for evaluation:** ≥ 30 closed trades in each out-of-sample fold and ≥ 200 in the
  concatenated out-of-sample set. Fewer → "insufficient sample", which is not a pass.

## 16. Pre-registered evaluation (D1, D5)

**Periods and sealing (D1, approved).**

- Development: 2020-11-12 … 2025-09-30. Sealed: 2025-10-01 … 2026-10-08 (bar dates, UTC).
- **Before strategy implementation**, tooling must:
  - record the sealed boundaries in machine-readable config;
  - make every normal backtest/report command refuse a range that touches them;
  - require an explicit `--open-sealed`;
  - journal when, why and by whom the period was opened;
  - allow opening once, only after a candidate and its parameters are frozen.
- The sealed result reports the exact-tick subset (2026-06-26 onward) separately from the
  bar-only part.
- During Phase 5 nothing inside the sealed period is inspected: no descriptive counts,
  signals, trades or P&L.

**Folds.** Anchored, by entry date (UTC):

| Window | Dates |
|---|---|
| Training only | 2020-11-12 … 2021-09-30 |
| OOS fold 1 | 2021-10-01 … 2022-09-30 |
| OOS fold 2 | 2022-10-01 … 2023-09-30 |
| OOS fold 3 | 2023-10-01 … 2024-09-30 |
| OOS fold 4 | 2024-10-01 … 2025-09-30 |

- **Selection:** fold k uses the grid point with the highest net expectancy per trade over all
  development trades before fold k, among points with ≥ 100 such trades. Ties go to the
  default point, then to the lexicographically smallest
  (`h1_ema`, `stop_atr_mult`, `target_r`).
- **Concatenated OOS** = each fold's trades from its selected point.
- **Frozen configuration** for Phase 6 = the same rule over the whole development period.
- No purge or embargo is needed: no trade spans midnight UTC, and folds start at 00:00 UTC.

**Reported** for each variant:

- net and gross expectancy (USD/trade and R/trade), net profit factor, max drawdown, trade
  count, holding times;
- spread / commission / slippage attribution;
- splits by fold, year, month, session and direction;
- `stop_below_minimum`, `stop_above_cap`, `data_gap` and risk-rejection counts. One
  `data_gap` = a decision time T at which a bar the variant may read at T is missing (m15: the
  M15 closes T−105 min … T; m5: the M5 closes T−30 … T and the M15 closes T−135 … T).
  Diagnostic only; a gap changes no rule (clarified 2026-10-10, see the change log);
- the Phase 3b summary (worst/best replay);
- the total trial count;
- the null baseline (`reference_fixed_time`, docs/STATUS.md) side by side.

Win rate is reported but is **not** a criterion.

**Measurement definitions** (used by every threshold):

- **Trade P&L** = the booked net P&L in USD (profit + commission, all costs included) from the
  trades table of the trial run it came from.
  - Each trial run starts at 10,000 USD on 2020-11-12 and is sized by the risk engine on its
    own equity.
  - Nothing is rescaled, and walk-forward slices keep their run's sizing.
- **Slicing.** A trade belongs to a fold, month, session and direction by its **entry decision
  time** (UTC). Session = the session of that decision time.
- **Net expectancy** = Σ trade P&L ÷ number of closed trades (USD/trade).
- **Net profit factor** = Σ positive trade P&L ÷ |Σ negative trade P&L|.
- **Max drawdown (closed-trade):** order the concatenated OOS trades by exit time and take the
  cumulative sum of trade P&L, starting at 0. It is the largest fall from a running peak to a
  later value, in USD.

**Pass thresholds (D5)**, per variant, on the concatenated OOS trades. **All** must hold;
"insufficient sample" anywhere is a **fail**.

1. **Sample:** ≥ 30 closed trades in every OOS fold and ≥ 200 in total.
2. **Positive after costs:** net expectancy > 0 USD/trade AND aggregate net P&L > 0.
3. **Chronological stability:** net expectancy > 0 in ≥ 3 of the 4 OOS folds.
4. **Profit factor:** net profit factor ≥ 1.10.
5. **Cost stress:** net expectancy > 0 in the spread ×1.5 re-run of the same selected points.
   The ×2.0 re-run is reported, not gated.
6. **Drawdown:** max drawdown ≤ 1,000 USD. That is 10% of reference equity, about half of the
   1,962 USD drop (19.6%) at which cap-sized stops become unaffordable.
7. **No single month:** aggregate net P&L > 0 after removing all trades of the calendar month
   (UTC, by entry) with the largest net P&L.
8. **Not one direction:** long and short each have ≥ 30 trades and net expectancy ≥ 0.
9. **Not one session:** `london_morning` and `ldn_ny_overlap` each have ≥ 30 trades and net
   expectancy ≥ 0.
   - For 8 and 9, ≥ 0 rather than > 0 is deliberate: "not dependent" means the other side
     does not lose money; a flat side passes.
10. **Neighbourhood:**
    - The frozen point's one-step neighbours are the points ±1 grid step on exactly one
      parameter: 6 for an interior point, 5 with one parameter at an edge, 4 with two, 3 at a
      corner.
    - Each neighbour is judged on its **own trial run**, at a fixed point (not walk-forward),
      using its trades in folds 1–4.
    - At least ⌈2/3 × n⌉ of the n neighbours must have net expectancy > 0: 4 of 6, 4 of 5,
      3 of 4, 2 of 3.
11. **Intra-bar:** `phase_3b_required` is false for the frozen configuration (its worst-case run
    and linked best-case replay; ADR-0009 §9).
12. **Deflated Sharpe ≥ 0.95** for the walk-forward OOS result (Bailey & López de Prado, 2014):
    - **Daily return:** `r_d` = (net P&L of trades **exiting** on UTC date d) ÷ 10,000, for
      every market day in folds 1–4 (`configs/data.yaml` calendar); days with no exit = 0.
    - **Sharpe:** `SR` = mean(r) ÷ sample std(r) (ddof 1), per day, not annualised. `T` =
      number of days. `γ3` and `γ4` are the skewness and (non-excess) kurtosis of the same
      series.
    - **Benchmark:** `SR0 = √V × ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N e)))`, with γ = 0.5772
      (Euler–Mascheroni), N = **54** (the whole declared search, conservative), and V = the
      variance of the 54 trials' `SR`. Each trial's `SR` is computed the same way from its own
      run over the same days.
    - **DSR** = `Φ((SR − SR0) √(T − 1) ÷ √(1 − γ3 SR + (γ4 − 1)/4 × SR²))`.
13. **Sealed period untouched:** the ledger shows no run whose range touches it.

M15 vs M5 are compared on OOS net expectancy and max drawdown after costs, never on win rate.
If both pass, both go to Phase 6.

**Sealed-test threshold** (Phase 6, opened once, frozen configuration, definitions as above):

- net expectancy > 0, net profit factor ≥ 1.05, and ≥ 50 trades. Fewer than 50 trades is
  insufficient sample = **fail**;
- the exact-tick subset (2026-06-26 onward) is reported separately; its result is
  information, not a separate gate.

## 17. Decision trace (worked example)

T = **2024-03-01T14:15:00Z**, variant m15, long. 14:15 London (GMT) is in `ldn_ny_overlap`
and outside both news windows that day (13:20–13:45 and 14:50–15:15 UTC). EMAs and ATR use the
500-bar windows of section 3.

**Causality:** every bar used closes at or before T. The H1 bar used closes at 14:00Z, the
newest M15 bar at 14:15Z = T, and `data_as_of = T`.

| Condition | Values | Result |
|---|---|---|
| H1 bar used `h0` (close_time) | 14:00Z ≤ T, close 2051.69 | |
| H1 close > EMA_H1 (strict) | 2051.69 > 2041.705 | true |
| slope = EMA_H1(h0) − EMA_H1(h3), h3 closes 11:00Z | 2041.705 − 2040.248 = +1.457 > 0.00 | true → long regime |
| `b0…b7` consecutive | M15 closes 12:30Z, 12:45Z, …, 14:15Z | true |
| `b0` | close_time 14:15Z: H 2054.54, L 2051.52, C 2054.40 | |
| Touch: some low(b0…b3) ≤ EMA20 | b3 (closes 13:30Z): 2050.09 ≤ 2051.278 | true |
| Hold: every close(b0…b7) > EMA50 (strict) | smallest margin b1 (14:00Z): 2051.69 > 2048.638 (+3.052) | true |
| Resumed: close(b0) > EMA20(b0) | 2054.40 > 2051.679 | true |
| m15 confirmation: b1 previous bar, close(b0) > high(b1) | b1 closes 14:00Z; 2054.40 > 2052.12 | true |
| ATR14_M15(b0) | 2.1402 | |
| Lows b7…b0 (closes 12:30Z … 14:15Z) | 2053.76, 2053.69, 2052.20, 2051.15, **2050.09** (13:30Z), 2050.73, 2050.71, 2051.52 → min 2050.09 | |
| Stop: x = min low − stop_atr_mult (0.5) × ATR; floor(round(x / point, 6)) × point | 2050.09 − 1.0701 = 2049.0199 → 2049.01 | |
| d = (ref − stop) / point | (2054.40 − 2049.01) / 0.01 = 539 | |
| 500 ≤ d ≤ 2,000 | 539 | true |
| Target: x = ref + target_r (1.5) × 5.39; floor(round(x / point, 6)) × point | 2054.40 + 8.085 = 2062.485 → 2062.48 | |
| Final decision | | `Signal(BUY, market, time 14:15:00Z, stop 2049.01, target 2062.48, max_hold 180 min)` |
| Fill | first quote at or after 14:15:00.200Z, at its ask + 5 points slippage (not 2054.40) | |
| Timeout (signal-to-exit) | `Signal.time` + 180 min = 17:15:00Z (D6 b) | |

**Rejected example, same day:** at 10:15Z a 4-bar-only hold check would have fired, but closes
at 08:45Z–09:15Z (inside b0…b7) were below M15 EMA50. Rule 5.3 → no signal. This example is
why version 0.1.0 tied the hold window to the 8 swing bars.

## 18. Known risks / failure modes

- **Volatility regime shift.** In the development period, M15 ATR in 2025 was about 1.5–2.2×
  its 2021–2024 level. If that trend continues, the fixed point bounds bind differently over
  time: more `stop_above_cap`, fewer `stop_below_minimum`. A later period may then see far
  fewer trades than the development folds. (This spec deliberately states nothing measured
  inside the sealed period.)
- **The cap depends on equity.** Below 8,038 USD equity the risk engine refuses cap-sized
  stops. These are reported as risk rejections.
- **Ranging days** with the slope barely above 0 produce whipsaw entries. The threshold is 0.00
  by design (no tuned threshold).
- **Sample overlap.** Consecutive setups in one move are correlated. One position at a time and
  3 trades per day limit this, but trades are not independent.
- **Bar-only development history.** Intra-bar flags are expected to be rare at stops ≥ 500
  points, not guaranteed (section 8). Phase 3b is decided per candidate from the backtests.
- **News.** The blackout windows protect entries, not open positions. A position open across a
  13:30 London release takes the full move to its stop, paid in full on gaps.
- **Implementation prerequisites** (not strategy rules): warm-up by bar count (section 3) and
  sealed-period tooling (section 16).

## 19. Change log

| Version | Date | Change | Reason |
|---|---|---|---|
| 0.1.0 | 2026-10-10 | Initial draft (6f371d2) | Phase 5 start |
| 0.2.0 | 2026-10-10 | Exact time/window/equality semantics; float-safe price-grid rounding; searched parameters by name (`EMA_H1`, `stop_atr_mult`, `target_r`); setup bars must be decision times; same-instant replacement rule; bar-only fill rule (ADR-0009 §4); warm-up 504 H1 / 509 M15 / 3 M5; D6 (a) fully specified; measurement definitions for every threshold (closed-trade drawdown, entry-time slicing, per-side and per-session minimums, neighbour counts 6/5/4/3, exact DSR formula); trace lists all 8 lows; consecutive-bar rule (`data_gap`); m5 window boundaries, session truncation, replacement, one use, stateless derivation; m5 swing includes the M5 bars since the last M15 close; fill vs `ref`; invalidation only on completed H1 bars; exit precedence delegated to the simulator; signal priority; D3 wording; D6 (timeout start); sealing conditions; trial definition; D5 thresholds expanded (aggregate P&L, drawdown, session, neighbourhood rule, DSR, sealed untouched) | Review of 0.1.0 (owner's reviewer, 2026-10-10) and an independent read-only audit of the 0.2.0 text; still draft, no results seen |
| 0.2.0 (approved) | 2026-10-10 | Approval recorded; D6 = (b); timeout wording "signal-to-exit"; status, header and section 0. **No rule changed.** | Owner approval of `b9baf4c` |
| 0.2.0 (clarified) | 2026-10-10 | Defines the `data_gap` diagnostic (section 16, Reported): one per decision time with a missing bar in the variant's read set. **No rule changed.** | Implementation review (2026-10-10) |
