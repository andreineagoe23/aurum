# ADR-0010: Risk policy: sizing with costs, limits from deal history, news blackout

- **Status:** Accepted
- **Date:** 2026-10-09 (owner decisions on the four open questions, same day)

## Context

Phase 4 replaces the fixed-volume stand-in with the risk engine every mode uses
(`aurum.risk.policy.PolicyRiskEngine`). Four choices change how money is risked, so the owner
decided them:

1. what counts against the per-trade budget;
2. how daily and weekly loss limits are measured and enforced;
3. how high-impact US releases are handled;
4. what `aurum kill` does (ADR-0011).

## Decision

**Sizing: stop distance plus costs.**
`lots = floor(equity × risk_per_trade_pct / 100 ÷ cost_per_lot, volume_step)`, capped at
`volume_max`, where `cost_per_lot = stop distance × value per price unit per lot + 2 ×
commission per lot per side + stop slippage (broker.yaml) × point × value`. The loss at a stop
filled with the configured slippage, commission included, never exceeds the budget. A
hypothesis property test checks this after rounding, and also that one more step would break
it. Entry slippage is not budgeted. Below `volume_min` the signal is rejected, never rounded up.

**Loss limits: equity-based, entries blocked.** Loss = Aurum's closed P&L (incl. commission)
since the start of the London trading date (or the Monday of the week) plus the open P&L of
Aurum's positions. The limit is a percentage of the balance at that start, computed as the
current balance minus Aurum's closed P&L since then. Reaching `daily_loss_limit_pct` (1%) or
`weekly_loss_limit_pct` (2.5%) blocks new entries until the next day or week. An open position
keeps its broker-side stop and is not closed. Everything comes from the broker's deal history
(magic and "AU" key), so a restart cannot reset a limit. Manual trades and deposits are not
Aurum's and are ignored.

**Prospective loss budget** (owner decision 2026-10-10, amends the above). A new trade is
approved only if its own worst-case risk fits what is left of each budget, checked separately
for the daily and the weekly limit:

```text
loss to date = -(Aurum's closed P&L incl. commission + open P&L)      (as above)
open risk    = Σ volume × (max(0, (exit price − stop) × side) × value
                           + exit commission per lot + stop slippage × point × value)
remaining    = limit − loss to date − open risk
approve only if new trade risk ≤ remaining
```

New trade risk is the sizing figure (stop distance, round-trip commission, stop slippage) and
stays in `RiskDecision.risk_amount`. Open risk uses the current executable exit price (bid for
a BUY, ask for a SELL), so the move from entry to now, already in loss to date, is not counted
twice; only the exit commission is added, the entry commission being booked. A position
without a stop, or no fresh quote, makes open risk unknown and rejects new risk (fail closed).
The rejection states limit, loss to date, open risk, remaining and new risk. Without this rule
0.9% lost plus a 0.25% trade could end the day at 1.15%. With one position at a time, open
risk is normally 0 when an entry is considered (any exposure already rejects); it is computed
anyway for complete accounting and for multiple positions later.

**Other gates, every one checked and every failure listed:**
- the session phase allows entries;
- at least `min_minutes_before_cutoff` (45) before the cutoff;
- the broker is connected;
- the quote is at most `stale_quote_seconds` (10) old;
- spread ≤ `max_spread_points` (20) and ≤ `max_spread_atr_fraction` (0.15) × M15 ATR(14)
  (Wilder, over the latest 100 completed M15 bars). An uncalibrated cap or a missing ATR
  rejects;
- one position or pending order at a time;
- fewer than `max_trades_per_day` (3) entries today;
- fewer than `consecutive_loss_cooldown` (2) losing trades in a row within the current session
  window. A trade that only breaks even before commission counts as a loss, and the lockout
  ends with the window;
- the stop is beyond the reference price;
- the margin (`lots × contract × price ÷ leverage`) fits `margin_free`.

**News blackout: fixed windows plus FOMC dates** (risk.yaml `news_blackout`, New York wall
clock). Every Monday to Friday, no entries 08:20–08:45 and 09:50–10:15. On listed FOMC
statement dates, no entries 13:30–15:00. The 50 dates from 2020-11-05 to 2026-12-09 were
verified on federalreserve.gov. `aurum doctor` warns when fewer than 60 days of dates remain.
Open positions are not touched.

## Consequences

- Deterministic and backtestable: the same engine runs in BACKTEST, SHADOW and DEMO, and every
  rule depends only on broker state and config.
- A strategy with wide stops can be untradeable at this size. The reference strategy's 14:30
  SELL (3000-point stop) needs about 30 USD at 0.01 lot, against a 25 USD budget on 10,000
  USD, so the policy rejects all 1,524 of them in the 2020–2026 backtest.
- The fixed release windows also block quiet days at 08:30 and 10:00 ET. That costs some
  opportunities, in exchange for needing no event feed.
- Limits are checked when an entry is considered. A loss arriving between decisions shows at
  the next decision. Positions are bounded by their broker-side stops.
- `FixedVolumeRisk` remains only for the MT5 Strategy Tester parity check (`--risk fixed`).
