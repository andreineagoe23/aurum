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

### 8a. Stop-size cap (required)

Guidance: the risk engine sizes down to `volume_step` and never rounds up to `volume_min` (ADR-0010). A stop too wide to afford at `volume_min` is therefore rejected on every trade. The cap makes that limit a rule of the strategy, fixed before any result is seen. Derive the cap for **this** spec from the inputs below. There is no global cap: nothing goes in `risk.yaml`. Every input names its source. Read symbol values from a spec snapshot (`<data>/specs/<symbol>/<stamp>.json`, written by `aurum mt5 symbols` and the collector), never from memory. If an input is not authoritative, write `UNRESOLVED` and do not finish the spec.

| Input | Value | Unit | Source |
|---|---|---|---|
| Reference equity | `<10000.00>` | account currency | `<configs/backtest.yaml initial_balance, or the demo account's equity on YYYY-MM-DD>` |
| Account currency | `<USD>` | | `configs/broker.yaml` `account_currency` |
| Risk per trade | `<0.25>` | % of equity | `configs/risk.yaml` `risk_per_trade_pct` |
| Risk budget = equity × % / 100 | `<25.00>` | account currency | computed |
| Symbol point | `<0.01>` | price | spec snapshot `<stamp>.json` |
| `volume_min` | `<0.01>` | lots | spec snapshot |
| `volume_step` | `<0.01>` | lots | spec snapshot |
| Money per price unit per lot = `tick_value / tick_size` | `<100.00>` | account currency per 1.00 price per lot | spec snapshot |
| Commission per lot per side / round trip | `<2.25 / 4.50>` | account currency | `configs/broker.yaml` `commission.per_lot_per_side` |
| Stop slippage | `<5>` points = `<0.05>` price | points, price | `configs/broker.yaml` `slippage.points_per_side` |
| **Ceiling** (minimum-volume affordability) | `<N>` points = `<N × point>` price | points, price | formula below |
| **Strategy cap** (≤ ceiling) | `<N>` points = `<N × point>` price | points, price | chosen; reason: `<...>` |
| Risk at `volume_min` with the stop at the cap | `<...>` ≤ budget `<...>` | account currency | formula below |
| Equity floor of the cap = risk at `volume_min` ÷ (% / 100) | `<...>` | account currency | below this equity the risk engine rejects cap-sized stops |

The calculation is the one `aurum.risk.policy.size_volume` uses:

```text
budget                 = reference_equity × risk_per_trade_pct / 100
risk at minimum volume = volume_min × ( stop_distance_price × money_per_price_unit_per_lot
                                      + round_trip_commission_per_lot
                                      + stop_slippage_price × money_per_price_unit_per_lot )
require                  risk at minimum volume ≤ budget

max_distance_price  = ( budget / volume_min
                      − round_trip_commission_per_lot
                      − stop_slippage_price × money_per_price_unit_per_lot )
                      / money_per_price_unit_per_lot
max_distance_points = floor(max_distance_price / point)     ← always rounded down, never up
ceiling             = max_distance_points points = max_distance_points × point price
```

Rules:

- **Stop above the cap → no signal.** Measure the distance from the entry reference price of section 7 (for a market order, the decision bar's close). If it is greater than the cap, the strategy emits nothing for that setup and records the reason `stop_above_cap`.
- **Never fit the stop to the cap.** A stop is never clamped, shortened or moved just to fit the cap. The stop from the price rule above is the trade's stop, or there is no trade.
- **The cap is a strategy rule.** Changing it, or any input it was derived from, needs a version bump in sections 1 and 19.
- **Phase 5 reports count `stop_above_cap`** for each candidate, next to the trades taken (`docs/VALIDATION_PROTOCOL.md`).
- **The engine can still reject.** It sizes on the equity at decision time and measures from the executable quote (ask for a buy, bid for a sell), not from the bar close. A stop under the cap is rejected (`sized volume … < volume_min`) when equity is below the cap's equity floor, or when the spread pushes the distance over the ceiling. Those rejections are risk rejections, reported separately, never as `stop_above_cap`.

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
