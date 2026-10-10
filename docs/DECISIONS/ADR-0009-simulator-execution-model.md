# ADR-0009: Simulator execution model (next quote, conservative fills, worst-case intra-bar)

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Phase 3 needs one execution model for `SimBroker` that strategy, risk and engine code cannot
tell apart from MT5 (`BrokerPort`), that never uses data from after the decision, and that errs
against the strategy wherever history cannot settle the question. Tick history is exact only
from 2026-06-26 (ADR-0007); before that, back to 2020-11-12, only M1 bars exist.

## Decision

1. **Timing.** An order decided at `now` arrives at `now + latency` (`backtest.latency_ms`,
   default 200). It fills on the first quote with time ≥ arrival; never at the decision bar's
   close. With no quote within `max_quote_wait_seconds` (closed market, gap), it is rejected.
2. **Prices.** Buys at the ask, sells at the bid. Market fills, stop entries and stop-loss exits
   pay `broker.slippage.points_per_side` adversely. Take-profits and limit entries fill exactly
   at their price: no price improvement.
3. **Stops are paid in full on gaps.** On ticks a stop fills at the triggering quote (minus
   slippage), not at the stop price. On bars a bar opening beyond the stop fills at the open.
4. **Bar-only days.** A bar quote is the M1 open (bid), and the ask is the open plus the bar's
   minimum spread plus `backtest.bar_spread_extra_points` (2, measured: reports/phase_2.md).
   Sub-minute latency cannot be resolved, so the fill is the next M1 open at or after arrival.
5. **Intra-bar ambiguity.** If one M1 bar reaches both stop and target, the stop is assumed
   first and the trade is flagged `intrabar_rule = sl_tp_same_bar` (`worst_case = true`). For
   a pending entry triggered inside a bar, the stop is assumed to follow the entry, a target
   counts only when it must have come after it, and the trade is flagged `entry_bar` when that
   assumption decided anything. A requested close (timeout, force-flat, kill) arriving inside
   a bar that also reaches the stop takes the stop (worst case); if the bar reaches only the
   target, the close at the open is assumed first and the target is not credited. Both are
   flagged `close_bar` (added 2026-10-09 in Phase 4, after the full-history run exposed the
   unflagged and target-crediting cases). A cancel (session boundary, kill, reconcile) is
   resolved against the pending entry before the order is deleted (added 2026-10-10, Phase 4
   issue 7): if the bar the cancel arrives in **opened** at or through the entry level, the
   order filled before the cancel (certain, not flagged); if only the bar's **range** reached
   the level, the order of the two is unknown and the entry is assumed first (worst case: an
   unwanted position), flagged `cancel_bar`. The flag stays on the trade until it closes and
   wins over any later rule (a stop or target in the same bar does not replace it). If the bar
   never reached the level the cancel goes through. A cancel that executes on the bar's open
   quote itself (arrival at or before the open) goes through, as on ticks. The freeze level
   applies only to an order still pending after this. On exact tick days, ticks decide and
   nothing is flagged: a trigger on a quote before the cancel's quote fills, otherwise the
   cancel wins.
6. **Costs.** Commission `per_lot_per_side × volume` per fill, rounded to cents and booked
   negative, as the broker does (measured 2026-10-09: 2.25/lot/side; a 0.01-lot fill books
   0.02). Profit is booked to the cent.
7. **Rules from the symbol spec.** Volume min/step/max, `trade_stops_level` (minimum stop, target
   and pending distance), freeze level, margin at the configured leverage, duplicate idempotency
   keys, and pluggable rejection hooks.
8. **Cutoffs.** Force-flat at `min(force_flat_at, market close − market_close_buffer_minutes)`,
   with the market close taken from `configs/data.yaml` (daily break or early close). Closed
   dates are not traded.
9. **Best-case replay and the Phase 3b trigger** (owner, 2026-10-10). Phase 5 reports every
   candidate under two intra-bar policies, `intrabar_policy: worst | best`. `worst` is the
   model above (a candidate passes only on its worst-case verdict). `best` is a **second
   deterministic replay** on the same data, parameters, costs and risk configuration, with only
   the policy changed: target first in `sl_tp_same_bar`, the exit level allowed before the entry in
   `entry_bar`, the close or target before the stop in `close_bar`, the cancel succeeding in
   `cancel_bar` (the trade never exists). It is never made by editing the worst-case trades,
   because a different ordering can remove a trade, move its exit, or change which later
   signals the risk engine accepts. The two runs are linked in the trial ledger. Then
   (`aurum.validation.phase3b`):

   ```text
   phase_3b_required = worst_case_share > 1%
                    or sign(net_worst) != sign(net_best)
                    or verdict_worst != verdict_best
   ```

   with `worst_case_share` = flagged trades ÷ total closed trades of the worst-case run
   (strictly greater than 1%), net P&L rounded to cents (0.00 is its own sign) and `verdict` the
   candidate gate's pass/fail. Phase 3b required means the candidate has not passed; there is
   no override. With flagged trades and no best-case replay the report fails closed. The
   best-case policy itself is built with the Phase 5 runner, not before.

## Consequences

- Backtests are biased against the strategy wherever data is ambiguous. On exact days the stop
  was actually first in about 7 of 10 ambiguous bars (scripts/intrabar_ambiguity.py), so the
  bias is real but bounded. Every affected trade is flagged and counted in the run summary.
- The share of worst-case trades depends on stop distance and the volatility regime:
  ≈0.1% of level exits at ≥500-point stops, up to 2.9–6.9% at 200–300 points on 2026 bar-only
  days (reports/phase_3.md §4). It is reported per run, so Phase 5 can judge it per strategy.
- The MT5 Strategy Tester parity check (docs/strategies/reference_fixed_time.md) checks
  points 1–3 and 6 against an independent implementation.
