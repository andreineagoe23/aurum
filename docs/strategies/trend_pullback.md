# trend_pullback — strategy spec

> **Status: DRAFT 0.1.0, not pre-registered.** Written 2026-10-10 from
> `docs/STRATEGY_SPEC_TEMPLATE.md`. It becomes binding only when the owner approves it, and the
> decisions in section 0 are part of that approval. No strategy code exists yet. No backtest,
> P&L or outcome of these rules has been computed. The only data used are the descriptive
> counts in sections 8a and 15, all from the development period (bar close ≤ 2025-09-30).

## 0. Decisions for the owner (part of approval)

| # | Decision | Proposed | Alternatives |
|---|---|---|---|
| D1 | Sealed final-test period | 2025-10-01 … 2026-10-08, never used in search | Shorter (6 months); or seal only data after 2026-10-08 |
| D2 | Stop-size cap | 2,000 points (ceiling 2,490) | Closer to the ceiling (less headroom after losses) |
| D3 | Minimum stop | 500 points (keeps intra-bar ambiguity under the 1% Phase 3b trigger) | 300 points (more trades, more flagged trades) |
| D4 | Varied parameters (27 grid points per variant, 54 trials) | H1 EMA, stop ATR multiple, target R | A different subset |
| D5 | Pass thresholds (section 16) | As stated | Stricter or looser |

## 1. Identity

- Name: `trend_pullback`
- Family: `trend_pullback`
- Variant: `m15` (enter on the M15 close) and `m5` (M15 setup + M5 trigger). Both are in this
  spec; everything except section 6 and the entry decision time is identical.
- Version: `0.1.0` (draft)
- Author: Claude (draft) for Andrei Neagoe
- Date: 2026-10-10

## 2. Hypothesis

- **Economic rationale.** During the London morning and the London–New York overlap, gold's
  hourly trend comes from flows that last for hours: macro repositioning after data and rates
  moves, systematic trend followers, and large orders executed over time (TWAP/VWAP). Short-term
  counter-trend traders and liquidity providers sell into strength (buy into weakness). That
  produces 15-minute pullbacks inside an hourly trend, which the slower flow then resumes. The
  trade is on the side of the slower flow, entered after a pullback has shown it resumed.
- **Why it should persist after costs.**
  - Cost per round trip at 0.01 lot is about 16–21 points of price: median spread 6–7 points
    at decisions (reports/phase_2.md §5), commission 4.50 USD per lot = 4.5 points, and
    5 points of stop slippage when the stop is hit.
  - Stops are 500–2,000 points (section 8), so cost is about 1–4% of the risk (R). The idea
    needs an average gross result above about +0.04R per trade to survive costs.
  - This is a hypothesis to test, not an expected number.
- **Falsification.** Concatenated out-of-sample net expectancy ≤ 0 USD/trade, or ≤ 0 in 2 or
  more of the 4 out-of-sample folds (section 16). Either kills version 0.x of the idea.

## 3. Data used

Bars are keyed by open time and visible at `close_time = open + timeframe`. Higher timeframes
are as-of joined on close time. All prices are bid (canonical bars).

| Timeframe | Fields used | Visible at decision time T |
|---|---|---|
| H1 | close (EMA), high/low/close (none in v0.1) | Last H1 bar with close_time <= T |
| M15 | open, high, low, close | Last M15 bar with close_time <= T |
| M5 | high, close (variant m5 only) | Last M5 bar with close_time <= T |
| Ticks | bid, ask | Execution only (simulator, broker) |

**Indicator definitions** (deterministic whatever the history length):

- `EMA(n)` of a series at bar b is computed over the **500 completed bars ending at b**. It is
  seeded with the simple mean of the first n of those 500 values, then
  `e = e_prev + 2/(n+1) × (x − e_prev)`. Fewer than 500 bars available → no signal.
- `ATR(14)` is Wilder's ATR as `aurum.features.indicators.atr` defines it, over the same
  500-bar window.
- **Warm-up:** 500 H1 bars is about 22 trading days, so the backtest needs ≥ 35 calendar days
  of warm-up for this strategy (the default `backtest.warmup_days` is 10). This is an
  implementation requirement.

Notation, for a long; a short mirrors every comparison:

- `b0` = the M15 bar that closed at T, `b1` the one before it, and so on.
- `EMA20(bk)` and `EMA50(bk)` are M15 EMAs of close at bar bk.
- `h0` = the H1 bar used at T.

## 4. H1 context (regime rule)

- **Long regime** at T: `close(h0) > EMA50_H1(h0)` AND
  `EMA50_H1(h0) − EMA50_H1(h0 − 3 bars) > 0.00`.
- **Short regime** at T: `close(h0) < EMA50_H1(h0)` AND
  `EMA50_H1(h0) − EMA50_H1(h0 − 3 bars) < 0.00`.
- Otherwise: no regime, no signal.
- Volatility is bounded through the stop rules in section 8: stops below 500 points or above
  the cap are refused. There is no separate ATR band in v0.1.

## 5. M15 setup

Evaluated at every M15 close T inside a session (section 12). A **long setup** at b0 needs all
three:

1. **Pullback into the trend zone:** some bar among b0…b3 has `low(bk) <= EMA20(bk)`.
2. **No close past structural invalidation:** every bar among b0…b7 has
   `close(bk) > EMA50(bk)`. These are the same 8 bars whose low sets the stop in section 8.
3. **Resumed:** `close(b0) > EMA20(b0)`.

**Short setup:** `high(bk) >= EMA20(bk)` for some bk in b0…b3, `close(bk) < EMA50(bk)` for all
of b0…b7, and `close(b0) < EMA20(b0)`.

**The regime must agree:** a long setup counts only in a long regime, a short setup only in a
short regime.

## 6. M5 trigger (variant m5 only; variant m15: not applicable)

- A setup at the M15 close T0 **arms** a trigger for the next 6 completed M5 bars (closes at
  T0+5 … T0+30 min). A new setup in the same direction re-arms the window from its own close.
- **Trigger (long)** at the close T of an M5 bar m0 inside the window:
  - `close(m0) > high(m1)`, where m1 is the previous M5 bar;
  - `close(m0) > EMA20(b_setup)`, where b_setup is the setup bar;
  - the H1 regime (section 4), evaluated as of T, is still long.
- **Expiry:** the setup is dropped after the 6th M5 bar, or at the first M5 close
  `<= EMA50(b_setup)` (long; short mirrored), whichever comes first.

## 7. Entry

- **Order type:** market.
- **Variant m15:** at the M15 close T of a setup bar that also has `close(b0) > high(b1)`
  (long) or `close(b0) < low(b1)` (short). This is the same "close beyond the previous bar"
  confirmation as the M5 trigger, on M15.
- **Variant m5:** at the close T of the trigger M5 bar.
- **Fills:** buys fill at the ask, sells at the bid, on the first quote after T + latency
  (ADR-0009).
- **Reference price `ref`** for the stop and target = the decision bar's close (bid).
- **Decision time** = that bar's close.

## 8. Stop loss

- **Price rule** at T:
  - Long: `stop = min(low(b0…b7)) − 0.5 × ATR14_M15(b0)`, rounded **down** to a multiple of
    `point`.
  - Short: `stop = max(high(b0…b7)) + 0.5 × ATR14_M15(b0)`, rounded **up**.
  - For variant m5, b0…b7 are the 8 M15 bars completed at the trigger time.
- **Distance:** `d = |ref − stop| / point` (points).
- **Minimum distance:** `d >= max(500, trade_stops_level + 10)` points (`trade_stops_level` is
  0 today). Otherwise there is no signal, and the setup is counted as `stop_below_min`.
  - Why 500: tighter stops make the worst-case intra-bar rule decide too many trades. Measured
    ambiguous exits: 200 points → 6.9%, 300 → 2.9%, 500 → 0.5% (2026 bar-only days);
    500 → 0.11% over 2020–2026 bar-only days (reports/phase_3.md §4). The Phase 3b trigger is 1%.
- **Always attached broker-side** at order submission.

### 8a. Stop-size cap

| Input | Value | Unit | Source |
|---|---|---|---|
| Reference equity | 10,000.00 | USD | `configs/backtest.yaml` `initial_balance` |
| Account currency | USD | | `configs/broker.yaml` `account_currency` |
| Risk per trade | 0.25 | % of equity | `configs/risk.yaml` `risk_per_trade_pct` |
| Risk budget | 25.00 | USD | 10,000 × 0.25 / 100 |
| Symbol point | 0.01 | price | spec snapshot `20261010T000046Z.json` (USD demo account 504691) |
| `volume_min` | 0.01 | lots | same snapshot |
| `volume_step` | 0.01 | lots | same snapshot |
| Money per price unit per lot | 100.00 | USD per 1.00 price per lot | same snapshot: `tick_value` 1.0 ÷ `tick_size` 0.01 |
| Commission per lot | 2.25 per side / 4.50 round trip | USD | `configs/broker.yaml` |
| Stop slippage | 5 points = 0.05 price | | `configs/broker.yaml` `slippage.points_per_side` |
| **Ceiling** | **2,490 points = 24.90 price** | | (25.00 / 0.01 − 4.50 − 0.05 × 100) / 100 = 24.905 → floor |
| **Strategy cap** | **2,000 points = 20.00 price** | | Chosen (D2): leaves room for equity to fall about 20% before cap-sized stops become unaffordable |
| Risk at `volume_min`, stop at the cap | 0.01 × (20.00 × 100 + 4.50 + 0.05 × 100) = 20.095 ≤ 25.00 | USD | |
| Equity floor of the cap | 20.095 / 0.0025 = 8,038.00 | USD | Below this, the risk engine rejects cap-sized stops |

- **Stop above the cap → no signal.** If `d > 2,000`, there is no signal and the setup is
  counted as `stop_above_cap`. The stop is never clamped, shortened or moved.
- **Changing the cap or any input above** is a version bump.

Descriptive, development period, before the position and daily limits:

| Variant | Stop distance (points) p10 / p50 / p90 / p99 | Below 500 points | Above the cap |
|---|---|---|---|
| m15 | 344 / 699 / 1,545 / 2,896 | 28% | 4.5% |
| m5 | 320 / 651 / 1,501 / 2,950 | 33% | 4.2% |

M15 ATR(14) has risen sharply: median 201–286 points in 2021–2024, 443 in 2025. Expect more
`stop_above_cap` and fewer `stop_below_min` at today's prices (section 18).

## 9. Target

- Long: `target = ref + R × (ref − stop)`. Short: `target = ref − R × (stop − ref)`.
- Rounded to `point` toward the entry (long down, short up), with R = 1.5 (grid {1.0, 1.5, 2.0}).

## 10. Timeout / max holding time

`max_hold = 180 minutes` from the decision time (`Signal.max_hold`). At expiry the position is
closed at market (TIMEOUT). Force-flat (section 12) applies first if it comes earlier.

## 11. Invalidation

- **Open position:** at an H1 close where, for a long, `close(h0) < EMA50_H1(h0)` (short:
  `>`), close at market (INVALIDATION). The slope alone turning flat does not invalidate.
- **Pending setup (variant m5):** the expiry rules in section 6.

## 12. Session constraints

- **Sessions:** `london_morning` 07:00–11:00 and `ldn_ny_overlap` 13:00–16:30 London
  (`configs/sessions.yaml`). Decision times T with session start ≤ T < session end.
- **Cutoff:** no entries within `min_minutes_before_cutoff` (45) of `force_flat_at` (risk
  engine).
- **Force-flat** at `min(20:00 London, market close − 15 min)`. All positions are closed and
  pending orders cancelled.
- **Other:** the risk engine's news blackout (risk.yaml) and `max_trades_per_day: 3` apply
  unchanged. No other restriction.

## 13. Cost assumptions

- **Spread:** real bid/ask from ticks on exact days. On bar-only days, the bar's minimum
  spread + `bar_spread_extra_points` (2), per ADR-0009. Never a constant.
- **Commission:** 2.25 USD per side per lot (4.50 round trip), from `configs/broker.yaml`.
- **Slippage:** 5 points per side, from `configs/broker.yaml` (provisional).
- **Stress:** spread ×1.5 and ×2.0 re-runs are mandatory and both are reported.

## 14. Parameters

Varied (D4): 3 × 3 × 3 = 27 grid points per variant, **54 trials in total**, all in the ledger.

| Name | Unit | Default | Justified range | Grid | Why |
|---|---|---|---|---|---|
| `h1_ema` | H1 bars | 50 | 30–80 | {30, 50, 80} | About 1.5–3.5 trading days of trend; shorter is noise, longer reacts after the intraday move |
| `stop_atr_mult` | × M15 ATR(14) | 0.5 | 0.25–1.0 | {0.25, 0.5, 1.0} | Buffer beyond the swing; below 0.25 sits inside normal M15 noise, above 1.0 pushes stops past the cap |
| `target_r` | R | 1.5 | 1.0–2.0 | {1.0, 1.5, 2.0} | Must reach within the 180-minute hold; above 2R rarely does intraday |

Fixed (not searched; a change is a version bump):

| Name | Value | Why |
|---|---|---|
| `h1_slope_bars` | 3 | Three hours of EMA direction |
| `m15_fast_ema` / `m15_slow_ema` | 20 / 50 | Trend zone / structural invalidation |
| `touch_bars` | 4 | The pullback must be within the last hour |
| `swing_bars` | 8 | Two hours of structure; the same bars as the invalidation check |
| `atr_period` | 14 | Convention |
| `max_hold_minutes` | 180 | Fits inside a session plus the gap to force-flat |
| `m5_trigger_bars` | 6 | 30 minutes, two M15 bars |
| `min_stop_points` | 500 | Section 8 |
| `stop_cap_points` | 2,000 | Section 8a |

## 15. Expected trade count

- **Raw rule firings** (development period, after the stop bounds, before one position at a
  time and `max_trades_per_day: 3`):
  - m15: 1,478 over 253 weeks, about **5.8 per week**;
  - m5: 2,950, about **11.7 per week** (overlapping armed setups counted separately).
- These come from a scratch count with the rules above (the m5 window simplified), not from a
  backtest. Actual trades will be fewer; the 3-per-day limit alone caps them at 15 per week.
- **Minimum for evaluation:** ≥ 30 closed trades in each out-of-sample fold and ≥ 200 in the
  concatenated out-of-sample set. Fewer → "insufficient sample", not pass. The raw counts are
  about 230–700 per year, so the minimum should be met. If the m15 variant falls below it after
  the limits, it is reported as such.

## 16. Pre-registered evaluation

Fixed before any result is seen (pending owner approval, D1/D5).

- **Periods.**
  - Development: 2020-11-12 … 2025-09-30.
  - Sealed: 2025-10-01 … 2026-10-08. Never used in search. Opened once, in Phase 6, after all
    choices are frozen.
- **Folds.** Anchored walk-forward over the development period:

  | Window | Dates |
  |---|---|
  | Training only | 2020-11-12 … 2021-09-30 |
  | OOS fold 1 | 2021-10-01 … 2022-09-30 |
  | OOS fold 2 | 2022-10-01 … 2023-09-30 |
  | OOS fold 3 | 2023-10-01 … 2024-09-30 |
  | OOS fold 4 | 2024-10-01 … 2025-09-30 |

  - Each fold is tested with the grid point chosen on all development data before it: the
    highest training net expectancy per trade among points with ≥ 100 training trades, ties
    to the default.
  - No purge or embargo is needed: trades never span midnight, and folds start on a date
    boundary.
- **Metrics:**
  - net and gross expectancy (USD/trade and R/trade), profit factor, max drawdown, trade
    count;
  - spread / commission / slippage attribution;
  - splits by year, session, direction;
  - `stop_above_cap` and `stop_below_min` counts;
  - intra-bar sensitivity (worst and best replay, `phase_3b_required`);
  - the trial count and a Deflated-Sharpe-style correction for the selected configuration;
  - the null baseline (`reference_fixed_time`, STATUS) side by side.
- **Pass (Phase 5 gate), per variant — all must hold:**
  1. net expectancy > 0 USD/trade in ≥ 3 of the 4 OOS folds;
  2. concatenated OOS: net profit factor ≥ 1.10, and net expectancy > 0 at spread ×1.5;
  3. concatenated OOS net P&L > 0 with the best calendar month removed;
  4. long and short OOS net expectancy each ≥ 0;
  5. the default point's 6 grid neighbours (±1 step on one parameter): ≥ 4 have OOS net
     expectancy > 0;
  6. `phase_3b_required` false; ≥ 30 trades per fold and ≥ 200 in total.
- **M15 vs M5:** compared on OOS net expectancy and max drawdown after costs, never on win
  rate.
- **Sealed-test threshold** (Phase 6, opened once, frozen parameters): net expectancy > 0 and
  net profit factor ≥ 1.05 over the sealed period, with ≥ 50 trades.

## 17. Decision trace (worked example)

T = **2024-03-01T14:15:00Z**, variant m15, long. 14:15 London (GMT) is in `ldn_ny_overlap`
and outside both news windows that day (13:20–13:45 and 14:50–15:15 UTC). EMAs and ATR use the
500-bar windows of section 3.

| Condition | Values | Result |
|---|---|---|
| H1 bar used (close_time) | 14:00Z, close 2051.69 | |
| H1 close > EMA50_H1 | 2051.69 > 2041.705 | true |
| EMA50_H1 slope over 3 bars > 0 | 2041.705 − 2040.248 (11:00Z) = +1.457 | true → long regime |
| M15 bar b0 (close_time) | 14:15Z: H 2054.54, L 2051.52, C 2054.40 | |
| Touch: some low(b0…b3) ≤ EMA20 | b3 (13:30Z): 2050.09 ≤ 2051.278 | true |
| Hold: every close(b0…b7) > EMA50 | 12:30Z…14:15Z; smallest margin b4 (13:15Z) 2051.32 > 2048.223 | true |
| Resumed: close(b0) > EMA20(b0) | 2054.40 > 2051.679 | true |
| m15 confirmation: close(b0) > high(b1) | 2054.40 > 2052.12 | true |
| ATR14_M15(b0) | 2.1402 | |
| Stop = min low(b0…b7) − 0.5 × ATR, rounded down | 2050.09 − 1.0701 = 2049.0199 → 2049.01 | |
| Distance d | (2054.40 − 2049.01) / 0.01 = 539 points | |
| d ≥ 500 (minimum) | 539 ≥ 500 | true |
| d ≤ 2,000 (cap) | 539 ≤ 2,000 | true |
| Target (R 1.5), rounded toward the entry | 2054.40 + 1.5 × 5.39 = 2062.485 → 2062.48 | |
| Max hold | expires 17:15Z (before force-flat 20:00 London) | |
| Final decision | | `Signal(BUY, market, stop 2049.01, target 2062.48, max_hold 180 min)` |

Rejected example from the same day: at 10:15Z a 4-bar-only hold check would have fired, but
closes at 08:45Z–09:15Z (inside b0…b7) were below M15 EMA50. Under rule 5.2 → no signal.

## 18. Known risks / failure modes

- **Volatility regime shift.** M15 ATR in 2025 is about 1.5–2.2× its 2021–2024 level, and the
  sealed period has higher prices still. The fixed point bounds bind differently over time:
  more `stop_above_cap`, fewer `stop_below_min`. The sealed test may see far fewer trades than
  the development folds.
- **The cap depends on equity.** After a drawdown below 8,038 USD, cap-sized stops are refused
  by the risk engine. These are reported as risk rejections, not `stop_above_cap`.
- **Ranging days** with an EMA slope barely above 0 produce whipsaw entries. The slope
  threshold is 0.00 by design (no tuned threshold in v0.1).
- **Sample overlap.** Consecutive setups in one move are correlated. One position at a time
  and 3 trades per day limit this, but fold results are not independent trades.
- **Bar-only history.** The whole development period is bar-only. Intra-bar flags are expected
  to stay under 1% at stops ≥ 500 points, but `entry_bar`/`close_bar` flags add to that.
  Phase 3b is checked per candidate.
- **News.** The fixed blackout windows protect entries, not open positions. A position open
  across a 13:30 London release takes the full move to its stop (paid in full on gaps).

## 19. Change log

| Version | Date | Change | Reason |
|---|---|---|---|
| 0.1.0 | 2026-10-10 | Initial draft | Phase 5 start; awaiting owner approval (section 0) |
