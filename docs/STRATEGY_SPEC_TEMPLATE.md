# Strategy Spec Template

Copy this file to `docs/strategies/<name>.md` and fill every section **before** writing any strategy code. A strategy without a completed spec is not implemented.

## Rule: numbers, not adjectives

Every field must be stated with numbers, timestamps, prices or explicit comparisons. A reader must be able to evaluate each condition at a given timestamp and get true or false without judgement.

Banned words (unless defined by a formula in this spec): strong, weak, clean, clear, significant, nice, obvious, choppy, good, bad, healthy, decent, solid, messy, sharp, tight, big, small, around, roughly, about, near.

| Bad | Good |
|---|---|
| "Enter when the trend is strong and the pullback is clean." | "Enter long when H1 close > H1 EMA(50) and H1 EMA(50) slope over 3 bars > 0, and M15 low <= M15 EMA(20) and M15 close > M15 EMA(20)." |
| "Stop just below the recent low." | "Stop = lowest M15 low of the last 8 completed bars minus 0.5 x M15 ATR(14), rounded down to a multiple of `point`." |

Replace every `<...>` placeholder. Delete guidance lines once filled.

---

## 1. Identity

Guidance: unique name, one family, one variant, version bump on any rule change.

- Name: `<snake_case_name>`
- Family: `<trend_pullback | opening_range_breakout | vwap_reversion>`
- Variant: `<m15 | m5>` (m15 = enter on M15 close; m5 = M15 setup + M5 trigger)
- Version: `<0.1.0>`
- Author: `<name>`
- Date: `<YYYY-MM-DD>`

## 2. Hypothesis

- Economic rationale: `<who is on the other side of the trade and why they trade this way>`
- Why it should persist after costs: `<mechanism; expected gross edge in points vs expected cost in points (spread + commission + slippage)>`
- Falsification criterion: `<numeric result that kills the idea, e.g. "net expectancy <= 0 USD/trade in more than 2 of 5 out-of-sample folds">`

## 3. Data used

Guidance: list timeframes and state which bars are visible at decision time. Bars are keyed by open time and visible at `close_time = open + timeframe`. Higher timeframes use an as-of join on close time.

| Timeframe | Fields used | Visible at decision time T |
|---|---|---|
| H1 | `<close, ...>` | Last H1 bar with close_time <= T |
| M15 | `<...>` | Last M15 bar with close_time <= T |
| M5 | `<...>` (variant m5 only) | Last M5 bar with close_time <= T |
| Ticks | `<bid, ask>` | Execution only |

## 4. H1 context (regime rule)

Guidance: one responsibility, regime and direction. Give the formula and thresholds.

`<e.g. long regime: H1 close > EMA(50) AND EMA(50)[t] - EMA(50)[t-3] > 0.0; ATR(14) between X and Y USD>`

## 5. M15 setup

Guidance: one responsibility, setup and invalidation. Define the setup bar and the conditions.

`<conditions with numbers>`

## 6. M5 trigger

Guidance: variant `m5` only. Variant `m15` enters on the M15 close and this section is "not applicable".

`<trigger bar conditions with numbers, max M5 bars allowed after setup before the setup expires>`

## 7. Entry

- Order type: `<market | stop | limit>`
- Price: `<rule>`. Buys fill at ask, sells at bid.
- Decision time: `<close of which bar>`

## 8. Stop loss

- Price rule: `<formula>`
- Minimum distance: stop distance in points >= `trade_stops_level` of the symbol (from `SymbolSpec`) plus `<N>` points.
- Always attached broker-side at order submission.

## 9. Target

`<price rule or R-multiple, e.g. entry + 1.5 x stop distance>`

## 10. Timeout / max holding time

`<N minutes after fill; position closed at market at expiry>`

## 11. Invalidation

Guidance: conditions that cancel a pending setup or close an open position before stop/target.

`<e.g. M15 close below setup_low; H1 regime rule becomes false at an H1 close>`

## 12. Session constraints

- Session(s): `<london_morning | ldn_ny_overlap>` (windows in `configs/sessions.yaml`)
- No entries within `min_minutes_before_cutoff` (`configs/risk.yaml`) of `force_flat_at`.
- Force-flat at `force_flat_at`; all positions and pending orders closed or cancelled.
- Other restrictions: `<e.g. no entries in the first N minutes of the session>`

## 13. Cost assumptions

- Spread source: `<real bid/ask from ticks; never a constant>`
- Commission: $2.25 per side per lot ($4.50 round trip), from `configs/broker.yaml`.
- Slippage: `<N points per side>` (default from `configs/broker.yaml`).
- Stress: spread x1.5 and x2.0 re-runs are mandatory; results at both must be reported.

## 14. Parameters

Guidance: every tunable number appears here. Ranges must be economically justified, grids small.

| Name | Unit | Default | Justified range | Grid step | Why |
|---|---|---|---|---|---|
| `<ema_period>` | bars | `<50>` | `<30-100>` | `<10>` | `<reason>` |

## 15. Expected trade count

- Per week: `<N>`; per session: `<N>`
- Minimum number of trades needed for evaluation: `<N total, N per fold>`, with the reasoning. If expected count is below this, say so and what that implies.

## 16. Pre-registered evaluation

Fixed before any results are seen.

- Metrics: net expectancy (USD/trade), profit factor, max drawdown, trade count, `<...>`
- Pass thresholds: `<e.g. net expectancy > 0 in >= 4 of 6 folds; PF > 1.15 over concatenated OOS trades>`
- Folds: `<anchored | rolling; train length, test length, step, embargo>`
- Sealed-test threshold: `<numeric pass condition on the sealed period; opened once>`

## 17. Decision trace (worked example)

Guidance: pick one real timestamp T. List every condition with the numeric values that made it true or false.

T = `<YYYY-MM-DDTHH:MM:SSZ>`

| Condition | Values | Result |
|---|---|---|
| H1 bar used (close_time) | `<10:00Z, close 2412.35>` | |
| H1 close > EMA(50) | `<2412.35 > 2405.10>` | `<true>` |
| `<...>` | `<...>` | `<...>` |
| Final decision | | `<Signal(long) | no signal>` |

## 18. Known risks / failure modes

`<regimes where it loses, data dependencies, sensitivity to spread, event days, capacity of the sample>`

## 19. Change log

| Version | Date | Change | Reason |
|---|---|---|---|
| `<0.1.0>` | `<date>` | Initial spec | |
