# vwap_reversion — strategy spec

> **DRAFT — NOT FROZEN — NOT IMPLEMENTED — NO TRIALS AUTHORISED.**
>
> - Written by Claude from `docs/STRATEGY_SPEC_TEMPLATE.md` at the owner's request
>   (2026-10-11: "documentation-only draft of vwap_reversion 0.1.0").
> - **No market data was read for this draft**: no bars, ticks, feature stores, trade tables,
>   ledger rows or symbol snapshots, and no feasibility count was run. Every number below is a
>   rule choice, arithmetic, or a figure quoted from a committed document (cited where used).
> - Nothing here is approved. Decisions V1–V15 (section 0) are open for review. No code, config
>   change, feasibility diagnostic, trace, trial or ledger write is authorised by this draft.
> - **Written after two rejected families.** The author has read the trend_pullback 0.2.0 and
>   opening_range_breakout 0.1.0 development results, including their splits. Section 18
>   lists what was seen and which choices it could have influenced.

## 0. Decisions open for review

None is data-informed in the sense of the ORB spec (no count or P&L was computed for this
family). "Prior results" marks a choice the author made knowing the earlier families' results.

| # | Decision | Proposed | Basis | Prior results? |
|---|---|---|---|---|
| V1 | Mean | Tick-weighted session mean of M5 typical bid prices, `TWV` (section 3); not a traded-volume VWAP | Only weight the data has (section 3) | No |
| V2 | Regime | H1 efficiency ratio over 12 bars `≤ er_max` (section 4) | Planned "non-trending" row; avoids reusing trend_pullback's EMA rule | No |
| V3 | Structural level | The setup bar makes a strict new extreme of its session (section 5) | Fixed before the session's later bars; no lookback parameter | No |
| V4 | Trigger | Close beyond the previous bar's far side, on the variant's timeframe (section 6) | Objective rejection confirmation, identical in both variants | No |
| V5 | Stop buffer | 25 points beyond the setup extreme (section 8) | Recorded per-quote spread p99 in the sessions (20–24 points) | No |
| V6 | Stop minimum | `max(500, trade_stops_level + 10)` points; below it **no signal** (section 8) | Bar-resolution ambiguity (reports/phase_3.md §4), not convenience | No |
| V7 | Stop cap | 2,000 points (ceiling 2,490) (section 8a) | Affordability arithmetic, equity floor 8,038 USD | No |
| V8 | Minimum reward:risk | `d_T ≥ d_S` (section 9) | Cost geometry (section 13) | No |
| V9 | Timeout | 90 minutes from `Signal.time` (section 10) | Calendar geometry: no session overlap, no 08:30 New York or FOMC statement reached | Yes (ORB exits were mostly timeouts) |
| V10 | Use per session | First trigger spends the session-date (section 6) | Limits within-session search and dependence | No |
| V11 | Grid | `k_dev` {1.0, 1.5, 2.0} × `er_max` {0.30, 0.50}: 6 per variant, 12 in all (section 14) | Two dimensions, owner's budget | No |
| V12 | News | Blackout instants are not trigger times; a setup bar touching a blackout window is not a setup (section 12) | Information-driven moves are outside the hypothesis | No |
| V13 | Accumulation | First setup bar closes ≥ 60 min after the session anchor (section 5) | TWV over ≥ 12 M5 bars | No |
| V14 | Phase 5 stop rule | If neither variant survives, the three-family lab ends (section 16) | Charter kill criterion | Yes |
| V15 | Trial accounting | N = 66 + new research trials actually recorded (section 16) | Existing ORB §14 / O6 rule | No |

## 1. Identity

- Name: `vwap_reversion`
- Family: `vwap_reversion` (declared in `aurum.settings.StrategyFamily`; placeholder config
  `configs/strategies/vwap_reversion.yaml` since the Phase 0 scaffold, `50d2b83`)
- Variants: `m15` (trigger on M15 bars) and `m5` (the same M15 setup, trigger on M5 bars).
  Only the trigger timeframe differs (section 6); every other rule is identical.
- Version: `0.1.0` (draft)
- Author: Claude (draft) for Andrei Neagoe
- Date: 2026-10-11

## 2. Hypothesis

**Provenance.** Two layers with different standing:

- **Planned before any result:** the broad family. MASTER_PROMPT §7 Phase 5 lists "VWAP /
  structure reversion: non-trending H1 regime; M15 deviation from session VWAP into a
  structural level; M5 rejection candle". It was written, and the config scaffolded, before
  any backtest existed.
- **Designed now, after two failed families:** every detailed rule in sections 3–14. These
  are new choices, not the pre-results plan. They are not an unseen-data test of the planned
  idea; the cumulative DSR (section 16) counts the earlier 66 trials for that reason.

**Economic rationale — a hypothesis, not evidence.**

- Within a session, some price moves come from order flow that carries no new information:
  an order executed faster than the book refills, position squaring, stops triggered in
  sequence. Dealers who absorb such flow charge for it by quoting away from the recent mean,
  and are expected to unwind toward it afterwards.
- Execution algorithms benchmarked to a session average price tend to buy below that average
  and sell above it, which, if it happens, pushes price back toward it.
- **Nothing in this repository shows that either mechanism operates in XAUUSD, at Fusion
  Markets, or in this dataset.** Spot gold is traded over the counter; no consolidated traded
  volume exists, and the data hold only broker quote activity (section 3). The proxy mean
  below is a hypothesis about where such flow would revert to, not a measured benchmark.
- **Where it should not work:** trending conditions (excluded by section 4) and moves caused
  by scheduled releases (section 12.3). Unscheduled news is not excluded and is a known
  failure mode (section 18).

**Why it might persist after costs.** Section 13 gives the cost and geometry arithmetic. It
shows that costs are 0.8–5.4% of the stop distance at the allowed distances; it does not show
that the hit rate exceeds break-even.

**Falsification.** Concatenated out-of-sample net expectancy ≤ 0 USD/trade, or ≤ 0 in 2 or
more of the 4 out-of-sample folds (section 16). Either kills version 0.x.

## 3. Data, time, the TWV proxy, warm-up

All bars are canonical **bid** bars, keyed by open time, visible at
`close_time = open_time + timeframe`. A bar is **completed at T** if `close_time ≤ T`.
Nothing with `close_time > T` is used at T.

| Timeframe | Fields | Bars read at decision time T |
|---|---|---|
| H1 | close | the 13 newest completed H1 bars at the setup close `Ts` (section 4) |
| M15 | high, low, close | the 100 newest completed M15 bars at `Ts` (ATR); the session's M15 bars up to T (setup, structure; trigger for m15) |
| M5 | high, low, close, `tick_count` | the session's M5 bars up to T (TWV, both variants; trigger for m5) |
| Ticks | bid, ask | none; execution only |

**Notation and conventions** (as trend_pullback and ORB section 3):

- Rules are written for a **long** (fading a move down). A short mirrors every comparison
  (`>` ↔ `<`, `≥` ↔ `≤`, high ↔ low, round down ↔ round up), except where written out.
- Comparisons are exact float64 comparisons of unrounded values. "Strictly" means equality
  fails.
- Rounding to the price grid: `q = round(x / point, 6)`, then `floor(q)` or `ceil(q)` as the
  rule says, × `point`. Distances in points are `round((a − b) / point)`, an integer.
- **Session anchor** `A(s, D)` = the start of session `s` on London trading date D, from
  `configs/sessions.yaml` (Europe/London wall clock, DST via `zoneinfo`, through
  `TradingCalendar`). Today's values: `london_morning` 07:00–11:00 and `ldn_ny_overlap`
  13:00–16:30 London; the config is the source. Both start on whole hours, so `A` is aligned
  to M5, M15 and H1 in every DST regime.
- **Previous bar** of a bar = the bar of the same timeframe whose `open_time` is one timeframe
  earlier. If it does not exist, any rule needing it is false and the instant counts as a
  `data_gap` (section 6).

### 3.1 The TWV proxy (V1)

**Definition.** For session `s` of date D and an instant `t` that is an M5 close with
`A < t`:

- `B(t)` = the M5 bars with `open_time ≥ A` and `close_time ≤ t`. It is **complete** when it
  holds every M5 slot `[A, t)`, i.e. `(t − A) / 5 min` bars.
- For each bar `i`: typical price `p_i = (high_i + low_i + close_i) / 3` (bid), weight
  `w_i = tick_count_i`.
- `TWV(t) = fsum(w_i × p_i) / Σ w_i`, with `math.fsum` for the numerator and an integer sum
  for the denominator, so the value is deterministic.
- `TWV(t)` **exists** only if `B(t)` is complete and `Σ w_i > 0`.

**Name.** `TWV` = tick-weighted session mean of bid typical prices. It is called a "VWAP
proxy" in prose; it is **not** a volume-weighted average price of traded gold.

**What the stored weight is — known:**

- Canonical bars carry `tick_count` (`aurum.data.schemas.CANONICAL_BARS`), exposed to
  strategies as `BarSeries.tick_count` (`aurum.features.view`; a null becomes 0).
- It follows the MT5 `tick_volume` convention: the number of ticks that **changed the bid**
  (flag bit 2). Measured in Phase 2: the broker's `tick_volume` equals the bid-changing ticks
  on exact days (ADR-0007; reports/phase_2.md §4).
- **Every development-period bar comes from the broker's M1 bars.** Stored ticks start on
  2026-03-29 (`configs/data.yaml` `backfill.ticks_from`; ADR-0007), so for 2020-11-12 …
  2025-09-30 `tick_count` = the broker M1 `tick_volume`, summed into M5
  (`aurum.data.resample.broker_m1_to_canonical`, `aggregate`).
- `real_volume` is documented as always 0 for this CFD (docs/DATA_DICTIONARY.md); there is no
  other volume field.
- Bars exist only for intervals with at least one tick (`aurum.data.resample`), so an M5 bar
  with `tick_count` 0 is not expected; the rules below handle it anyway.

**What is not known (and not checked for this draft):**

- How Fusion's server generates and filters ticks, and whether that changed between 2020 and
  2025 (liquidity providers, aggregation, throttling). A change would change the weights
  without any change in the market.
- Whether quote-update frequency is related to traded volume in the gold market at all. A
  bid change may come from one quote revision by one provider.
- Whether `tick_volume` on old broker bars was computed from the same feed definition as
  today's (ADR-0007 shows the server's *tick* history is thinned with age; the bars were
  built from the full feed, but the bar-building rule is the broker's).

**Consequences for the rules.** TWV weights prices by quote activity. If activity is
unrelated to traded volume, TWV is an activity-weighted average, close to a time-weighted
one. The hypothesis is then tested against that mean, and section 18 states it.

**Zero weights, gaps and resets.**

- A bar with `w_i = 0` contributes nothing to TWV and counts one `zero_weight_bar`
  (diagnostic, once per bar per session).
- `Σ w_i = 0` → TWV does not exist; every rule needing it is false (`twv_undefined`).
- A missing M5 slot in `[A, t)` → `B(t)` is incomplete; this is a `data_gap` for every later
  instant of that session-date (the slot stays in the read set), as in ORB section 6.
- **Session reset.** Each session has its own TWV, starting at its own `A`. Nothing carries
  over between sessions or dates. Bars before `A` (including the Asian hours and, for
  `ldn_ny_overlap`, the London morning) are never in `B(t)`.

### 3.2 ATR

`ATR(Ts)` = `aurum.features.indicators.atr(high, low, close, 14)` over the **100 newest M15
bars with `close_time ≤ Ts`**, Wilder's ATR exactly as the risk engine computes it at a
decision (`aurum.execution.engine.ATR_BARS`, `risk.yaml` `atr_period` 14). Fewer than 15
bars → no ATR → no setup. It is the volatility unit of the setup (section 5).

### 3.3 Warm-up and history (by bar count)

- Strategy warm-up: `warmup_bars = {H1: 13, M15: 100}` (section 4 and 3.2). M5 needs none:
  TWV uses only bars of the same session.
- The engine's own history is 100 M15 bars (`engine.HISTORY_BARS`), so the merged
  requirement is the same `{H1: 13, M15: 100}`.
- Warm-up bars produce no decision, signal or metric. The evaluated period starts on
  2020-12-15 (section 16); the history before it lies in 2020-11-12 … 12-14.

**Stateless.** Every rule is a function of the bars and the calendar at T. At each call the
strategy re-walks the session from `A` to T, so a restart reaches the same state, including
whether the session is spent.

## 4. H1 context (regime rule) (V2)

- `ER(Ts) = |close(h0) − close(h12)| / Σ_{i=0..11} |close(h_i) − close(h_{i+1})|`, where `h0`
  is the newest H1 bar with `close_time ≤ Ts` and `h_k` is k bars before it **in the bar
  sequence** (H1 bars exist only for hours with ticks; the window can span the daily break
  or a weekend, as trend_pullback's EMA windows can).
- **Non-trending regime** at `Ts`: `ER(Ts) ≤ er_max`. `er_max` is searched: {0.30, 0.50},
  default 0.30.
- Fewer than 13 H1 bars, or a zero denominator → no regime → no setup.
- **Why ER.** It is bounded in [0, 1], has no smoothing seed, and needs no price-unit
  threshold, so one value means the same in 2021 and 2025. Twelve H1 bars is the preceding
  half-day: for a London setup, mostly the Asian hours; for an overlap setup, mostly the
  London day. These are judgments, not data.
- **Not trend_pullback's rule reversed.** The rejection of trend_pullback 0.2.0 says nothing
  about its EMA regime on its own (ORB spec section 4); ER is used so this family does not
  depend on a component of a rejected family.
- Volatility is bounded only through the stop rules (section 8).

## 5. M15 setup (V3, V13)

A **setup** is an M15 bar `b_s` of session `s` on date D, closing at `Ts`, with (long):

1. **Accumulation:** `A + 60 min ≤ Ts < session end` (today: `Ts` from 08:00 to 10:45 London
   for `london_morning`, from 14:00 to 16:15 London for `ldn_ny_overlap`). Early closes clip
   the session end through the calendar.
2. **Regime:** `ER(Ts) ≤ er_max` (section 4).
3. **Deviation:** `TWV(Ts) − low(b_s) ≥ k_dev × ATR(Ts)` (inclusive). `k_dev` is searched:
   {1.0, 1.5, 2.0}, default 1.5.
4. **Structural level — new session extreme:** `low(b_s) < min low` of every M15 bar of the
   session with `open_time ≥ A` and `close_time < Ts` (strict). With rule 1 there are always
   at least 3 such bars.
5. **Read set complete:** every M5 slot in `[A, Ts)` and every M15 slot in `[A, Ts)` has a
   bar, and `TWV(Ts)` exists.
6. **No release inside the bar:** no instant of `[open_time(b_s), close_time(b_s)]` lies in a
   news blackout window (`NewsBlackout.reason_at` not None; section 12.3).

A short setup mirrors 3 and 4: `high(b_s) − TWV(Ts) ≥ k_dev × ATR(Ts)` and
`high(b_s) > max high` of the earlier session bars.

- **Setup extreme** `X = low(b_s)` (long) or `Y = high(b_s)` (short).
- A bar meeting both the long and the short conditions is **not** a setup
  (`ambiguous_setup`).
- A bar meeting 1 and 3–6 but not 2 counts one `setup_regime_off` (diagnostic of how often
  the regime binds).
- **Why the session extreme.** It is the only level fixed by bars the session has already
  printed, needs no lookback parameter, and is the point where the deviation is largest. A
  prior-day or prior-24-hour extreme is the alternative (V3); it would make setups rarer by
  an amount that cannot be known without counts.
- **Why 60 minutes.** TWV over fewer than 12 M5 bars is dominated by the first bars of the
  session. In normal weeks it also keeps the 08:30 New York release (13:30 London) inside the
  `ldn_ny_overlap` accumulation period rather than in a setup bar.

## 6. Trigger, decision times, spend and diagnostics (V4, V10)

**Variant timeframe.** `m15`: decision bars are M15 bars; `m5`: decision bars are M5 bars.
`d` = the decision-timeframe bar closing at T; `p` = its previous bar.

**Active setup at T.** The setup `b_s` of the same session-date with the largest `Ts < T`,
provided `T ≤ Ts + 30 min`. Every M15 close is checked for a setup, so a newer setup
**replaces** an older one (either side).

**Trigger (long)** at an instant T. All must hold:

1. T is a close of the decision timeframe, `session_at(T)` is `s` with entries allowed
   (`session start ≤ T < session end`; a close exactly at the session end is not a decision
   time), and T is not in a news blackout window (section 12.3).
2. There is an active setup (long) with extreme `X`.
3. **Not broken:** every decision-timeframe bar closing in `(Ts, T]`, including `d`, has
   `low ≥ X`.
4. **Rejection:** `close(d) > high(p)` strictly.
5. **Room to the mean:** `close(d) < TWV(T)` strictly.
6. **Read set complete:** every M5 slot in `[A, T)` and every M15 slot in `[A, T)` whose bar
   closes at or before T has a bar, `p` exists, and `TWV(T)` exists.

A short mirrors 3–5: every bar has `high ≤ Y`; `close(d) < low(p)`; `close(d) > TWV(T)`.

- For `m15`, the first possible trigger is the M15 close 15 minutes after `Ts` (`p` is then
  `b_s`); for `m5`, the M5 close 5 minutes after `Ts` (`p` is the last M5 bar of `b_s`).
- The **expiry is the same 30 minutes** in both variants: 2 M15 decision bars or 6 M5
  decision bars.

**One use per session-date (V10).** The session-date is **spent** at the first instant
meeting trigger rules 1–6, whether that trigger produces a signal (sections 8 and 9) or not.
After that the session gives no signal that date.

**Diagnostic counters** (per variant run; each instant counted once however often the
strategy is called; only while the session-date is unspent unless stated):

| Counter | +1 for each |
|---|---|
| `setups` | setup bar (section 5), counted at its close, whether or not it is later triggered |
| `ambiguous_setup` | bar meeting both the long and the short setup conditions |
| `setup_regime_off` | bar meeting setup rules 1 and 3–6 but not 2 |
| `data_gap` | decision instant meeting trigger rule 1 whose read set is incomplete |
| `twv_undefined` | decision instant meeting rule 1 with a complete read set but `Σ w_i = 0` |
| `zero_weight_bar` | M5 bar with `tick_count` 0 in a read set (once per bar) |
| `blackout_skipped` | instant meeting trigger rules 2–6 at a blackout instant |
| `stop_below_minimum` | spend whose `d_S` is below the minimum (section 8) — at the spend |
| `stop_above_cap` | spend whose `d_S` exceeds 2,000 (section 8a) — at the spend |
| `reward_risk_below_min` | spend with `d_T < d_S` (section 9) — at the spend |

**Session-date status** (exactly one): `signal`, `stop_below_minimum`, `stop_above_cap`,
`reward_risk_below_min`, `no_trigger` (complete session, no spend), `data_gap` (read set
incomplete before any spend), `twv_undefined`, or `no_decision_time`.

## 7. Entry

- **Order type:** market. `Signal.time = T`, `data_as_of = T`, `entry_price = None`.
- **`ref`** = `close(d)` (bid), used only for `d_S`, `d_T` and the bounds.
- **Fill:** unchanged simulator and broker rules (trend_pullback / ORB section 7): a buy
  fills at the ask of the first quote at or after `T + latency`, plus entry slippage; on
  bar-only days, the next M1 open with the bar's minimum spread + `bar_spread_extra_points`
  (ADR-0009). Never at the decision bar's close.

## 8. Stop loss (V5, V6)

**Buffer.** `stop_buffer_points` = **25 points = 0.25 USD/oz**, a fixed constant of this spec,
not linked to `risk.yaml`.

- **Why 25, not ORB's 20.** A short's stop is hit by the ask while `Y` is a bid high. The
  recorded per-quote spread p99 inside the two sessions is 20 (`london_morning`) and 24
  (`ldn_ny_overlap`) points (reports/phase_2.md §5). 25 covers both, so a re-test of `Y` by the
  bid does not stop a short by spread alone. Applied to both sides for symmetry. Those
  statistics come from 2026-06-26 … 10-08, measured before the sealed period existed; no new
  measurement was made.

| | Long | Short |
|---|---|---|
| Unrounded stop | `x = X − 25 × point` | `x = Y + 25 × point` |
| Stop | `floor(round(x / point, 6)) × point` | `ceil(round(x / point, 6)) × point` |
| `d_S` (points) | `round((ref − stop) / point)` | `round((stop − ref) / point)` |

- **Minimum (V6):** `d_S ≥ max(500, trade_stops_level + 10)`; otherwise **no signal**, the
  session-date is spent, `stop_below_minimum` +1. The stop is never widened to reach it.
  - **Reason, stated for this family:** below 500 points the share of trades whose outcome
    the worst-case intra-bar rule decides becomes material on bar-only days (0.39% at 300
    points overall, 2.9% in the 2026 regime; 0.11% at 500; reports/phase_3.md §4). With
    bar-only development data, smaller stops would make the evaluation measure the simulator's
    assumption more than the strategy. This is a measurement constraint, not an economic one.
  - **Consequence:** only setups whose rejection close is at least 475 points above `X` can
    trade. That selects larger extensions mechanically, and may thin the sample (section 15).
  - **Alternative for review:** a floor-widened stop `min(X − 25 points, ref − 500 points)`
    keeps more setups but stops trades later than the structural level; not proposed.
- **Always attached broker-side.** Never clamped, shortened or moved.

### 8a. Stop-size cap (V7)

Inputs as recorded in opening_range_breakout section 8a (same pinned snapshot,
`20261010T000046Z.json`, sha256 `0426cf8c…be0c7ee`). **They must be re-read from the pinned
snapshot at freeze;** the snapshot was not opened for this draft.

| Input | Value | Source |
|---|---|---|
| Reference equity | 10,000.00 USD | `configs/backtest.yaml` `initial_balance` |
| Risk per trade → budget | 0.25% → 25.00 USD | `configs/risk.yaml` |
| Point, `volume_min`, `volume_step` | 0.01 price, 0.01 lot, 0.01 lot | pinned snapshot (as recorded) |
| Money per price unit per lot | 100.00 USD | pinned snapshot: `tick_value` 1.0 ÷ `tick_size` 0.01 (as recorded) |
| Commission | 2.25 per side, 4.50 round trip | `configs/broker.yaml` |
| Stop slippage | 5 points = 0.05 price | `configs/broker.yaml` (provisional) |
| **Ceiling** | **2,490 points** | (25.00 / 0.01 − 4.50 − 0.05 × 100) / 100 = 24.905 → floor |
| **Strategy cap** | **2,000 points** | 0.01 × (20.00 × 100 + 4.50 + 5.00) = 20.095 ≤ 25.00 USD |
| Equity floor of the cap | 8,038.00 USD | 20.095 / 0.0025 |

- **Why 2,000:** it leaves a 19.6% equity drawdown before cap-sized stops become
  unaffordable. It equals the earlier families' cap because the same arithmetic gives the
  same answer, not because it was copied; a reviewer may choose another value below 2,490.
- `d_S > 2,000` → no signal, session spent, `stop_above_cap` +1. Never clamped.

## 9. Target (V8)

- **Target = the TWV at the decision time,** fixed when the signal is made:
  long `target = floor(round(TWV(T) / point, 6)) × point`; short
  `target = ceil(round(TWV(T) / point, 6)) × point` (both rounded toward the entry).
  `d_T = round(|target − ref| / point)`.
- It is set broker-side with the stop. TWV keeps moving after T; the target does not follow
  it.
- **Minimum reward:risk:** `d_T ≥ d_S` (inclusive). Otherwise no signal, session spent,
  `reward_risk_below_min` +1. Reason: section 13; it keeps the break-even hit rate for
  stop-or-target exits at or below 52.7% at the smallest allowed stop.
- **Short-side asymmetry, accepted:** TWV is a bid mean; a short's target is reached when the
  **ask** reaches it, one spread later than a long's. This cost is in section 13.
- Not an R multiple: the hypothesis names the mean as the reversion point, so a fixed R
  target would test a different idea.

## 10. Timeout (V9)

- `max_hold = 90 minutes` from `Signal.time` (the engine's existing signal-to-exit rule). At
  expiry the engine **requests** a market close (TIMEOUT); a request does not guarantee a
  fill, and the broker-side stop stays attached while it is retried.
- **Why 90, from the calendar** (latest trigger times with today's `sessions.yaml`):
  - `london_morning`: latest m5 trigger 10:55 London → deadline **12:25 London**. That is
    before the 08:30 New York release in mismatch weeks (12:30 London) and before
    `ldn_ny_overlap` starts (13:00 London), so a London position's close is requested before
    any overlap decision.
  - `ldn_ny_overlap`: latest m5 trigger 16:25 London → deadline **17:55 London**. That is
    before the 14:00 New York FOMC statement in every week (19:00 London in normal weeks,
    18:00 London in mismatch weeks) and before force-flat at 20:00 London.
  - 120 minutes would give 12:55 and 18:25 London deadlines, reaching the mismatch-week 08:30
    release and the mismatch-week FOMC statement.
- On an early close, force-flat at `market close − 15 min` comes first if it is earlier.
- **Disclosure:** the author knows that most ORB exits were timeouts. 90 is derived from the
  times above, not from those results; the knowledge is still listed in section 18.

## 11. Invalidation

- **Of a setup** (no position yet): expiry (`T > Ts + 30 min`), a decision bar breaking the
  extreme (trigger rule 3), replacement by a newer setup, the session end, or the spend.
- **Of a position: none in 0.1.0.** Exits are stop, target, timeout and force-flat. The stop
  sits beyond the setup extreme; a second "back through the extreme on a close" exit would be
  a second, untested rule. A regime change after entry does not close the position.
- Simultaneous or ambiguous exits: delegated unchanged to the simulator (ADR-0009 §5) and, on
  demo, to the broker.

## 12. Session constraints and signal priority

1. **Decision times:** trigger rule 1. Sessions `london_morning` and `ldn_ny_overlap`, both
   kept (gate 9 needs both; neither is restricted, see section 18).
2. **Risk engine gates, unchanged:** entry cutoff (`min_minutes_before_cutoff` 45 before
   force-flat), news blackout, spread caps (`max_spread_points` 20, `max_spread_atr_fraction`
   0.15 with the 100-bar M15 ATR), `max_trades_per_day` 3, daily and weekly loss budgets, the
   consecutive-loss cooldown, one position at a time.
3. **News (V12).** `aurum.sessions.news.NewsBlackout`, the same object and `risk.yaml`
   windows the risk engine uses (`[start, end)` New York time: 08:20–08:45 and 09:50–10:15
   daily; 13:30–15:00 on FOMC statement dates).
   - A blackout instant is not a trigger time: it neither signals nor spends
     (`blackout_skipped`). As in ORB O3, the strategy never emits a signal the risk engine
     must reject by construction.
   - A bar whose `[open_time, close_time]` touches a window is not a setup (setup rule 6):
     the hypothesis is about uninformed flow, and a scheduled release is information. Bars
     after the window are eligible; a release-driven move that continues past 08:45 or 10:15
     New York is not excluded (section 18).
   - Inside the decision windows only the 10:00 New York window occurs (15:00 London in
     normal weeks, 14:00 London in mismatch weeks); the 08:30 window falls in the overlap
     accumulation period (normal weeks) or before the session (mismatch weeks).
4. **Force-flat** at `min(20:00 London, market close − 15 min)`. Session ends do not close
   positions.
5. **Priority.** Each variant runs as its own strategy, run and account. At most one signal
   per session-date, so at most 2 per date; `max_trades_per_day` 3 never binds. A signal
   while an earlier position or order exists is rejected by the risk engine (exposure), with
   no queueing or reversal.

## 13. Cost assumptions and the cost/geometry argument

- **Spread:** real bid/ask on exact tick days; bar-only days (the whole development period):
  the bar's minimum spread + `bar_spread_extra_points` (2), ADR-0009. Never a constant.
- **Commission:** 2.25 USD per lot per side (4.50 round trip) = 4.5 points of price per
  round trip.
- **Slippage:** 5 points per side (`configs/broker.yaml`, provisional).
- **Stress:** spread ×1.5 and ×2.0 re-runs before any pass (gate 5).

**Geometry, without counts or P&L.** Let `C` = round-trip cost in points: one spread (the
recorded median at M5 decisions is 6 points, reports/phase_2.md §5), commission 4.5, entry
slippage 5, plus 5 more on a stop exit, plus, for a short's target, the extra spread of
section 9. With the median spread, `C` is 15.5 (long target exit) to 26.5 (short target
exit) points; 16–27 below.

- With `500 ≤ d_S ≤ 2,000`, `C` is 0.8%–5.4% of the stop distance.
- For trades that end at the stop or the target, the break-even hit rate is
  `p* = (d_S + C) / (d_S + d_T)`. With `d_T ≥ d_S` (section 9): `p* ≤ (1 + C/d_S) / 2`, which
  is at most 52.7% at `d_S` 500 and 50.4–50.7% at `d_S` 2,000. A wider spread than the median
  raises `C` and `p*`.
- Timeouts and force-flats end between the levels; their share and value cannot be stated
  without data.
- **What this shows:** at the allowed distances costs do not dominate the geometry. **What it
  does not show:** that the hit rate exceeds `p*`, or that enough setups pass the 500-point
  minimum and `d_T ≥ d_S` together. Geometrically, a trade needs `TWV(T) − X ≥ 2 × d_S − 25`
  points, so at `d_S` 500 the mean must be at least 9.75 USD above the extreme. How often that
  holds is unknown (section 15).

## 14. Parameters and search budget (V11)

| Name | Unit | Default | Range | Grid | Why |
|---|---|---|---|---|---|
| `k_dev` | M15 ATR(14) | 1.5 | 1.0–2.0 | {1.0, 1.5, 2.0} | Below 1 ATR the deviation is within one average M15 true range of the mean, the noise the hypothesis does not address; above 2 the setup becomes rare by construction. Judgment, no data |
| `er_max` | ratio | 0.30 | 0.30–0.50 | {0.30, 0.50} | 0.30: the last half-day's net move is at most 30% of its path; 0.50 admits more directional days. Tests whether the regime filter matters. Judgment, no data |

- **Trials:** 2 variants × 3 × 2 = **12** (owner's maximum: two dimensions, ≤ 6 points per
  variant, ≤ 12 initial trials).
- **One trial** = one backtest run of one grid point of one variant over 2020-12-15 …
  2025-09-30, recorded in the ledger. Fold statistics are slices, not trials.
- **Not trials** (existing rule, ORB section 14): stress re-runs and the best-case intra-bar
  replay of a selected configuration, linked to their trial.
- **Frozen at freeze:** no value, filter or definition is added after results. Any change is a
  new version and its trials add to the count.

**Fixed (not searched; a change is a version bump):**

| Name | Value | Section |
|---|---|---|
| mean | TWV, M5 typical bid price × `tick_count`, session-anchored | 3.1 |
| `atr_period`, ATR window | 14, 100 M15 bars ending at `Ts` | 3.2 |
| `er_bars` | 12 (13 H1 closes) | 4 |
| `accumulation_minutes` | 60 | 5 |
| structural level | strict new session extreme | 5 |
| `setup_expiry_minutes` | 30 | 6 |
| trigger | close beyond the previous bar's far side | 6 |
| uses per session-date | 1 | 6 |
| `stop_buffer_points` | 25 | 8 |
| `min_stop_points` | `max(500, trade_stops_level + 10)` | 8 |
| `stop_cap_points` | 2,000 | 8a |
| target | TWV at T | 9 |
| `min_reward_risk` | 1.0 (`d_T ≥ d_S`) | 9 |
| `max_hold_minutes` | 90 | 10 |

## 15. Expected trade count

- **Not estimated.** No count was run (not authorised). Feasibility diagnostics are a
  separate approval (section 18b, B1).
- **Upper bound:** one per session-date, so at most 2 × the number of trading dates.
- **Requirements:** ≥ 30 closed trades per OOS fold, ≥ 200 concatenated, ≥ 30 per side and
  session (gates 1, 8, 9), and ≥ 100 prior trades for a point to be selectable for a fold
  (section 16). The training-only window before fold 1 is 9.5 months, so fold 1 needs some
  grid point with at least 100 trades in it (10.5 a month on average).
- **Main risk:** the 500-point minimum, `d_T ≥ d_S`, the rejection trigger and one use per
  session all remove setups. An insufficient sample is a **fail**, never a reason to relax
  a rule.

## 16. Pre-registered evaluation (carried forward)

Carried forward unchanged from opening_range_breakout 0.1.0 section 16 (itself trend_pullback
A1's), with only the grid names changed:

- **Periods and sealing:** development data 2020-11-12 … 2025-09-30; sealed 2025-10-01 …
  2026-10-08, refused by the loader before reading, opened once only for a frozen, approved
  candidate (`--open-sealed --by --reason`); exact-tick subset (2026-06-26 onward) reported
  apart. **Evaluated period 2020-12-15 … 2025-09-30**, 10,000 USD at 2020-12-15.
- **Folds:** anchored, by entry date (UTC): training only 2020-12-15 … 2021-09-30; OOS folds
  2021-10-01 … 2022-09-30, 2022-10-01 … 2023-09-30, 2023-10-01 … 2024-09-30, 2024-10-01 …
  2025-09-30.
- **Selection:** fold k uses the point with the highest net expectancy per trade over all
  development trades entered before fold k, among points with ≥ 100 such trades. Exact ties
  (integer cents) go to the default (`k_dev` 1.5, `er_max` 0.30), then to the
  lexicographically smallest (`k_dev`, `er_max`). No eligible point → the fold has 0 OOS
  trades and gate 1 fails; no fallback. Concatenated OOS = each fold's trades from its
  selected point's own run. Frozen configuration = the same rule over the whole period.
- **No purge or embargo:** every position is closed by 17:55 London, before midnight UTC.
- **Reported:** as ORB section 16, with this family's counters and statuses (section 6), the
  `d_S`, `d_T` and `TWV − X` distributions, and the sessions `london_morning` /
  `ldn_ny_overlap`. Win rate is reported, never a criterion.
- **Measurement definitions:** unchanged (booked net P&L; slicing by entry decision time;
  net expectancy, net PF, closed-trade max drawdown by exit time).

**Pass thresholds** (all must hold; insufficient sample anywhere is a fail):

1. ≥ 30 closed trades in every OOS fold, ≥ 200 in total.
2. Net expectancy > 0 USD/trade and aggregate net P&L > 0.
3. Net expectancy > 0 in ≥ 3 of 4 OOS folds.
4. Net profit factor ≥ 1.10.
5. Net expectancy > 0 in the spread ×1.5 re-run of the same selected points (×2.0 reported).
6. Max drawdown ≤ 1,000 USD.
7. Aggregate net P&L > 0 without the calendar month with the largest net P&L.
8. Long and short each ≥ 30 trades and net expectancy ≥ 0.
9. `london_morning` and `ldn_ny_overlap` each ≥ 30 trades and net expectancy ≥ 0.
10. Neighbourhood: one-step neighbours of the frozen point (±1 step on exactly one
    parameter; 3 for `k_dev` 1.5, else 2); at least ⌈2/3 × n⌉ with net expectancy > 0 in
    folds 1–4.
11. `phase_3b_required` false for the frozen configuration (worst-case run + linked
    best-case replay; ADR-0009 §9).
12. Deflated Sharpe ≥ 0.95 for the walk-forward OOS result (Bailey & López de Prado, 2014;
    `aurum.validation.walkforward`), with the cumulative N and V below and the existing
    undefined-SR rules (undefined trial SRs stay in N, listed, out of V; an undefined
    candidate SR or V fails).
13. Sealed period untouched: no vwap_reversion trial's data range reaches 2025-10-01; no
    sealed opening or parity access by a vwap_reversion run; other strategies' historical
    rows listed apart.

**Evaluation order:** gates 1–4, 6–9, 12 and 13 first, read-only, on the 12 runs. Any
failure rejects the variant and stops its evaluation; gates 5, 10 and 11 are then not run.
All pass → Claude stops; gates 5, 10, 11 each need a new authorisation. M15 vs M5 compared on
OOS net expectancy and drawdown, never win rate.

**Sealed-test threshold** (Phase 6): net expectancy > 0, net PF ≥ 1.05, ≥ 50 trades.

**Compatibility flags for review** (nothing changed silently):

- Gate 10's neighbour count works on the 3 × 2 grid as on ORB's 2 × 3.
- The 100-prior-trade selection minimum and gate 1 may not be reachable if setups are rare
  (section 15). Under the rules that is a fail; flagged so it is not a surprise.
- Gate 9 slices by the session of the entry decision time; a trade entered in one session
  and exiting in another cannot happen here (section 10).

**Cumulative trial accounting (V15).**

- **N = 66 + n**, where 66 = 54 trend_pullback + 12 opening_range_breakout research trials
  (reference trials excluded), and **n = the number of vwap_reversion research trials
  actually recorded** in the ledger for the declared grid, rejected or not. If all 12 points
  produce a trial, N = 78.
- 78 is the count this grid produces, **not a cap**. Under the existing rule, stress re-runs
  and best-case replays are linked runs, not trials. Any later research trial (a new version,
  family or grid point) adds to N. Before the assessment, the analysis must confirm how the
  ledger represents linked runs, so that N counts exactly the research trials.
- **V** = sample variance (ddof 1) of all N trials' daily Sharpe ratios over the same 1,032
  folds 1–4 market days; the existing trials' values are read from their runs, read-only.

**What gate 12 implies, approximately — not a target and not a probability of success.**
Assumptions: N = 66 and V = 6.293e-04 held fixed (the ORB assessment's inputs, so
SR0 = 0.0597 per day); T = 1,032 days; the candidate's daily skew 0 and kurtosis 3 (normal).
Then DSR ≥ 0.95 needs a daily SR ≈ 0.111, or ≈ 1.76 annualised (× √252). With skew 0.47
and kurtosis 4.11 (ORB m5's values) it is ≈ 0.110. With N = 78 and the same V, SR0 rises to
≈ 0.061 and the requirement to ≈ 0.113. These are hand calculations from the formula in
`walkforward.deflated_sharpe`, not a run. V will change when the new trials enter it. The
rules here were not chosen to reach any Sharpe value; the figure only states how demanding
the unchanged gate is.

**Proposed Phase 5 stop rule (V14).** vwap_reversion is the third and last family the
MASTER_PROMPT plans for Phase 5. If **neither variant survives the registered evaluation**:

- the planned three-family lab ends, and the Phase 5 exit gate is recorded as **not met**;
- `reports/phase_5.md` writes up the findings of all three families (charter kill criterion);
- no fourth family, rescue revision of any family, or ML is started without a separate
  owner decision recorded in `docs/STATUS.md`.

## 17. Decision trace (worked example) — synthetic

**All values are invented** to check the arithmetic; no data was read. A real-data trace is
an implementation-stage check (section 18a) and needs its own approval.

Date: a UK-winter trading date, `london_morning`, `A` = 07:00 London = 07:00Z. Variant `m15`,
defaults (`k_dev` 1.5, `er_max` 0.30).

| Condition | Values | Result |
|---|---|---|
| Setup bar `b_s` | 08:15–08:30Z, `Ts` = 08:30Z; low 1990.50, high 1994.80 | |
| Accumulation | `A + 60 min` = 08:00Z ≤ 08:30Z < 11:00Z | true |
| Regime | ER(08:30Z) = 0.22 ≤ 0.30 | true |
| Deviation | TWV(Ts) 2002.00 − 1990.50 = 11.50 ≥ 1.5 × ATR 6.00 = 9.00 | true |
| New session low | 1990.50 < 1992.30 (min low of 07:00–08:15Z bars) | true |
| Blackout in `[08:15, 08:30]Z` | 03:15–03:30 New York | none |
| Trigger bar `d` | 08:30–08:45Z, T = 08:45Z; low 1991.00, close 1995.60 | |
| Not broken | 1991.00 ≥ X 1990.50 | true |
| Rejection | 1995.60 > high(`b_s`) 1994.80 | true |
| Room to the mean | 1995.60 < TWV(T) 2001.40 | true |
| Stop | 1990.50 − 0.25 = 1990.25 → floor → 1990.25; `d_S` = 535 | 500 ≤ 535 ≤ 2,000 |
| Target | floor(2001.40) = 2001.40; `d_T` = 580 | 580 ≥ 535 |
| Decision | | `Signal(BUY, market, time 08:45Z, stop 1990.25, target 2001.40, max_hold 90 min)` |
| Spent | no further `london_morning` signal that date | |
| Timeout | 08:45Z + 90 min = 10:15Z | |

Same setup with TWV(T) = 1999.40 instead: `d_T` = 380 < 535 → no signal,
`reward_risk_below_min`, session spent. With ATR 3.00, TWV(Ts) 2000.00, a setup low of
1995.40 (deviation 4.60 ≥ 4.50) and a rejection close at 1997.60, the stop is 1995.15 and
`d_S` = 245 → `stop_below_minimum`, session spent. These two cases are why the sample size is
the main unknown (section 15).

## 18. Known risks, failure modes and disclosure

- **Disclosure: what the author had seen when writing this draft.**
  - **trend_pullback 0.2.0 + A1** (54 trials): its rejection, gate results and splits.
    `london_morning` lost, `ldn_ny_overlap` gained; m5 beat m15; m15's short side lost; m5's
    OOS was profitable but failed gates 9 and 12.
  - **opening_range_breakout 0.1.0** (12 trials): its rejection and full assessment. Both
    variants lost on the London range and the short side; the New York slices and the UK
    winter/normal-week slices were positive; most exits were timeouts and few reached the
    target; the monthly table; DSR inputs (N 66, V 6.293e-04, SR0 0.0597). Its feasibility
    evidence: range widths and stop-distance distributions by session.
  - **Other recorded figures:** the Phase 3 intra-bar ambiguity table, the Phase 2 spread
    statistics, and the null baseline (−381.76 USD over a period that includes the sealed
    one).
  - All of this was read from committed documents; no raw data was read for this draft.
- **Choices those results could have influenced, and how they were made:**
  - **Fading moves at all.** The planned family predates every result, but two momentum
    families losing makes reversion look attractive after the fact. A reversion family is
    not the sign-reversal of a losing one: costs are paid twice over, not refunded.
  - **Both sessions and both sides are kept.** No session, side or DST restriction is made,
    although London and short trades lost in both earlier families.
  - **90-minute timeout:** derived from calendar times (section 10); the ORB timeout share is
    known.
  - **Target at the mean:** fixed by the planned hypothesis; ORB's low target-hit share is
    known.
  - **Not influenced by data:** the TWV definition, ER, the grid values, the trigger, the
    one-use rule. The buffer (spread statistics) and minimum (ambiguity table) use recorded
    measurements, not P&L.
- **Hypotheses derived from viewed results — not part of this spec and not independent:**
  New-York-only or long-only variants of any family; ORB with other exits or a re-gridded
  range; trend_pullback without `london_morning`; fading London breakouts; DST or season
  filters. Any of them would be a new post-results hypothesis whose trials add to N.
- **Proxy risk.** If quote activity is unrelated to traded volume, TWV is an activity-weighted
  mean (section 3.1). A change in the broker's tick generation over 2021–2025 would move the
  weights without any market change; it cannot be checked from the stored bars alone.
- **Bid-only mean.** TWV and every setup use bid prices; costs of the short side are higher
  (section 9, 13).
- **Unscheduled news and release continuation.** Only the scheduled windows are excluded.
  A move that continues past the window end, or an unscheduled event, is the case where
  reversion is expected to fail and the stop is the only protection.
- **Session-extreme selection.** Setups occur only at new session extremes with an extension
  of at least `k_dev` ATR and a stop of at least 500 points: larger moves on more volatile
  days. This is mechanical and stated in advance.
- **Volatility drift.** The ORB evidence recorded wider ranges in 2025; with ATR-scaled
  deviations and absolute stop bounds, the share of setups passing 500 points will change
  across folds.
- **Sample dependence.** At most two trades per date, sharing a macro backdrop.
- **Tester parity is unresolved** (docs/STATUS.md, Phase 3). The simulator has not been
  compared with the MT5 Strategy Tester. Whether research may continue before that is an
  owner decision required before any trial (section 18b, B5); this spec does not make it.

## 18a. Checklist before any trial (implementation and analysis tests)

Each item is a test or a recorded check, written and green before launch.

**Rules (synthetic bars, `tests/unit`):**

- [ ] TWV: weighted mean exactly as section 3.1 (fsum, integer weights); zero-weight bar
      excluded and counted; `Σ w = 0` → undefined; a missing M5 slot → `data_gap` for every
      later instant of the session; reset at each session anchor (no carry-over between
      sessions or dates); bars before `A` never included.
- [ ] Anchors and windows in every DST regime (UK winter/summer, both mismatch directions)
      against section 5 and 10 times; early-close clipping; a session layout change in
      `sessions.yaml` moves the anchor.
- [ ] ER: formula, 13-bar requirement, zero denominator, a window spanning the daily break
      and a weekend.
- [ ] ATR at `Ts` equals `indicators.atr` over the 100 M15 bars ending at `Ts`.
- [ ] Setup boundaries: deviation equal to `k_dev × ATR` passes; equal to the prior session
      low fails (strict); `Ts = A + 60` eligible, `A + 45` not; blackout-touching bars
      (`[08:15, 08:30]` New York excluded, `[08:45, 09:00]` eligible); ambiguous setups.
- [ ] Trigger: strict comparisons; expiry at exactly `Ts + 30` (eligible) and one bar later
      (not); rule 3 breaking; replacement by a newer setup of either side; missing `p`.
- [ ] Spend and one use; restart before and after the spend (stateless re-walk).
- [ ] Stop/target rounding (float noise, off-grid); `d_S` 499/500/2,000/2,001 and the
      stops-level minimum; `d_T = d_S` passes, `d_T = d_S − 1` fails.
- [ ] Signal fields: `time`, `data_as_of`, `max_hold` 90 min, reasons and features in numbers.
- [ ] Variant identity: on the same bars both variants find the same setups; only the trigger
      timeframe differs.
- [ ] Diagnostics: each instant counted once however often the strategy is called; every
      session-date ends in exactly one status.
- [ ] Causality property test (hypothesis): changing or adding bars after T never changes a
      decision, TWV, ER or ATR at T.
- [ ] Grid: exactly 6 declared points per variant; params accept only declared values.
- [ ] Warm-up: merged history `{H1: 13, M15: 100}`, loaded by bar count; refused when short.
- [ ] Architecture tests: no adapter, execution or broker import.
- [ ] Mutation check of the rule comparisons, survivors explained.

**Data and environment (each needs its own approval, section 18b):**

- [ ] Section 8a re-read from the pinned snapshot.
- [ ] Real-data trace on development bars (no P&L, no ledger).
- [ ] Dry `prepare` of both variants: contexts differ only by `variant`.
- [ ] Ledger integrity check and a pre-run backup; collector state recorded.

**Analysis (committed and tested before the trials, unlike ORB, where the analysis followed
the runs):**

- [ ] Assessment script with the registered selection (new default and tie order), every
      gate, the counters above, and gate 12 with N = 66 + n and the undefined-SR rules; tested
      on synthetic runs; byte-reproducible output.
- [ ] How the ledger represents linked runs confirmed, so N counts research trials only.

## 18b. Approval boundaries

Claude stops at each boundary; one approval never covers the next.

| # | Step | Needs | Reads market data? |
|---|---|---|---|
| B0 | Review of this draft; decisions V1–V15 | owner | no |
| B1 | Feasibility diagnostics (optional): descriptive counts on development bars ≤ 2025-09-30, no fill, exit or P&L; any rule change after them is listed as data-informed | separate approval | yes, development only |
| B2 | Freeze 0.1.0 | owner | no |
| B3 | Implementation and deterministic tests (synthetic data only) | separate approval | no |
| B4 | Real-data trace check (no P&L, no ledger) | separate approval | yes, development only |
| B5 | Prerequisites: tester-parity decision (complete it, or proceed without it with the reason recorded); analysis script committed and tested; ledger backup | owner | no |
| B6 | Run the 12 declared trials (one command per variant, no automatic retry) | separate approval | yes, development only |
| B7 | Read-only first-stage assessment | separate approval | run outputs and ledger, read-only |
| B8 | Decision on the assessment | owner | — |
| B9 | Later validation, each separately: stress re-runs (gate 5), best-case replay (gate 11), frozen-configuration neighbourhood (gate 10), like-for-like null baseline, Phase 6 sealed opening | separate approval each | yes |

## 19. Change log

| Version | Date | Change | Reason |
|---|---|---|---|
| 0.1.0 (draft) | 2026-10-11 | Initial draft: tick-weighted session mean (VWAP proxy), H1 efficiency-ratio regime, M15 setup at a new session extreme ≥ `k_dev` ATR from the mean, rejection trigger on M15 or M5 bars, stop 25 points beyond the extreme within 500–2,000 points, target at the mean with `d_T ≥ d_S`, 90-minute timeout, one use per session-date, 12-trial grid, gates carried forward from ORB 0.1.0, cumulative N = 66 + n, proposed Phase 5 stop rule. Decisions V1–V15 open. No data read | Owner authorised a documentation-only draft (2026-10-11) after rejecting opening_range_breakout 0.1.0 |
