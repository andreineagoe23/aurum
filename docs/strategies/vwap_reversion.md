# vwap_reversion — strategy spec

> **DRAFT — NOT FROZEN — NOT IMPLEMENTED — NO TRIALS AUTHORISED.**
>
> - Written by Claude from `docs/STRATEGY_SPEC_TEMPLATE.md` at the owner's request
>   (2026-10-11: "documentation-only draft of vwap_reversion 0.1.0"). Revision 2 answered the
>   owner's first B0 review; revision 3 applies the B0 audit's documentation corrections;
>   revision 4 records the owner's B1 authorisation and rulings R1–R6 (all 2026-10-11). The
>   changes are listed in section 19.
> - **No market data was read for this draft, up to and including revision 4**: no bars,
>   ticks, feature stores, trade tables, ledger rows or symbol snapshots, and no feasibility
>   count was run. Every number below is a rule choice, arithmetic, or a figure quoted from a
>   committed document or config (cited where used).
> - **B0 decided (owner, 2026-10-11):** decisions V1–V17 (section 0) accepted with the
>   proposed defaults; V17 explicitly, with the disclosure as corrected in revision 3. The
>   draft is **not frozen**.
> - **B1 authorised (owner, 2026-10-11):** the feasibility diagnostic only, under rulings
>   R1–R6 and the numerical rulings of section 18c. No snapshot read (B2), freeze, strategy
>   implementation, registry or config change, backtest, fill, exit, P&L, ledger access, trial,
>   parity access or sealed-data opening is authorised.
> - **Written after two rejected families.** The author has read the trend_pullback 0.2.0 and
>   opening_range_breakout 0.1.0 development results, including their splits. Section 18
>   lists what was seen and which choices it could have influenced.

## 0. Decisions (accepted at B0, 2026-10-11; not frozen)

All seventeen were accepted by the owner with the proposed defaults below; V17 explicitly.
None is data-informed in the sense of the ORB spec (no count or P&L was computed for this
family). "Prior results" marks a choice the author made knowing the earlier families' results.
V16 and V17 are new in revision 2.

| # | Decision | Proposed default | Reason | Alternative | Prior results? |
|---|---|---|---|---|---|
| V1 | Mean | `TWV`: session mean of M5 bid typical prices weighted by `tick_count` (section 3.1); not a traded-volume VWAP | The only weight the data has; closest available proxy to the planned "session VWAP" | Equal weights (time-weighted mean): immune to changes in the broker's tick generation, further from "VWAP" | No |
| V2 | Regime | H1 efficiency ratio over 12 bars `≤ er_max` (section 4) | Bounded, unit-free, no seed; does not reuse a rejected family's EMA rule | trend_pullback's EMA slope reversed | No |
| V3 | Structural level | The setup bar makes a strict new extreme of its session (section 5) | Fixed by bars already printed; no lookback parameter | Prior-day or prior-24-hour extreme: rarer setups, by an amount unknown without counts | No |
| V4 | Trigger | Close beyond the previous bar's far side, on the variant's decision bars (section 6) | Objective rejection rule; same template as both earlier families' variant convention (variants differ by decision timeframe). **Not an identical trigger** across variants (section 6.4) | m5: M5 close beyond the setup bar's far side (same level as m15's first trigger, earlier timing) | No |
| V5 | Stop buffer | 25 points beyond the setup extreme (section 8) | Covers the recorded per-quote spread p99 in both sessions (20, 24); see V17 | 20 (as ORB) | No |
| V6 | Stop minimum | `max(500, trade_stops_level + 10)` points; below it **no trade**, the stop is never widened (section 8) | Engineering policy for bar-only data (section 8.2); not shown sufficient for this strategy | Floor-widened stop `min(X − 25, ref − 500)`: **not proposed** | No |
| V7 | Stop cap | 2,000 points (ceiling 2,490) (section 8a) | Affordability arithmetic; cap-sized stops stay affordable down to about 8,040–8,120 USD equity | Any value ≤ 2,490 | No |
| V8 | Minimum reward:risk | `d_T ≥ d_S`, measured from the bid reference `ref` (section 9) | Keeps the zero-edge cost hurdle near 1.8 percentage points of hit rate at the smallest stop (section 13). After costs the executable ratio is below 1 (section 13.2) | None | No |
| V9 | Timeout and hold bound | `max_hold` 90 min from `Signal.time`, plus a construction-time check that `latest decision + 90 min ≤` 12:15 London (`london_morning`) and 17:45 London (`ldn_ny_overlap`) (section 10) | Every TIMEOUT close is **requested** ≥ 15 min before 08:30 New York (London positions) and 14:00 New York (overlap positions) in every DST regime. It does not guarantee the fill (section 10.3) | 60 or 120 min (120 breaks the bound) | Yes (ORB exits were mostly timeouts) |
| V10 | Spend | The first instant meeting trigger rules 1–6 spends the session-date, whatever happens next: geometry rejection, risk rejection, broker rejection or unknown outcome (section 6.3) | No within-session search for a trigger that happens to fit the geometry; stateless and restart-safe | Spend only on an emitted signal: more trades, but a search; not proposed | No |
| V11 | Grid | `k_dev` {1.0, 1.5, 2.0} × `er_max` {0.30, 0.50}: 6 per variant, 12 in all (section 14) | Two dimensions, owner's budget. Note: when `k_dev × ATR` is small, `k_dev` acts mostly through which setups spend the session (section 13.1) | — | No |
| V12 | News | A blackout instant is not a decision time; a setup bar touching a blackout window is not a setup; blackouts never close positions (section 12) | Scheduled releases are information, outside the hypothesis | — | No |
| V13 | Setup window | `A + 60 min ≤ Ts ≤ E − 45 min` (section 5) | ≥ 12 M5 bars in the mean; the whole 30-min expiry ends before the session end, so both variants have the same setups and the same latest decision `E − 15 min` | `Ts < E` (revision 1): m5 could use setups m15 could not | No |
| V14 | Phase 5 stop rule | If neither variant survives, the three-family lab ends (section 16) | Charter kill criterion | — | Yes |
| V15 | Trial accounting | N = 66 + vwap_reversion research trials actually recorded (section 16) | Existing ORB §14 / O6 rule | — | No |
| V16 | Direction coverage | Longs and shorts, exact mirror (table 5a); gate 8 unchanged | The hypothesis is symmetric; gate 8 needs both sides | Long-only: a post-results idea (section 18), not proposed | Yes (shorts lost in both earlier families; not used) |
| V17 | Statistics measured on dates now sealed | Accept, disclosed: V5, V6 and the cost illustration behind V8 (section 13.3, quoted in section 2) cite spread and intra-bar statistics computed before the seal existed, on dates wholly (spreads) or partly (intra-bar table) in the sealed period; sections 3.1 and 18 cite the release spread spikes recorded there (section 18) | Cost and fill-structure statistics, not P&L; the same figures already set `risk.yaml` `max_spread_points` and `backtest.yaml` `bar_spread_extra_points` | Re-derive from development-period bars: needs a data-reading approval; per-quote spreads do not exist before 2026-03-29 | No |

## 1. Identity

- Name: `vwap_reversion`
- Family: `vwap_reversion` (declared in `aurum.settings.StrategyFamily`; placeholder config
  `configs/strategies/vwap_reversion.yaml` since the Phase 0 scaffold, `50d2b83`)
- Variants: `m15` and `m5`. They share the mean, regime, setups, stop, target, timeout, spend
  rule and grid; they differ in the decision bars of the trigger (M15 or M5), which changes
  the confirmation level, the decision instants and the timing (section 6.4).
- Version: `0.1.0` (draft, revision 4)
- Author: Claude (draft) for Andrei Neagoe
- Date: 2026-10-11

## 2. Hypothesis

**Provenance.** Two layers with different standing:

- **Planned before any result:** the broad family. The Phase 5 family table of MASTER_PROMPT
  §7 plans "VWAP / structure reversion" with, paraphrased (not quoted): a non-trending H1
  regime, an M15 deviation from the session VWAP into a structural level, and an M5
  rejection candle. It was written, and the config scaffolded, before any backtest existed.
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
- The mechanism, if it exists, is symmetric: uninformed selling and uninformed buying should
  both revert. Both sides are therefore traded with mirrored rules (V16).
- **Nothing in this repository shows that either mechanism operates in XAUUSD, at Fusion
  Markets, or in this dataset.** Spot gold is traded over the counter; no consolidated traded
  volume exists, and the data hold only broker quote activity (section 3.1). The proxy mean
  is a hypothesis about where such flow would revert to, not a measured benchmark.
- **Where it should not work:** trending conditions (excluded by section 4) and moves caused
  by scheduled releases (section 12). Unscheduled news is not excluded and is a known failure
  mode (section 18).

**Why it might persist after costs.** Section 13 states the cost arithmetic: at the allowed
stop distances the zero-edge cost is about 0.8–4.1% of the stop distance at the median spread.
It does not show that the hit rate clears the break-even rate.

**Falsification.** Concatenated out-of-sample net expectancy ≤ 0 USD/trade, or ≤ 0 in 2 or
more of the 4 out-of-sample folds (section 16). Either kills version 0.x.

## 3. Data, time, the TWV proxy, warm-up

All bars are canonical **bid** bars, keyed by open time, visible at
`close_time = open_time + timeframe`. A bar is **completed at T** if `close_time ≤ T`.
Nothing with `close_time > T` is used at T.

| Timeframe | Fields | Bars read at decision time T |
|---|---|---|
| H1 | close | the 13 newest completed H1 bars at the setup close `Ts` (section 4) |
| M15 | high, low, close | the 100 newest completed M15 bars at `Ts` (ATR); the session's M15 bars completed by T (setup, structure; trigger for m15) |
| M5 | high, low, close, `tick_count` | the session's M5 bars completed by T (TWV, both variants; trigger for m5) |
| Ticks | bid, ask | none; execution only |

**Conventions:**

- Both sides are written out (table 5a and sections 6, 8, 9). No rule is left to an implied
  mirror.
- Comparisons are exact float64 comparisons of unrounded values. "Strictly" means equality
  fails.
- Rounding to the price grid: `q = round(x / point, 6)`, then `floor(q)` or `ceil(q)` as the
  rule says, × `point`. Distances in points are `round((a − b) / point)`, an integer. Stops
  are rounded **away from** the entry, targets **toward** it (both sides).
- **Session anchor** `A(s, D)` and **session end** `E(s, D)` = start and end of session `s` on
  London trading date D, from `configs/sessions.yaml` through `TradingCalendar` (Europe/London
  wall clock, DST via `zoneinfo`, `E` clipped by an early close). Today's values:
  `london_morning` 07:00–11:00 and `ldn_ny_overlap` 13:00–16:30 London; the config is the
  source. Both start and end on whole hours or half hours, so every configured boundary is an
  M5 and M15 close in every DST regime. An `E` clipped by an early close need not be (a
  16:31 cutoff, for example); the rules stay well defined because they compare times with
  `≤` and `<`, and no evaluated date clips either session (section 5).
- **Previous bar** of a bar = the bar of the same timeframe whose `open_time` is one timeframe
  earlier. If it does not exist, any rule needing it is false and the instant counts as a
  `data_gap` (section 6).

### 3.1 The TWV proxy (V1)

**Definition.** For session `s` of date D and an instant `t` that is an M5 close with
`A < t`:

- `B(t)` = the M5 bars with `open_time ≥ A` and `close_time ≤ t`, i.e. the slots
  `[A, A+5), …, [t−5, t)`. It is **complete** when it holds all `(t − A) / 5 min` of them.
- For each bar `i`: typical price `p_i = (high_i + low_i + close_i) / 3` (bid), weight
  `w_i = tick_count_i`.
- `TWV(t) = fsum(w_i × p_i) / Σ w_i`, with `math.fsum` for the numerator and an integer sum
  for the denominator, so the value is deterministic.
- `TWV(t)` **exists** only if `B(t)` is complete and `Σ w_i > 0`.

**Which TWV each rule uses** (no other value is ever used):

| Rule | Value | Bars in it |
|---|---|---|
| Setup deviation (section 5) | `TWV(Ts)` | M5 bars in `[A, Ts)` |
| Trigger "room to the mean" (section 6) | `TWV(T)` | M5 bars in `[A, T)` |
| Target (section 9) | `TWV(T)`, the same number, rounded | M5 bars in `[A, T)` |

`TWV(T)` for m15 and m5 is the same function; the variants evaluate it at different instants
because their decision instants differ.

**Name.** `TWV` = tick-weighted session mean of bid typical prices. It is called a "VWAP
proxy" in prose; it is **not** a volume-weighted average price of traded gold.

**What the stored weight is — known:**

- Canonical bars carry `tick_count` (`aurum.data.schemas.CANONICAL_BARS`), exposed to
  strategies as `BarSeries.tick_count` (`aurum.features.view`; a null becomes 0).
- It follows the MT5 `tick_volume` convention: the number of ticks that **changed the bid**.
  Measured in Phase 2: the broker's `tick_volume` equals the bid-changing ticks on exact days
  (ADR-0007; reports/phase_2.md §4).
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
one, and the hypothesis is tested against that mean (section 18). Quote activity spikes at
releases (reports/phase_2.md §5 records spread spikes at the 08:30 and 10:00 New York
releases), so a release inside the accumulation period can weigh heavily in TWV.

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
decision (`aurum.execution.engine.ATR_BARS`; period `RiskConfig.atr_period`, default 14 in
`aurum/settings.py` and not set in `risk.yaml`, passed to the engine by
`aurum.backtest.pipeline`). Fewer than 15
bars → no ATR → no setup. It is the volatility unit of the setup (section 5) and is fixed at
`Ts`; it is not recomputed at the trigger.

### 3.3 Warm-up and history (by bar count)

- Strategy warm-up: `warmup_bars = {H1: 13, M15: 100}` (sections 4 and 3.2). M5 needs none:
  TWV uses only bars of the same session.
- The engine's own history is 100 M15 bars (`engine.HISTORY_BARS`), so the merged
  requirement is the same `{H1: 13, M15: 100}`.
- Warm-up bars produce no decision, signal or metric. The evaluated period starts on
  2020-12-15 (section 16); the history before it lies in 2020-11-12 … 12-14.

**Stateless.** Every rule is a function of the bars and the calendar at T. At each call the
strategy re-walks the session from `A` to T, so a restart, a missed call or a late call
reaches the same state, including whether and when the session was spent (section 6.3).

## 4. H1 context (regime rule) (V2)

- `ER(Ts) = |close(h0) − close(h12)| / Σ_{i=0..11} |close(h_i) − close(h_{i+1})|`, where `h0`
  is the newest H1 bar with `close_time ≤ Ts` and `h_k` is k bars before it **in the bar
  sequence** (H1 bars exist only for hours with ticks; the window can span the daily break
  or a weekend, as trend_pullback's EMA windows can).
- **Non-trending regime** at `Ts`: `ER(Ts) ≤ er_max`. `er_max` is searched: {0.30, 0.50},
  default 0.30. The regime is checked once, at the setup close; it is not re-checked at the
  trigger or during the position.
- The denominator is summed with `math.fsum`, so the value is deterministic (clarified in
  revision 4; the defined quantity is unchanged).
- Fewer than 13 H1 bars, or a zero denominator → no regime → no setup.
- ER does not depend on side.
- **Why ER.** It is bounded in [0, 1], has no smoothing seed, and needs no price-unit
  threshold, so one value means the same in 2021 and 2025. Twelve H1 bars is the preceding
  half-day: for a London setup, mostly the Asian hours; for an overlap setup, mostly the
  London day. These are judgments, not data.
- **Not trend_pullback's rule reversed.** The rejection of trend_pullback 0.2.0 says nothing
  about its EMA regime on its own (ORB spec section 4); ER is used so this family does not
  depend on a component of a rejected family.
- Volatility is bounded only through the stop rules (section 8).

## 5. M15 setup (V3, V13, V16)

A **setup** is an M15 bar `b_s` of session `s` on date D, closing at `Ts`, that meets rules
1, 2, 5, 6 below and the side rules 3 and 4 of exactly one side (table 5a).

1. **Setup window (V13):** `A + 60 min ≤ Ts ≤ E − 45 min`. Today: `Ts` from 08:00 to 10:15
   London (10 M15 closes) for `london_morning`, from 14:00 to 15:45 London (8 closes) for
   `ldn_ny_overlap`. `E` is the clipped session end; no early close in the development period
   clips either session (the earliest cutoff in `configs/data.yaml` for 2020-11 … 2025-09 is
   16:31 London on 2020-11-26).
2. **Regime:** `ER(Ts) ≤ er_max` (section 4).
3. **Deviation:** side rule (table 5a). `k_dev` is searched: {1.0, 1.5, 2.0}, default 1.5.
4. **Structural level — strict new session extreme:** side rule (table 5a), against every M15
   bar of the session with `open_time ≥ A` and `close_time < Ts`. With rule 1 there are always
   at least 3 such bars.
5. **Read set complete:** every M5 slot and every M15 slot with `open_time ≥ A` and
   `close_time ≤ Ts` has a bar, and `TWV(Ts)` and `ATR(Ts)` exist.
6. **No release inside the bar:** no instant of `[open_time(b_s), close_time(b_s)]` (closed
   interval) lies in a news blackout window (`NewsBlackout.reason_at` not None; section 12).

### 5a. Long and short rules side by side

| Rule | Long (fade a move down) | Short (fade a move up) |
|---|---|---|
| Setup extreme | `X = low(b_s)` | `Y = high(b_s)` |
| 3. Deviation | `TWV(Ts) − X ≥ k_dev × ATR(Ts)` | `Y − TWV(Ts) ≥ k_dev × ATR(Ts)` |
| 4. New session extreme | `X < min low` of earlier session M15 bars | `Y > max high` of earlier session M15 bars |
| Trigger 3: not broken | every decision bar closing in `(Ts, T]` has `low ≥ X` | every such bar has `high ≤ Y` |
| Trigger 4: rejection | `close(d) > high(p)` | `close(d) < low(p)` |
| Trigger 5: room to the mean | `close(d) < TWV(T)` | `close(d) > TWV(T)` |
| Unrounded stop | `X − 25 × point` | `Y + 25 × point` |
| Stop rounding | `floor` (away from entry) | `ceil` (away from entry) |
| `d_S` (points) | `round((ref − stop) / point)` | `round((stop − ref) / point)` |
| Target rounding | `floor(TWV(T))` on the grid (toward entry) | `ceil(TWV(T))` on the grid (toward entry) |
| `d_T` (points) | `round((target − ref) / point)` | `round((ref − target) / point)` |
| Order side | BUY (fills at ask) | SELL (fills at bid) |
| Stop / target triggered by (broker) | bid ≤ stop / bid ≥ target | ask ≥ stop / ask ≤ target |

All other rules (window, regime, read set, blackout, expiry, spend, minimum, cap, minimum
reward:risk, timeout) are identical for both sides.

- **Classification (ruling R5, revision 4), mutually exclusive.** It first requires rules 1,
  5 and 6. Then:
  - both sides satisfy side rules 3 and 4 → `ambiguous_setup`, **whatever the regime**; it
    does not also count `setup_regime_off`;
  - exactly one side satisfies rules 3 and 4, but rule 2 fails or ER is unavailable →
    `setup_regime_off` (diagnostic of how often the regime binds);
  - exactly one side satisfies rules 3 and 4 and rule 2 holds → a setup.
- An ambiguous bar is **not** a setup; it does not replace an active setup (section 6.2), but
  it breaks any active setup whose extreme it passes (trigger rule 3).
- **Why the session extreme.** It is the only level fixed by bars the session has already
  printed, needs no lookback parameter, and is the point where the deviation is largest. A
  prior-day or prior-24-hour extreme is the alternative (V3).
- **Why 60 minutes.** TWV over fewer than 12 M5 bars is dominated by the first bars of the
  session. In normal weeks it also keeps the 08:30 New York release (13:30 London) inside the
  `ldn_ny_overlap` accumulation period rather than in a setup bar.
- **Why `E − 45`.** The expiry is 30 minutes and a decision instant must be strictly before
  `E`; `Ts ≤ E − 45` makes every instant of `(Ts, Ts + 30]` a decision instant of both
  variants, so both variants see the same setups and the same latest decision `E − 15`
  (section 6.1).
- **Bid-only levels and shorts.** `Y` is a bid high; the short's stop is triggered by the ask.
  The 25-point buffer means a bid re-test of `Y` stops a short only if the spread then exceeds
  25 points (section 8).

## 6. Trigger, decision times, spend and diagnostics (V4, V10)

**Variant timeframe.** `m15`: decision bars are M15 bars; `m5`: decision bars are M5 bars.
At a decision instant T, `d` = the decision bar closing at T and `p` = its previous bar.

### 6.1 Decision instants and exact times

A **decision instant** of variant v for session `s` is T such that: T is a close of v's
timeframe; `session_at(T)` is `s` with entries allowed; `A + 60 min < T ≤ E − 15 min`, the
only instants at which a setup can be active (sections 5 and 6.2); and T is not in a news
blackout window. The engine asks the strategy at every OPEN close of the decision timeframe
(`Engine._step`); at any other of those calls the strategy returns nothing and counts
nothing. In particular `T = A`, where `B(A)` is empty and `TWV` cannot exist, is not a
decision instant. No trade changes: outside this range trigger rule 2 cannot hold.

Times with today's `sessions.yaml` (London wall clock; New York = London − 5 h in normal
weeks, − 4 h in mismatch weeks; UTC = London in UK winter, London − 1 h in UK summer):

| | `london_morning` | `ldn_ny_overlap` |
|---|---|---|
| `A`, `E` | 07:00, 11:00 | 13:00, 16:30 |
| Setup closes `Ts` | 08:00 … 10:15 (10) | 14:00 … 15:45 (8) |
| Expiry of the latest setup | 10:45 | 16:15 |
| m15 decision instants | 08:15 … 10:45 | 14:15 … 16:15 |
| m5 decision instants | 08:05 … 10:45 | 14:05 … 16:15 |
| **Latest decision (both variants)** | **10:45** | **16:15** |
| Latest decision, New York, normal / mismatch weeks | 05:45 / 06:45 | 11:15 / 12:15 |
| Entry fill (backtest) | first quote at or after `T` + 200 ms (≤ 60 s wait, else rejected) | same |

Blackout instants removed from the overlap decision instants (none in `london_morning`):

| Week | 10:00 New York window in London | m5 instants removed | m15 instants removed | Setup bars excluded (rule 6) |
|---|---|---|---|---|
| normal | 14:50–15:15 | 14:50, 14:55, 15:00, 15:05, 15:10 | 15:00 | `Ts` 15:00, 15:15 |
| mismatch | 13:50–14:15 | 14:05, 14:10 | none | `Ts` 14:00, 14:15 |

The 08:30 New York window (13:20–13:45 London normal, 12:20–12:45 mismatch) lies in the
overlap accumulation period or before the session; the FOMC window (13:30–15:00 New York =
18:30 / 17:30 London at the earliest) lies after every decision instant.

### 6.2 Setup life cycle

- **Known at `Ts`.** A setup exists only once `b_s` has closed. It is never used at any
  instant `≤ Ts`.
- **Trigger bars start no earlier than the setup is known.** Candidate decision bars `d` are
  those closing in `(Ts, Ts + 30 min]`; their `open_time ≥ Ts` in both variants. The previous
  bar `p` may lie inside the setup bar (m5: the last M5 bar of `b_s` at the first instant) or
  be the setup bar itself (m15, first instant); `p` is completed at `Ts` and serves only as
  the rejection threshold.
- **Active setup at T.** The setup with the largest `Ts < T` (strictly), provided
  `T ≤ Ts + 30 min` (inclusive). A setup whose `Ts = T` (possible for m15, and for m5 at M15
  closes) becomes active only for instants after T; at T the trigger is evaluated against the
  previously active setup, if any.
- **Replacement.** A newer setup of either side replaces the active one at its `Ts`. A bar
  that is not a setup (fails any rule, including `ambiguous_setup`, the regime, the window or
  the blackout rule) never replaces.
- **Breaking (trigger rule 3).** Checked over **every** decision bar closing in `(Ts, T]`,
  including `d` and bars closing at blackout instants: the whole interval `[Ts, T)` at the
  decision timeframe's resolution (each bar's low or high covers its whole path). Once
  broken, a setup stays broken for every later T. Not checked: the path after T (the stop
  covers it) and the ask (levels are bid levels).
- **Expiry.** At `T > Ts + 30 min` the setup is gone. With rule 1, `Ts + 30 ≤ E − 15`, so
  the session end never cuts an expiry window short.
- **Cancellation** = broken, replaced, expired, or the session-date spent. Nothing else.

**Order within an instant (rulings R2–R4, revision 4).** These fix how the life cycle is
counted. No trading decision changes: they follow from the rules above.

- **One terminal outcome (R2).** Each setup ends exactly once: triggered (it spent the
  session-date), broken, replaced or expired. At each instant **breaking precedes
  replacement**. A same-side newer setup is a strict new session extreme, so its bars pass the
  old extreme: the old setup normally counts as **broken**, not replaced. `setups_replaced`
  counts only an **unbroken** active setup displaced by a valid newer setup.
- **Exact expiry (R3).** At `T = Ts_old + 30`, the old setup's final eligible trigger (rules
  1–6) and its break condition are evaluated first. If it is still untriggered and unbroken,
  it counts **expired**. A new setup closing at that instant does not replace it; it becomes
  active for later instants if the session-date is still unspent.
- **Setup closing at the spend (R4).** At T, the previously active setup and a possible spend
  are processed first. If that spends the session-date, processing of the session-date stops
  at T: a new setup whose bar also closes at T is neither classified nor counted.

### 6.3 Trigger and spend

**Trigger** at a decision instant T (rule 1). All must hold, for the side of the active setup
(table 5a):

1. T is a decision instant of the variant (section 6.1).
2. There is an active setup (section 6.2).
3. Not broken.
4. Rejection.
5. Room to the mean.
6. **Read set complete:** every M5 slot and every M15 slot with `open_time ≥ A` and
   `close_time ≤ T` has a bar, `p` exists, and `TWV(T)` exists. At an m5 instant that is
   not an M15 close, the M15 slot containing T is not yet complete and is not in the set.

- For m15 the decision instants of a setup are `Ts + 15` and `Ts + 30`; for m5,
  `Ts + 5 … Ts + 30` (six), minus blackout instants.

**Spend (V10).** The session-date is **spent** at the first instant T meeting trigger rules
1–6. Then, in this order:

1. Section 8 (stop minimum, cap) and section 9 (minimum reward:risk) are checked. A failure
   gives no signal and records the status at T.
2. Otherwise the strategy returns a `Signal` with `time = T`.

The session-date stays spent in every case: a geometry failure; a risk-engine rejection
(spread caps, entry cutoff, news, stale quote, loss budgets, cooldown, exposure, sized volume
below `volume_min`); an entry blocked because an earlier order outcome is unresolved; a broker
rejection (no quote within 60 s, invalid stops); or an UNKNOWN outcome (never resent,
ADR-0011). The strategy never learns these outcomes and never signals again that session.

The spend is computed from bars by the re-walk, not from whether the strategy was called at
T. A missed call (disconnect, restart) followed by a later call finds the session already
spent at T and returns nothing; a signal is only ever emitted with `time` equal to the
current decision instant.

### 6.4 What m15 and m5 share and what differs (V4)

| | m15 | m5 |
|---|---|---|
| TWV, regime, ATR, setups, setup window | shared | shared |
| Expiry duration | 30 min | 30 min |
| Stop, cap, minimum, target, minimum reward:risk, timeout, spend rule, grid | shared | shared |
| Decision instants per setup | 2 (`Ts+15`, `Ts+30`) | 6 (`Ts+5` … `Ts+30`) |
| First rejection threshold | high (low) of the whole setup bar | high (low) of the setup bar's last M5 bar |
| Later rejection thresholds | the previous M15 bar | the previous M5 bar |
| Earliest possible entry | `Ts + 15` | `Ts + 5` |
| Break check | M15 bars, at M15 closes | M5 bars, at every M5 close |
| `ref`, `TWV(T)`, `d_S`, `d_T` | at an M15 close | at any M5 close |

Consequences, stated before any result:

- The trigger is the same **template**, not the same trigger. An M5 close above the previous
  M5 high is a smaller confirmation than an M15 close above the setup bar's high; m5 can
  enter earlier, nearer the extreme, with a smaller `d_S` and a larger `d_T`.
- Because the first trigger spends the session (V10), m5's earlier, weaker triggers make
  `stop_below_minimum` spends more likely than in m15. Which variant trades more is unknown.
- The variants share setups and dates, so they are **strongly dependent**; comparing them
  compares confirmation strength and timing, not two independent tests of the hypothesis.
- Every m15 decision instant is also an m5 decision instant; the converse does not hold.

### 6.5 Diagnostics and statuses

**Diagnostic counters** (per variant run; each instant counted once however often the
strategy is called; only while the session-date is unspent unless stated):

| Counter | +1 for each |
|---|---|
| `setups` | setup bar (section 5), by side, counted at its close, whether or not it is later triggered |
| `setups_replaced` | unbroken active setup displaced by a valid newer setup (R2) |
| `setups_broken` | active setup broken before any trigger, including by a same-side newer setup (R2) |
| `setups_expired` | setup still active at `Ts + 30` that was neither triggered nor broken after that instant's trigger and break checks (R3; a replaced setup is not counted) |
| `ambiguous_setup` | bar meeting rules 1, 5, 6 and both sides' rules 3 and 4, whatever the regime (R5) |
| `setup_regime_off` | bar meeting rules 1, 5, 6 and exactly one side's rules 3 and 4, with rule 2 failing or ER unavailable (R5) |
| `data_gap` | instant meeting trigger rule 1 whose read set is incomplete |
| `twv_undefined` | instant meeting rule 1 with a complete read set but `Σ w_i = 0` |
| `zero_weight_bar` | M5 bar with `tick_count` 0 in a read set (once per bar) |
| `blackout_skipped` | close of the decision timeframe in the session, inside a blackout window, at which rules 2–6 would hold |
| `stop_below_minimum` | spend whose first geometry failure (R1) is `d_S` below the minimum (section 8) — at the spend |
| `stop_above_cap` | spend whose first geometry failure (R1) is `d_S` above 2,000 (section 8a) — at the spend |
| `reward_risk_below_min` | spend whose first geometry failure (R1) is `d_T < d_S` (section 9) — at the spend |

**Geometry-failure precedence (ruling R1, revision 4).** At a spend the checks are evaluated
in the order (1) `d_S` below the minimum, (2) `d_S` above the cap, (3) `d_T < d_S`. The first
failure supplies the sole session-date status and increments only its counter. Reports keep
separate, clearly named `would_fail_*` combination flags (each check's result regardless of
precedence) so other aggregations remain possible; those flags are **not** section 6.5
counters. Each terminal life-cycle outcome counts once (R2–R4, section 6.2); counts of a newer
setup arriving after a break or at an exact expiry may be reported as non-normative
diagnostics, never as counters.

**Session-date status** (exactly one, the first that applies):

1. spent: `signal`, `stop_below_minimum`, `stop_above_cap` or `reward_risk_below_min`, from
   the spend (section 6.3);
2. `no_decision_time`: the session-date has no decision instant (section 6.1);
3. `data_gap`: at some decision instant the read set of trigger rule 6 was incomplete;
4. `twv_undefined`: at some decision instant the read set was complete and `Σ w_i = 0`;
5. `no_trigger`: otherwise.

**Engine-side outcomes** (from the journal, reported per variant, not strategy state): signals,
risk rejections by reason, broker rejections, unknown outcomes, fills.

## 7. Entry

- **Order type:** market. `Signal.time = T`, `data_as_of = T`, `entry_price = None`,
  `take_profit` = the target of section 9, `stop_loss` = the stop of section 8,
  `max_hold` = 90 min.
- **`ref`** = `close(d)` (bid), used only for `d_S`, `d_T` and the bounds. It is a decision
  reference, not an executable price.
- **Fill:** unchanged simulator and broker rules (trend_pullback / ORB section 7): a buy
  fills at the ask, a sell at the bid, of the first quote at or after `T + latency`, with
  entry slippage; on bar-only days, the next M1 open with the bar's minimum spread +
  `bar_spread_extra_points` (ADR-0009). Never at the decision bar's close.

## 8. Stop loss (V5, V6)

### 8.1 Buffer and formula

`stop_buffer_points` = **25 points = 0.25 USD/oz**, a fixed constant of this spec, not linked
to `risk.yaml`. Formula and rounding: table 5a.

- **Why 25, not ORB's 20.** A short's stop is triggered by the ask while `Y` is a bid high.
  The recorded per-quote spread p99 inside the two sessions is 20 (`london_morning`) and 24
  (`ldn_ny_overlap`) points (reports/phase_2.md §5). 25 covers both, so a bid re-test of `Y`
  does not stop a short by spread alone except in the top 1% of quotes. Applied to longs too,
  for symmetry. Those statistics come from 2026-06-26 … 10-08, measured before the sealed
  period existed (V17); no new measurement was made.

### 8.2 Minimum (V6)

`d_S ≥ max(500, trade_stops_level + 10)`; otherwise **no trade**: no signal, the session-date
is spent, `stop_below_minimum` +1. The stop is never widened to reach the minimum.

- **What is documented:** reports/phase_3.md §4 measured how often one M1 bar reaches both
  levels, so that the worst-case intra-bar rule decides the trade. Its design: hypothetical
  BUY entries every 30 minutes in the London sessions, stop `s`, target `1.5 s`, held up to
  4 hours. At `s` = 500 points: 0.11% of level exits on bar-only days 2020-11 … 2026-06, 0.5%
  on the 2026 bar days, 0.1% on exact tick days; at 300 points: 0.39%, 2.9% and 1.0%. On the
  exact days, ticks showed the stop really came first in about 70% of the ambiguous cases.
  The report proposed opening Phase 3b if a candidate's share exceeded 1% or best-case
  scoring changed its net expectancy by more than 10%. That proposal was superseded. The
  registered rule (ADR-0009 §9, `aurum.validation.phase3b`; gate 11) is
  `phase_3b_required = worst_case_share > 1% or sign(net_worst) != sign(net_best) or
  verdict_worst != verdict_best`, with `worst_case_share` = flagged ÷ total closed trades of
  the worst-case run (strictly greater than 1%), net P&L rounded to cents (0.00 its own sign),
  the best case a separate linked deterministic replay, and no override.
- **What the 500 minimum is:** an engineering policy that keeps this family's stops in the
  range where that measurement found the share small. It is a measurement constraint, not an
  economic one.
- **What is not verified:** the share for **this** strategy's trades. Its entries come after
  extended moves at session extremes (likely higher short-term volatility than entries every
  30 minutes), and its targets are at `d_T ≥ d_S`, not `1.5 s`. Its own share is measured
  only by gate 11 (worst-case run plus linked best-case replay), and a failure there is a
  fail. The 2026 columns of the measurement lie in the now-sealed period (V17).
- **Consequence:** only triggers at least 475 points above `X` (below `Y`) can trade
  (section 13.1). That selects larger extensions mechanically and may thin the sample
  (section 15).
- **Alternative, not proposed:** a floor-widened stop `min(X − 25 points, ref − 500 points)`
  keeps more setups but places the stop away from the structural level.
- **Always attached broker-side.** Never clamped, shortened or moved.

### 8a. Stop-size cap (V7)

Inputs as recorded in opening_range_breakout section 8a (same pinned snapshot,
`20261010T000046Z.json`, sha256 `0426cf8c…be0c7ee`). **They must be re-read from the pinned
snapshot at freeze (B2, section 18b);** the snapshot was not opened for this draft.

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

- **Sizing distance differs from `d_S`.** The risk engine measures the stop from the
  executable side of the quote at T (`aurum.risk.policy`: `entry_price` is None, so the
  reference is the ask for a BUY, the bid for a SELL). A short's distance is about `d_S`; a
  long's is `d_S` + the spread at T, at most `d_S + 20` under the spread cap. Equity floor at
  which a cap-sized stop still affords 0.01 lot: 20.095 / 0.0025 = **8,038 USD** (short, or
  long with zero spread) up to 20.295 / 0.0025 = **8,118 USD** (long at a 20-point spread).
- **Why 2,000:** it leaves a drawdown of about 19% before cap-sized stops become unaffordable.
  It equals the earlier families' cap because the same arithmetic gives the same answer; a
  reviewer may choose another value below 2,490.
- `d_S > 2,000` → no signal, session spent, `stop_above_cap` +1. Never clamped.

## 9. Target (V8)

- **Target = `TWV(T)`, fixed at decision time T** and rounded toward the entry (table 5a). It
  is written into the `Signal` as `take_profit`, sent broker-side with the stop, and never
  recomputed: not at the fill, not as TWV moves afterwards.
- **Minimum reward:risk:** `d_T ≥ d_S` (inclusive), both measured from the bid reference
  `ref`. Otherwise no signal, session spent, `reward_risk_below_min` +1. Reason: section 13.
  After the spread and costs the executable ratio is below 1 (section 13.2).
- Not an R multiple: the hypothesis names the mean as the reversion point, so a fixed R
  target would test a different idea.

## 10. Timeout and hold bound (V9)

### 10.1 Rule

- `max_hold = 90 minutes` from `Signal.time` (`Engine`: the deadline is
  `Signal.time + max_hold`, the decision instant, not the fill; rebuilt from the persisted
  signal after a restart). At the deadline the engine **requests** a market close (TIMEOUT).
  The backtest runner wakes at every pending deadline.
- **Explicit bound (new in revision 2).** With latest decision `E − 15` (section 6.1), every
  deadline is at most `E + 75 min`. The spec fixes these bounds in London wall clock:

  | Session | Latest decision | Latest TIMEOUT request | Bound | Bound's reason |
  |---|---|---|---|---|
  | `london_morning` | 10:45 | 12:15 | **12:15 London** | 15 min before the earliest London time of 08:30 New York (12:30, mismatch weeks); before `ldn_ny_overlap` starts (13:00) |
  | `ldn_ny_overlap` | 16:15 | 17:45 | **17:45 London** | 15 min before the earliest London time of 14:00 New York (18:00, mismatch weeks), the FOMC statement time |

  The implementation must check, at construction and for every date it trades, that
  `E − 15 min + max_hold ≤ bound` for both sessions, and refuse to run otherwise (a
  configuration error, not a silent clip). A change to `sessions.yaml` therefore cannot move
  a deadline past a bound without a spec change.

### 10.2 Demonstration across DST regimes

New York is 5 h behind London in normal weeks and 4 h behind in the two mismatch periods (US
summer time starts on the second Sunday of March, UK on the last Sunday of March; UK winter
time starts on the last Sunday of October, US on the first Sunday of November). Times in
London:

| Regime | Latest London deadline | 08:30 NY | Margin | Latest overlap deadline | 10:00 NY | 14:00 NY (FOMC) | Margin to FOMC | Force-flat |
|---|---|---|---|---|---|---|---|---|
| UK winter, US winter (normal) | 12:15 | 13:30 | 75 min | 17:45 | 15:00 | 19:00 | 75 min | 20:00 |
| UK summer, US summer (normal) | 12:15 | 13:30 | 75 min | 17:45 | 15:00 | 19:00 | 75 min | 20:00 |
| March mismatch (US summer, UK winter) | 12:15 | 12:30 | 15 min | 17:45 | 14:00 | 18:00 | 15 min | 20:00 |
| Oct/Nov mismatch (US summer, UK winter) | 12:15 | 12:30 | 15 min | 17:45 | 14:00 | 18:00 | 15 min | 20:00 |

In the evaluated period (2020-12-15 … 2025-09-30) FOMC statements fall in mismatch weeks on
exactly these eight dates, the complete list: 2021-03-17, 2021-11-03, 2022-03-16,
2022-11-02, 2023-03-22, 2023-11-01, 2024-03-20 and 2025-03-19 (`risk.yaml` dates; DST rules
as above). There the statement is at 18:00 London.

Revision 1 used the latest m5 decisions 10:55 and 16:25, giving deadlines 12:25 and 17:55:
also before 12:30 and 18:00, with a 5-minute margin, and m15 and m5 had different latest
decisions. Revision 2's setup window (V13) moves both to 10:45 and 16:15.

### 10.3 What the timeout guarantees and what it does not

- **Guaranteed (by the rule and the code):** the TIMEOUT close is **requested** no later than
  12:15 London (`london_morning` positions) and 17:45 London (`ldn_ny_overlap` positions), in
  every DST regime, given the bound check of section 10.1.
- **Not guaranteed:** that the position is **closed** by then.
  - Backtest: the close reaches the broker at `deadline + 200 ms` and fills on the first quote
    then (bar-only days: the open of the M1 bar it arrives in). With no quote within 60 s it
    is rejected and retried at every following M5 close while exposure remains
    (`aurum.backtest.runner`). A data gap can therefore carry a position past the bound.
  - Demo: a disconnect or broker rejection delays the close until the engine's next
    successful step. The broker-side stop and target stay attached throughout.
- **Not covered by the timeout:** overlap positions can be open across the 10:00 New York
  release (15:00 London normal, 14:00 mismatch). The news blackout blocks entries only. This
  exposure is accepted and stated, as in the earlier families.

### 10.4 Three separate controls

| Control | What it does | What it does not do |
|---|---|---|
| Timeout (`max_hold`, section 10.1) | Requests a close 90 min after `Signal.time` | Does not look at news, sessions or the cutoff |
| News blackout (section 12) | No decision instants in a window; no setup bar touching one; the risk engine rejects entries in a window | Never closes or modifies a position |
| Force-flat (engine, `sessions.yaml`) | Closes everything from `min(20:00 London, market close − 15 min)` | Not reached on normal days (17:45 < 20:00) |

None is used to justify removing another. On early-close dates force-flat can come first: in
the evaluated period, the cutoff is 17:31 London on 2021-05-31 and 2021-07-05 (12:46 New
York closes) and 17:45 London on 2021-09-06 and 2025-07-04 (13:00 New York closes), computed
from `configs/data.yaml`, not from bars. These four are the complete list of evaluated dates
with a cutoff at or before 17:45 London; the next earliest are 17:46 (2021-02-15,
2021-11-25) and 17:50 (2021-01-18). The risk engine's 45-minute entry cutoff therefore never
binds in the evaluated period: the earliest cutoff, 17:31, stops entries at 16:46, after the
latest decision instant (16:15).

- **Disclosure:** the author knows that most ORB exits were timeouts. 90 is set by the
  calendar bounds above, not by those results; the knowledge is still listed in section 18.

## 11. Invalidation

- **Of a setup** (no position yet): section 6.2 (broken, replaced, expired, spent).
- **Of a position: none in 0.1.0.** Exits are stop, target, timeout and force-flat. The stop
  sits beyond the setup extreme; a second "back through the extreme on a close" exit would be
  a second, untested rule. A regime change after entry does not close the position.
- Simultaneous or ambiguous exits: delegated unchanged to the simulator (ADR-0009 §5) and, on
  demo, to the broker.

## 12. Session constraints and signal priority

1. **Decision instants:** section 6.1. Sessions `london_morning` and `ldn_ny_overlap`, both
   kept (gate 9 needs both; neither is restricted, see section 18).
2. **Risk engine gates, unchanged:** entry cutoff (`min_minutes_before_cutoff` 45 before
   force-flat), news blackout, spread caps (`max_spread_points` 20, `max_spread_atr_fraction`
   0.15 with the 100-bar M15 ATR), `max_trades_per_day` 3, daily and weekly loss budgets, the
   consecutive-loss cooldown, one position at a time.
3. **News (V12).** `aurum.sessions.news.NewsBlackout`, the same object and `risk.yaml`
   windows the risk engine uses (`[start, end)` New York time: 08:20–08:45 and 09:50–10:15
   every weekday; 13:30–15:00 on FOMC statement dates).
   - A blackout instant is not a decision instant: it neither signals nor spends
     (`blackout_skipped`). As in ORB O3, the strategy never emits a signal the risk engine
     must reject by construction. A bar closing at a blackout instant still counts for
     trigger rule 3 at later instants.
   - A bar whose closed interval `[open_time, close_time]` touches a window is not a setup:
     the hypothesis is about uninformed flow, and a scheduled release is information. Bars
     after the window are eligible; a release-driven move that continues past 08:45 or 10:15
     New York is not excluded (section 18).
   - Which instants and setup bars this removes: section 6.1.
4. **Force-flat** at `min(20:00 London, market close − 15 min)`. Session ends do not close
   positions.
5. **Priority.** Each variant runs as its own strategy, run and account. At most one signal
   per session-date, so at most 2 per date; `max_trades_per_day` 3 never binds. A London
   position's close is requested by 12:15, before the overlap's first decision instant
   (14:05 at the earliest); if that close failed, an overlap signal is rejected by the risk engine (exposure),
   with no queueing or reversal.

## 13. Geometry, executable eligibility and cost

Three separate layers. Only layer 13.1 is decided by this strategy; 13.2 is decided at T by
the risk engine and the broker from quotes; 13.3 is the realised outcome.

Notation (points of `point` = 0.01 USD/oz as recorded, to be re-read at freeze; all
strategy levels are on the bid):

- `X` / `Y` — setup extreme (bid low / bid high of `b_s`), on the price grid.
- `ref` — decision reference, the bid close of `d`.
- `stop`, `target` — the levels of sections 8 and 9.
- executable entry — ask (long) or bid (short) of the fill quote, ± 5 points slippage.

### 13.1 Necessary geometry (decision time, bid bars only)

Long; `X` on the grid, so `stop = X − 0.25` exactly:

1. `d_S = (ref − stop) / 0.01 = (ref − X) / 0.01 + 25`.
2. Minimum: `d_S ≥ 500` ⇔ `ref − X ≥ 4.75 USD`. Cap: `d_S ≤ 2,000` ⇔ `ref − X ≤ 19.75 USD`.
3. Reward:risk: `d_T ≥ d_S` ⇔ `target − ref ≥ ref − X + 0.25`.
4. Adding 3 to `ref − X` on both sides: `target − X ≥ 2 × (ref − X) + 0.25`.
5. With 2 at its smallest (`ref − X = 4.75`): `target − X ≥ 9.50 + 0.25 = 9.75 USD`
   (975 points). In general `target − X ≥ 2 × d_S × 0.01 − 0.25`.
6. `target = floor(TWV(T))` on the grid, so `target ≤ TWV(T)`; therefore
   **`TWV(T) − X ≥ 9.75 USD` is necessary for any long signal.** The bound is reached only at
   `ref = X + 4.75` and `target = ref + 5.00`.

Short, mirrored: `stop = Y + 0.25`, `d_S = (Y − ref) / 0.01 + 25`, `target = ceil(TWV(T))`,
**`Y − TWV(T) ≥ 9.75 USD` necessary for any short signal.**

What this is and is not:

- It is necessary, not sufficient: the trigger rules, `ref − X ≤ 19.75`, and the
  rounding of `TWV(T)` must also hold.
- It concerns `TWV(T)`, not `TWV(Ts)`. The setup's deviation rule uses `TWV(Ts)` and
  `k_dev × ATR(Ts)`. When `k_dev × ATR(Ts)` is well below 9.75 USD, the deviation rule admits
  setups that cannot trade; they can still be triggered and **spend** the session
  (`stop_below_minimum` or `reward_risk_below_min`). In such periods `k_dev` acts mostly
  through which setups spend, not through which trades are taken (V11). How often this
  happens is unknown without counts (section 15).

### 13.2 Executable eligibility (at T, from quotes; not known from bars)

A signal that passes 13.1 becomes an order only if, at T:

- the risk engine approves: spread ≤ 20 points and ≤ 0.15 × ATR; quote ≤ 10 s old; at least
  45 min before the cutoff; outside a blackout; no exposure; loss budgets and cooldown allow;
  sized volume ≥ 0.01 lot with the sizing distance of section 8a;
- no earlier order outcome is unresolved;
- the broker accepts: a quote within 60 s of `T + 200 ms`, and the stop and target at least
  `trade_stops_level` from the closing-side quote: the bid for a long, the ask for a short
  (MT5 rule; `aurum.backtest.broker.SimBroker._stops_vs_quote`).

Executable distances (long, spread `s` at the fill, 5 points slippage): entry ≈ `ref + s + 5`,
so risk to the stop ≈ `d_S + s + 5` (+ 5 stop slippage) and reward to the target
≈ `d_T − s − 5`. Short: entry ≈ `ref − 5` (bid), risk ≈ `d_S + 5` (+ 5), reward ≈ `d_T − 5`,
but its stop and target trigger on the ask, i.e. `s` earlier (stop) and `s` later (target) in
bid terms. At `d_T = d_S = 500` and `s = 6`, a long's executable reward:risk is
489 / 516 ≈ 0.95 before commission. Price drift between `ref` and the fill adds to this in
either direction.

### 13.3 Cost-adjusted outcome

- **Spread:** real bid/ask on exact tick days; bar-only days (the whole development period):
  the bar's minimum spread + `bar_spread_extra_points` (2), ADR-0009. Never a constant.
- **Commission:** 2.25 USD per lot per side (4.50 round trip) = 4.5 points of price per
  round trip.
- **Slippage:** 5 points on market entries, market closes and stop exits; none on targets
  (`aurum.backtest.broker`: take-profits fill at their price).
- **Stress:** spread ×1.5 and ×2.0 re-runs before any pass (gate 5).

**Each side pays the spread once.** A long pays it at entry (buys at the ask). A short sells
at the bid and pays it at exit: its stop is reached `s` earlier and its target `s` later in
bid terms. Revision 1 added an extra spread for a short's target exit; that double-counted it
and is corrected here.

**Zero-edge cost.** If price moved between the levels with no edge, a trade ending at the
stop or target would lose on average about `s` + 4.5 commission + 5 entry slippage + 5 ×
P(stop) points: **≈ 18 points at the median spread `s` = 6** (reports/phase_2.md §5, M5
decision instants), 15.5–20.5 depending on the exit. That is 3.1–4.1% of `d_S` at 500 points
and 0.8–1.0% at 2,000. A wider spread raises it point for point.

**Break-even hit rate** for stop-or-target exits at `d_T = d_S = D` and `s` = 6:

- Long: target probability under no edge `D / 2D` = 50%; break-even
  `p* = (D + s + 14.5) / (2D + 5)` = **51.8% at D = 500**, 50.4% at D = 2,000.
- Short: target probability under no edge `(D − s) / 2D` = 49.4% at 500 (the ask offset);
  break-even `p* = (D + 14.5) / (2D + 5)` = 51.2% at 500.
- Both sides need about **1.8 percentage points** of hit rate above their no-edge rate at
  D = 500, and about 0.45 at D = 2,000. Larger `d_T` lowers `p*`.

Timeouts and force-flats end between the levels; their share and value cannot be stated
without data. **What this shows:** at the allowed distances costs do not dominate the
geometry. **What it does not show:** that the hit rate exceeds `p*`, or that enough setups
meet 13.1 and 13.2 together.

## 14. Parameters and search budget (V11)

| Name | Unit | Default | Range | Grid | Why |
|---|---|---|---|---|---|
| `k_dev` | M15 ATR(14) | 1.5 | 1.0–2.0 | {1.0, 1.5, 2.0} | Below 1 ATR the deviation is within one average M15 true range of the mean, the noise the hypothesis does not address; above 2 the setup becomes rare by construction. Judgment, no data. See 13.1 for how it interacts with the 9.75 USD geometry |
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
| sides | long and short, table 5a | 5a |
| mean | TWV, M5 typical bid price × `tick_count`, session-anchored | 3.1 |
| `atr_period`, ATR window | 14, 100 M15 bars ending at `Ts` | 3.2 |
| `er_bars` | 12 (13 H1 closes) | 4 |
| setup window | `A + 60 min ≤ Ts ≤ E − 45 min` | 5 |
| structural level | strict new session extreme | 5 |
| `setup_expiry_minutes` | 30 | 6 |
| trigger | close beyond the previous decision bar's far side | 6 |
| uses per session-date | 1, spent at the first trigger | 6.3 |
| `stop_buffer_points` | 25 | 8 |
| `min_stop_points` | `max(500, trade_stops_level + 10)` | 8 |
| `stop_cap_points` | 2,000 | 8a |
| target | `TWV(T)` | 9 |
| `min_reward_risk` | 1.0 (`d_T ≥ d_S`) | 9 |
| `max_hold_minutes` | 90 | 10 |
| hold bounds | 12:15 / 17:45 London | 10.1 |

## 15. Expected trade count

- **Not estimated in this text.** No count had been run when revision 4 was written. The B1
  feasibility diagnostic (section 18c), authorised 2026-10-11, produces descriptive counts of
  geometry-eligible signals as separate evidence; they are not trades and are not written back
  into this section without a recorded, data-informed revision.
- **Upper bound:** one per session-date, so at most 2 × the number of trading dates.
- **Requirements:** ≥ 30 closed trades per OOS fold, ≥ 200 concatenated, ≥ 30 per side and
  per session (gates 1, 8, 9), and ≥ 100 prior trades for a point to be selectable for a fold
  (section 16). The training-only window before fold 1 is 9.5 months, so fold 1 needs some
  grid point with at least 100 trades in it (10.5 a month on average).
- **Main risk:** the 500-point minimum and `d_T ≥ d_S` together need `TWV(T)` at least
  9.75 USD beyond the extreme (13.1); the rejection trigger and the one-use spend remove more.
  An insufficient sample is a **fail**, never a reason to relax a rule.

## 16. Pre-registered evaluation (carried forward)

Carried forward unchanged from opening_range_breakout 0.1.0 section 16 (itself trend_pullback
A1's), with only the grid names changed. Gate 8 applies as written: both sides are defined
(table 5a). **Where this section is silent, the frozen ORB 0.1.0 section 16 text applies
verbatim** (with this family's parameter names). In particular these five details are
preserved:

- no point with ≥ 100 trades over the whole development period → no frozen configuration,
  and the variant cannot go to Phase 6;
- a neighbour with no trade in folds 1–4 has no net expectancy and counts as not > 0
  (gate 10);
- gate 7 removes all trades of the calendar month (UTC, by entry) with the largest net P&L;
- gate 5's ×2.0 spread re-run is reported, not gated;
- if both variants pass every gate, both go to Phase 6.

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
- **No purge or embargo:** every TIMEOUT close is requested by 17:45 London and force-flat
  is at 20:00 London at the latest, before midnight UTC in every DST regime.
- **Reported:** as ORB section 16, with this family's counters and statuses (section 6.5),
  the engine-side outcomes, the `d_S`, `d_T` and `TWV(T) − X` (`Y − TWV(T)`) distributions,
  the sessions `london_morning` / `ldn_ny_overlap`, and long / short. Win rate is reported,
  never a criterion.
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
OOS net expectancy and drawdown, never win rate, and read as dependent results (6.4).

**Sealed-test threshold** (Phase 6): net expectancy > 0, net PF ≥ 1.05, ≥ 50 trades.

**Compatibility flags for review** (nothing changed silently):

- Gate 10's neighbour count works on the 3 × 2 grid as on ORB's 2 × 3.
- The 100-prior-trade selection minimum and gate 1 may not be reachable if setups are rare
  (section 15). Under the rules that is a fail; flagged so it is not a surprise.
- Gate 9 slices by the session of the entry decision time; a London position's close is
  requested before the overlap session starts (section 10).

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

**How demanding gate 12 is — an approximation, not a target and not a probability.**
Assumptions: N = 66 and V = 6.293e-04 held fixed (the ORB assessment's inputs, so
SR0 = 0.0597 per day); T = 1,032 days; the candidate's daily skew 0 and kurtosis 3 (normal).
Then DSR ≥ 0.95 needs a daily SR ≈ 0.111, or ≈ 1.76 annualised (× √252). With skew 0.47
and kurtosis 4.11 (ORB m5's values) it is ≈ 0.110. With N = 78 and the same V, SR0 rises to
≈ 0.061 and the requirement to ≈ 0.113. These are hand calculations from the formula in
`walkforward.deflated_sharpe`, not a run, and V will change when the new trials enter it.
The figure says how high the unchanged bar sits under those assumptions. It says nothing
about how likely this family is to reach it, and no rule here was chosen to reach it.

**Proposed Phase 5 stop rule (V14).** vwap_reversion is the third and last family the
MASTER_PROMPT plans for Phase 5. If **neither variant survives the registered evaluation**:

- the planned three-family lab ends, and the Phase 5 exit gate is recorded as **not met**;
- `reports/phase_5.md` writes up the findings of all three families (charter kill criterion);
- no fourth family, rescue revision of any family, or ML is started without a separate
  owner decision recorded in `docs/STATUS.md`.

## 17. Decision traces (worked examples) — synthetic

**All values are invented** to check the arithmetic; no data was read. A real-data trace is
an implementation-stage check (section 18a) and needs its own approval.

### 17.1 Long, m15

Date: a UK-winter trading date, `london_morning`, `A` = 07:00 London = 07:00Z,
`E − 45` = 10:15Z. Variant `m15`, defaults (`k_dev` 1.5, `er_max` 0.30).

| Condition | Values | Result |
|---|---|---|
| Setup bar `b_s` | 08:15–08:30Z, `Ts` = 08:30Z; low 1990.50, high 1994.80 | |
| Setup window | 08:00Z ≤ 08:30Z ≤ 10:15Z | true |
| Regime | ER(08:30Z) = 0.22 ≤ 0.30 | true |
| Deviation | TWV(Ts) 2002.00 − 1990.50 = 11.50 ≥ 1.5 × ATR 6.00 = 9.00 | true |
| New session low | 1990.50 < 1992.30 (min low of 07:00–08:15Z bars) | true |
| Blackout in `[08:15, 08:30]Z` | 03:15–03:30 New York | none |
| Trigger bar `d` | 08:30–08:45Z, T = 08:45Z ≤ Ts + 30; `p` = `b_s`; low 1991.00, close 1995.60 | |
| Not broken | 1991.00 ≥ X 1990.50 | true |
| Rejection | 1995.60 > high(`b_s`) 1994.80 | true |
| Room to the mean | 1995.60 < TWV(T) 2001.40 | true → **spent at 08:45Z** |
| Stop | 1990.50 − 0.25 = 1990.25 → floor → 1990.25; `d_S` = 535 | 500 ≤ 535 ≤ 2,000 |
| Target | floor(2001.40) = 2001.40; `d_T` = 580 | 580 ≥ 535 |
| Geometry check | target − X = 10.90 ≥ 2 × 5.35 − 0.25 = 10.45 | consistent |
| Decision | | `Signal(BUY, market, time 08:45Z, stop 1990.25, take_profit 2001.40, max_hold 90 min)` |
| Timeout request | 08:45Z + 90 min = 10:15Z ≤ bound 12:15 London | |

Same setup with TWV(T) = 1999.40: `d_T` = 380 < 535 → no signal, `reward_risk_below_min`,
session spent. With ATR 3.00, TWV(Ts) 2000.00, a setup low of 1995.40 (deviation 4.60 ≥ 4.50)
and a rejection close at 1997.60, the stop is 1995.15 and `d_S` = 245 →
`stop_below_minimum`, session spent: a setup that passed `k_dev` but could not meet 13.1.

### 17.2 Short, m5

Date: a UK-summer trading date (normal week), `ldn_ny_overlap`, `A` = 13:00 London = 12:00Z,
`E − 45` = 15:45 London = 14:45Z. Variant `m5`, defaults.

| Condition | Values | Result |
|---|---|---|
| Setup bar `b_s` | 13:15–13:30Z, `Ts` = 13:30Z; high 2412.40, low 2408.10 | |
| Setup window | 13:00Z ≤ 13:30Z ≤ 14:45Z | true |
| Regime | ER(13:30Z) = 0.27 ≤ 0.30 | true |
| Deviation | 2412.40 − TWV(Ts) 2400.20 = 12.20 ≥ 1.5 × ATR 7.00 = 10.50 | true |
| New session high | 2412.40 > 2410.95 (max high of 12:00–13:15Z bars) | true |
| Blackout in `[13:15, 13:30]Z` | 09:15–09:30 New York | none |
| First instant T = 13:35Z | `p` = M5 13:25–13:30Z (low 2409.80); `d` high 2412.10, low 2408.00, close 2410.40 | rejection 2410.40 < 2409.80 false → no trigger, not spent |
| Second instant T = 13:40Z | `p` = M5 13:30–13:35Z (low 2408.00); `d` high 2411.30, low 2406.50, close 2406.90 | |
| Not broken | highs 2412.10, 2411.30 ≤ Y 2412.40 | true |
| Rejection | 2406.90 < low(`p`) 2408.00 | true |
| Room to the mean | 2406.90 > TWV(T) 2400.353 | true → **spent at 13:40Z** |
| Stop | 2412.40 + 0.25 = 2412.65 → ceil → 2412.65; `d_S` = 575 | 500 ≤ 575 ≤ 2,000 |
| Target | ceil(2400.353 on the grid) = 2400.36; `d_T` = 654 | 654 ≥ 575 |
| Geometry check | Y − target = 12.04 ≥ 2 × 5.75 − 0.25 = 11.25 | consistent |
| Decision | | `Signal(SELL, market, time 13:40Z, stop 2412.65, take_profit 2400.36, max_hold 90 min)` |
| Timeout request | 13:40Z + 90 min = 15:10Z = 16:10 London ≤ bound 17:45 London | |
| Broker | the stop triggers when the ask ≥ 2412.65; the target when the ask ≤ 2400.36 | |

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
- **Statistics measured on dates now sealed (V17).** The Phase 2 spread statistics
  (2026-06-26 … 10-08) and the Phase 3 intra-bar table (columns up to 2026-06 and the exact
  days 2026-06-26 … 10-08) were measured before the sealed period was defined (2026-10-10)
  and lie wholly or partly inside it. They are cost and fill-structure figures, not P&L, and
  the same figures already set `max_spread_points` and `bar_spread_extra_points`. V5 (buffer
  25) and V6 (minimum 500) cite them; so do the cost illustration of section 13.3 (median
  spread 6 → zero-edge cost ≈ 18 points, 0.8–4.1%, the break-even rates and the 1.8-point
  hurdle given as V8's reason, also in section 2) and, qualitatively, the release spread
  spikes of sections 3.1 and 18. No rule value depends on the section 13.3 figures. Using
  them is a disclosed exception, not an inspection of the sealed period for this family.
- **Choices those results could have influenced, and how they were made:**
  - **Fading moves at all.** The planned family predates every result, but two momentum
    families losing makes reversion look attractive after the fact. A reversion family is
    not the sign-reversal of a losing one: costs are paid on both, not refunded.
  - **Both sessions and both sides are kept (V16).** No session, side or DST restriction is
    made, although London and short trades lost in both earlier families.
  - **90-minute timeout:** set by the calendar bounds (section 10); the ORB timeout share is
    known.
  - **Target at the mean:** fixed by the planned hypothesis; ORB's low target-hit share is
    known.
  - **Not influenced by data:** the TWV definition, ER, the grid values, the trigger, the
    one-use rule, the setup window. The buffer (spread statistics) and minimum (ambiguity
    table) use recorded measurements, not P&L.
- **Hypotheses derived from viewed results — not part of this spec and not independent:**
  New-York-only or long-only variants of any family; ORB with other exits or a re-gridded
  range; trend_pullback without `london_morning`; fading London breakouts; DST or season
  filters. Any of them would be a new post-results hypothesis whose trials add to N.
- **Proxy risk.** If quote activity is unrelated to traded volume, TWV is an activity-weighted
  mean (section 3.1). A change in the broker's tick generation over 2021–2025 would move the
  weights without any market change; it cannot be checked from the stored bars alone.
- **Release weight in TWV.** In normal weeks the 08:30 New York release falls inside the
  overlap accumulation period; its quote burst can dominate the early TWV.
- **Bid-only mean and levels.** TWV, setups and triggers use bid prices. Each side pays one
  spread (section 13.3); a short's levels trigger on the ask.
- **Unscheduled news and release continuation.** Only the scheduled windows are excluded.
  A move that continues past the window end, or an unscheduled event, is the case where
  reversion is expected to fail and the stop is the only protection. Overlap positions can be
  open across the 10:00 New York release (section 10.3).
- **Session-extreme selection.** Setups occur only at new session extremes with an extension
  of at least `k_dev` ATR, and trades only where `TWV(T)` is at least 9.75 USD from the
  extreme: larger moves on more volatile days. This is mechanical and stated in advance.
- **Volatility drift.** The ORB evidence recorded wider ranges in 2025; with ATR-scaled
  deviations and absolute stop bounds, the share of setups that can trade will change across
  folds.
- **Sample dependence.** At most two trades per date, sharing a macro backdrop; m15 and m5
  share setups (section 6.4).
- **Intra-bar uncertainty is not verified for this strategy** (section 8.2); gate 11 measures
  it.
- **Tester parity is unresolved** (docs/STATUS.md, Phase 3). The simulator has not been
  compared with the MT5 Strategy Tester. Whether research may continue before that is an
  owner decision required before any trial (section 18b, B5); this spec does not make it.

## 18a. Checklist before any trial (implementation and analysis tests)

Each item is a test or a recorded check, written and green before launch. Per the lean-suite
rule, cases go in tables or hypothesis properties inside a few tests, not one test each.

**Rules (synthetic bars, `tests/unit`):**

- [ ] TWV: weighted mean exactly as section 3.1 (fsum, integer weights); zero-weight bar
      excluded and counted; `Σ w = 0` → undefined; a missing M5 slot → `data_gap` for every
      later instant of the session; reset at each session anchor; bars before `A` never
      included; the value at `Ts` and at T uses exactly the bars of the table in 3.1.
- [ ] Anchors, setup window, decision instants and hold bounds in every DST regime (UK
      winter/summer, both mismatch periods) against the tables of 6.1 and 10.2; blackout
      instants and excluded setup bars as in 6.1; early-close clipping; a `sessions.yaml`
      change that would break a hold bound is refused.
- [ ] ER: formula, 13-bar requirement, zero denominator, a window spanning the daily break
      and a weekend.
- [ ] ATR at `Ts` equals `indicators.atr` over the 100 M15 bars ending at `Ts`.
- [ ] Setup boundaries, both sides: deviation equal to `k_dev × ATR` passes; equal to the
      prior session extreme fails (strict); `Ts = A + 60` and `Ts = E − 45` eligible,
      `A + 45` and `E − 30` not; blackout-touching bars; ambiguous setups.
- [ ] Setup life cycle: a setup with `Ts = T` is not active at T; expiry at exactly `Ts + 30`
      (eligible) and one bar later (not); breaking by any bar in `(Ts, T]`, including one
      closing at a blackout instant; replacement only by a setup; missing `p`.
- [ ] Mirror property (hypothesis): reflecting all prices about a constant swaps long and
      short decisions, levels and statuses exactly (with the rounding directions of 5a).
      float64 reflection is not exact in general (typical prices divide by 3, TWV and ATR
      divide), so the property either uses inputs whose reflected derived values are exactly
      representable, or discards generated cases with any comparison at or within float
      tolerance of equality. Equality boundaries stay covered by the explicit boundary tests.
- [ ] Spend and one use; spent on a geometry failure; a missed or late call after the spend
      returns nothing; restart before and after the spend (stateless re-walk).
- [ ] Stop/target rounding (float noise, off-grid); `d_S` 499/500/2,000/2,001 and the
      stops-level minimum; `d_T = d_S` passes, `d_T = d_S − 1` fails; the 9.75 USD bound of
      13.1 is reached exactly and not crossed.
- [ ] Signal fields: `time`, `data_as_of`, `take_profit`, `max_hold` 90 min, reasons and
      features in numbers.
- [ ] Variant identity: on the same bars both variants find the same setups; decision
      instants as in 6.4.
- [ ] Diagnostics: each instant counted once however often the strategy is called; every
      session-date ends in exactly one status.
- [ ] Causality property test (hypothesis): changing or adding bars after T never changes a
      decision, TWV, ER or ATR at T.
- [ ] Grid: exactly 6 declared points per variant; params accept only declared values.
- [ ] Warm-up: merged history `{H1: 13, M15: 100}`, loaded by bar count; refused when short.
- [ ] Architecture tests: no adapter, execution or broker import.
- [ ] Mutation check of the rule comparisons, survivors explained.

**Data and environment (each needs its own approval, section 18b):**

- [ ] Section 8a re-read from the pinned snapshot (at B2).
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
| B0 | Review of this draft; decisions V1–V17 (**decided 2026-10-11**: all accepted, V17 explicitly) | owner | no |
| B1 | Feasibility diagnostics (optional): descriptive counts on development bars ≤ 2025-09-30, no fill, exit or P&L; any rule change after them is listed as data-informed (**authorised 2026-10-11** with rulings R1–R6, section 18c) | separate approval | yes, development only |
| B2 | Pinned-snapshot read, then freeze 0.1.0: the file's sha256 is checked against `backtest.yaml` `symbol_snapshot`; only the section 8a inputs and `trade_stops_level` are read; they are recorded in section 8a before the freeze | owner; the snapshot read needs the owner's explicit authorisation | pinned symbol snapshot only, no market data |
| B3 | Implementation and deterministic tests (synthetic data only) | separate approval | no |
| B4 | Real-data trace check (no P&L, no ledger) | separate approval | yes, development only |
| B5 | Prerequisites: tester-parity decision (complete it, or proceed without it with the reason recorded); analysis script committed and tested; ledger backup | owner | no |
| B6 | Run the 12 declared trials (one command per variant, no automatic retry) | separate approval | yes, development only |
| B7 | Read-only first-stage assessment | separate approval | run outputs and ledger, read-only |
| B8 | Decision on the assessment | owner | — |
| B9 | Later validation, each separately: stress re-runs (gate 5), best-case replay (gate 11), frozen-configuration neighbourhood (gate 10), like-for-like null baseline, Phase 6 sealed opening | separate approval each | yes |

## 18c. B1 feasibility diagnostic (authorised 2026-10-11)

The owner authorised B1 on 2026-10-11 and ruled on the six ambiguities the diagnostic raised.
This section records the access rules, the rulings and the intended run **before any market
data is read**. Nothing in it changes a trading rule. R1–R5 are written into sections 5, 6.2
and 6.5, where they also bind the implementation.

**Rulings.**

| # | Topic | Ruling | Where |
|---|---|---|---|
| R1 | Geometry-failure precedence | Minimum, then cap, then `d_T < d_S`; the first failure is the sole status and increments only its counter; `would_fail_*` combination flags kept apart, not counters | 6.5 |
| R2 | Setup life cycle | One terminal outcome; breaking precedes replacement; a same-side newer setup normally breaks the old one; `setups_replaced` = an unbroken setup displaced; alternative "both" counts only as non-normative diagnostics | 6.2, 6.5 |
| R3 | Exact expiry | At `Ts_old + 30` the old setup's final trigger and break are evaluated first; untriggered and unbroken → expired; a new setup at that instant does not replace it | 6.2, 6.5 |
| R4 | Setup closing at the spend | The previous setup and the spend are processed first; a spend stops the session-date at T; a setup closing at T is neither classified nor counted | 6.2 |
| R5 | Ambiguous and regime-off | After rules 1, 5, 6: both sides → ambiguous whatever the regime; one side with rule 2 failing or ER unavailable → regime-off; one side with the regime → setup; mutually exclusive | 5, 6.5 |
| R6 | Final UTC partition | The 2025-09-30 partition is authorised. Load with a predicate excluding `open_time >= 2025-09-30T22:00:00Z` (the market open of trading date 2025-10-01); assert that no returned frame contains such a row; report only the number excluded per timeframe, never their values, and pass none of them into any calculation. No partition dated 2025-10-01 or later is opened | this section |

**Numerical rulings.**

- `point` = 0.01 and `trade_stops_level` = 0 are **provisional recorded B1 inputs**, copied
  from the frozen opening_range_breakout spec (sections 8a and 8). They are not a snapshot
  verification; B2 must still verify both from the pinned snapshot. With any stops level
  ≤ 490 the minimum is 500.
- The ER denominator is summed with `math.fsum` (section 4).
- Official classifications use exact float64 comparisons. Comparisons within 1e-9 of their
  threshold (deviation, new extreme, ER, break, rejection, room to the mean) are reported
  separately; the tolerance never alters an official classification.
- Warm-up exactly by bar count: 13 H1 and 100 M15 bars (section 3.3), the fewest whole UTC
  days before 2020-12-15 that hold them; ATR uses exactly the 100 newest M15 bars and ER the 13
  newest H1 closes. The generic ten-calendar-day buffer is not added.
- The output is called **geometry-eligible signals, never trades**: a spend that passes
  sections 8, 8a and 9 on bid bars, before the risk engine, the broker and the quotes. At least
  100 prior geometry-eligible signals is necessary, not sufficient, for a point's eventual
  selection (section 16).

**Method and run.**

- Script: `scripts/vwap_reversion_feasibility.py`, read-only. It refuses to start unless the
  worktree is a clean commit (recorded as the methodology commit) and this file is revision 4
  (git blob checked). It then runs its synthetic tests and checks the R6 cut against the
  market calendar, all before any market data is read. It imports no ledger, backtest,
  execution, risk, strategy, MT5 or snapshot module (asserted).
- Inputs: canonical XAUUSD M5, M15 and H1 bid bars, files selected by UTC date in
  2020-11 … 2025-09-30 (warm-up by bar count for M15 and H1; M5 from 2020-12-15), sha256 of
  every file recorded before reading and re-checked after the run.
- Intended command (repository root):
  `uv run --no-sync python scripts/vwap_reversion_feasibility.py`. On the VM `uv run` cannot
  start from the agent's process (os error 448), so the equivalent launch is the venv's base
  interpreter with `-S` through the `venv_exec.py` wrapper (sha256 `7714dc0e…`, verbatim in
  `docs/strategies/evidence/opening_range_breakout_2026-10-11_dev_launch.md`):
  `& $py -S "$S\venv_exec.py" scripts\vwap_reversion_feasibility.py`. The script is then run a
  second time and both outputs must be byte-identical.
- Outputs: `docs/strategies/evidence/vwap_reversion_2026-10-11_b1_feasibility.md` (report)
  and `…_b1_feasibility.csv`, UTF-8, LF. CSV columns `variant, k_dev, er_max, scope, slice,
  metric, count`, one integer count per row:
  - `variant` is `m15`, `m5`, `both` (shared setup bars), `m15-m5` (overlap) or empty (inputs,
    dates, read set, checks); `k_dev` and `er_max` are the grid point, a `k_dev` pair such as
    `1.0-1.5`, or empty.
  - `scope` is one of `inputs`, `dates`, `read_set`, `calendar`, `setup_bars`, `point`,
    `selection`, `k_dev`, `overlap`, `checks`; `slice` is `all`, a timeframe, a session, a
    fold, a year, a side or a selection boundary.
  - `point` metrics: `session_dates`, `status_<status>`, the section 6.5 counters by name
    (`setups_long`, `setups_short`, `setups_triggered`, `setups_broken`, `setups_replaced`,
    `setups_expired`, `ambiguous_setup`, `setup_regime_off`, `data_gap`, `twv_undefined`,
    `zero_weight_bar`, `blackout_skipped`, `stop_below_minimum`, `stop_above_cap`,
    `reward_risk_below_min`), `spends`, R1's `would_fail_min`, `would_fail_cap`,
    `would_fail_rr` and `would_fail_combination_min<0|1>_cap<0|1>_rr<0|1>`, R2's
    `nonnormative_new_setup_after_break`, `…_after_break_same_instant` and
    `nonnormative_new_setup_at_expiry`, `spend_twv_gap_<ge|lt>_975`, `near_boundary_<name>`.
  - `inputs` metrics include `rows_excluded_open_time_ge_cut` per timeframe (R6).
- **Procedure.** Commit 1 (methodology: this revision, STATUS, script with synthetic tests)
  is committed and pushed before any market data is read. B1 runs only from that exact clean
  commit. If any script or rule change proves necessary after market data is opened, Claude
  stops and reports it before editing or rerunning. Commit 2 adds the report, the CSV and a run
  record (methodology commit, command, input identities and hashes, excluded-row counts,
  output hashes, the second-run byte-identity check), and STATUS records the descriptive
  result. Claude then stops.
- **Consequence.** Every later choice about this family is made knowing the B1 counts and is
  listed as data-informed (section 18b).

## 19. Change log

| Version | Date | Change | Reason |
|---|---|---|---|
| 0.1.0 (draft) | 2026-10-11 | Initial draft: tick-weighted session mean (VWAP proxy), H1 efficiency-ratio regime, M15 setup at a new session extreme ≥ `k_dev` ATR from the mean, rejection trigger on M15 or M5 bars, stop 25 points beyond the extreme within 500–2,000 points, target at the mean with `d_T ≥ d_S`, 90-minute timeout, one use per session-date, 12-trial grid, gates carried forward from ORB 0.1.0, cumulative N = 66 + n, proposed Phase 5 stop rule. Decisions V1–V15 open. No data read | Owner authorised a documentation-only draft (2026-10-11) after rejecting opening_range_breakout 0.1.0 |
| 0.1.0 (draft, rev. 2) | 2026-10-11 | Short rules written out (table 5a; V16). Setup window `Ts ≤ E − 45` (V13): same setups and latest decision `E − 15` in both variants. Exact decision-instant, blackout and deadline tables (6.1, 10.2); explicit hold bounds 12:15 / 17:45 London with a construction-time check (V9); the timeout claim corrected from "closes" to "requests a close". Causal rules made explicit: TWV bars per rule, target fixed at T, setup life cycle, spend after risk or broker rejection (6.2, 6.3). Variant differences stated (6.4). Geometry split into necessary / executable / cost (13); the 9.75 USD derivation written out; the double-counted short spread removed (costs 0.8–4.1%, not 0.8–5.4%); break-even rates recomputed. Long sizing distance includes the spread (8a floors 8,038–8,118). Intra-bar evidence and its limits (8.2); statistics from dates now sealed disclosed (V17). Short synthetic trace. No data read | Owner's B0 review (2026-10-11) |
| 0.1.0 (draft, rev. 3) | 2026-10-11 | B0 corrections, documentation only. Decision instants defined as `A + 60 min < T ≤ E − 15 min`; other OPEN calls return and count nothing (fixes `twv_undefined` at `T = A`). Session-date statuses given an explicit precedence (6.5). V17 disclosure completed (V5, V6, the 13.3 cost illustration behind V8, section 2, the release-spike remarks). B2 made an explicitly authorised, hash-checked pinned-snapshot read of the 8a inputs and `trade_stops_level` only, recorded before freezing. Wording: closing-side stops-level quote (13.2); the exact ADR-0009 §9 gate-11 rule (8.2); `atr_period` source (3.2); M15 slot completeness at non-M15 instants; ORB §16 applies verbatim where section 16 is silent, five details restated; MASTER_PROMPT paraphrase labelled; `setups_expired` excludes replaced setups; clipped session ends; complete FOMC-mismatch and early-cutoff lists, 45-minute cutoff never binding; mirror-property test constraint (18a). B0 decision recorded (V1–V17 accepted). **No trading rule changed; no market data, ledger or snapshot was read.** Audit and patch: `docs/strategies/evidence/vwap_reversion_2026-10-11_b0_audit.{md,patch}` | Owner accepted the B0 audit (2026-10-11) |
| 0.1.0 (draft, rev. 4) | 2026-10-11 | B1 diagnostic and access clarifications, documentation only. Rulings R1–R6 recorded (new section 18c) and written into sections 5 (R5 classification), 6.2 (R2–R4 order within an instant) and 6.5 (R1 precedence and `would_fail_*` flags; counter definitions); ER denominator summed with `math.fsum` (section 4); numerical rulings (provisional `point` and `trade_stops_level`, exact comparisons with near-boundary reporting, warm-up exactly by bar count, "geometry-eligible signals, never trades"); intended command and output schema; section 15 wording; B1 marked authorised in 18b. **No market data had yet been read** (no bars, ticks, ledger rows or snapshot) **and no trading rule changed.** Not frozen | Owner's B1 authorisation and rulings (2026-10-11) |
