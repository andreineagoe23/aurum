# Phase 4 report: risk and execution core

- **Date:** 2026-10-10
- **Exit gate:** **PASSED** (§6). Phase 5 has not started.
- Commits: 03d84d0 … 813bc0a on `main`.
- Phase 3 is still open on one item, the MT5 Strategy Tester parity check, which needs your
  tester run. It is unaffected by Phase 4: the parity run reproduces byte for byte on the new
  engine (§4).

## 1. Your decisions (2026-10-09), as implemented

| Question | Decision | Where |
|---|---|---|
| What counts against the 0.25% budget | Stop distance, round-trip commission and configured stop slippage | ADR-0010, `risk/policy.py::size_volume` |
| Daily 1% / weekly 2.5% loss limits | Equity-based (closed + open P&L of Aurum's trades), London trading day and week; blocks entries, open positions keep their stop | ADR-0010 |
| News | Every weekday no entries 08:20–08:45 and 09:50–10:15 New York; FOMC statement dates 13:30–15:00 New York | `risk.yaml news_blackout`, `sessions/news.py` |
| `aurum kill` | Writes the KILL file; in DEMO also connects and flattens Aurum's items at once | ADR-0011, `cli.py` |

The 50 FOMC dates (2020-11-05 … 2026-12-09) were checked against federalreserve.gov by a
research subagent. No corrections and no unscheduled decisions in range. The 14:00 ET time is
the Fed's standard practice; the pages don't state it. `aurum doctor` warns 60 days before the
list runs out.

Your Phase 4 notes:
- Intents older than 60 s are never sent: `broker.max_intent_age_seconds`, enforced in the
  order manager and again in `MT5Broker`.
- Ownership is magic 7020261 **and** the "AU" prefix. Deals now carry `magic`, so deal history
  follows the same rule.
- Force-flat on early-close days uses the same `TradingCalendar` in every mode.
- The news blackout is above.

## 2. What was built

| Area | Module | What it does |
|---|---|---|
| Risk engine | `risk/policy.py` | `PolicyRiskEngine`: sizing with costs (floored to `volume_step`, never rounded up to `volume_min`). Gates: session phase, minutes to cutoff, news, connection, stale quote, spread in points and vs M15 ATR, one position, trades/day, daily/weekly loss, session losing streak, stop side, margin. Every failure is listed |
| Indicators | `features/indicators.py` | Wilder ATR over completed bars (latest 100 M15 bars) |
| Intent store | `execution/intents.py` | SQLite (`<data>/state/intents.sqlite`) or in-memory. Each intent is recorded with its signal before it is sent; a restart restores signals and deadlines |
| Order manager | `execution/orders.py` | The engine's only send path. Age limit; lost reply = UNKNOWN (never resent); broker-agnostic duplicate lookup over positions, orders and deals; `flatten_owned` |
| Reconciliation | `execution/reconcile.py` | Adopts unknown positions, attaches a missing stop or closes the position, cancels unexplained pending orders, resolves or expires unresolved intents |
| Engine | `execution/engine.py` | Reconciles on startup, after a disconnect, while an outcome is unresolved, and after every action. Applies the kill switch. A disconnect ends the step and the next step retries. No new entry while an outcome is unresolved |
| Kill switch | `execution/killswitch.py`, `aurum kill` / `unkill` | `<data>/KILL` |
| Watchdog | `monitoring/watchdog.py`, `aurum watchdog` | Atomic heartbeat file. Kill switch (plus flatten in DEMO) if the heartbeat is missing or stale during the trading window |
| Backtests | `backtest/pipeline.py` | The policy engine by default; `--risk fixed` only for the tester parity check |
| Simulator fix | `backtest/broker.py` | `close_bar` rule (§5) |

## 3. Fault injection (`tests/fault/test_execution_faults.py`, full engine + real policy)

| Scenario | Result |
|---|---|
| Reply lost mid-order | Journalled UNKNOWN; reconciled to FILLED in the same step; the next signal meets "one position at a time"; **1 order at the broker** |
| Reply lost, then the line stays down | Two DISCONNECTED steps; on reconnect: reconcile first, no duplicate, and the restored signal still times out at 08:20 |
| Send never reached the broker | UNKNOWN, then EXPIRED once older than 60 s; trading resumes |
| Outcome still unresolved at the next decision | `entry_blocked`, nothing sent |
| Quote 30 s old | Rejected: "stale quote: 30.0 s old" |
| Restart with an open position (same SQLite file) | Signal restored; TIMEOUT at 08:00 + 30 min by the new process |
| Restart with the state file lost | Position adopted; FORCE_FLAT at 19:00 |
| Restart already past the cutoff | Flattened on the first step |
| Cutoff passes while disconnected | Nothing possible at 19:00 or 19:05; FORCE_FLAT on reconnect at 19:10 |
| Kill switch | Flattens (KILL_SWITCH), blocks entries, resumes after `unkill` |
| A strategy signalling at every M5 close | 2 losses lock the morning window; the afternoon trade makes 3/day; the news windows show; **every sent intent carries the approved volume** |

Unit tests also cover: the stale intent never sent; a stale resubmit recognised as a
duplicate (position and pending order); a missing stop attached, or the position closed if the
stop is unknown; an unexplained pending order cancelled; manual trades never touched by
`flatten_owned` or `aurum kill`. The CLI test runs a DEMO kill against the fake terminal.

## 4. Real-data runs (`reference_fixed_time`, every run in the trial ledger)

| Run | Risk | Period | Trades | Net P&L | Max DD | Worst-case |
|---|---|---|---|---|---|---|
| `…231142716293Z_…_1c0066` | fixed 0.10 | parity window | 38 | -2,062.98 | — | 0 |
| `…233154669819Z_…_e2cbe2` | fixed 0.10 | 2020-11-12 … 2026-10-08 | 3,048 | -2,512.98 | 74.9% | 1 (`close_bar`) |
| `…233326910002Z_…_ae3d92` | **policy** | 2020-11-12 … 2026-10-08 | **1,520** | **-381.76** | **5.4%** | 1 (`close_bar`) |

- **Parity is unaffected.** The parity window on the Phase 4 engine with `--risk fixed` has
  byte-identical trades, equity and journal to `…be6c94`.
- **What the policy engine did over six years:**
  - It rejected all **1,524** 14:30 SELLs: "sized volume 0 < volume_min". A 3,000-point stop
    needs about 30 USD at 0.01 lot, against a 25 USD budget.
  - It rejected **4** BUYs for spread over 20 points.
  - It traded the rest at 0.02 lots.
- Drawdown fell from 75% (fixed 0.10 lot on 10,000 USD) to 5.4%. That's the point of the
  policy. It says nothing about any edge: this strategy has none by design.

## 5. Simulator bug found and fixed (Phase 3 addendum)

The policy run's journal showed one `close_failed`: 2021-09-16, a timeout close that found its
position already stopped out. On bar-only days a requested close arriving inside an M1 bar was
processed after the whole bar. A stop inside that bar was taken **unflagged**. A target was
**credited** although the close at the open almost certainly came first, which is optimistic.
In a two-scenario test the target case showed +3.70 where the conservative answer is -5.30.

Fix (6c202cb, ADR-0009): process only up to the bar's open, then:
- a gap through the stop at the open is a STOP, not flagged;
- a stop inside the bar is taken first (worst case) and flagged `close_bar`;
- a target only inside the bar is not credited; the close at the open is used, flagged
  `close_bar`.

Two new golden scenarios fail on the old code and pass now. Effect: the reference strategy has
1 worst-case trade in 3,048 (P&L unchanged; the optimistic case never occurred in this
history). The parity window (ticks) is unchanged.

## 6. Exit gate

| Item | Result |
|---|---|
| Strategies cannot bypass any limit | **Passed.** Strategies can't import the broker, execution or adapters (`test_architecture.py`). `OrderIntent` can't exist without an approved decision or exceed its volume. Brokers refuse unapproved intents. The engine sends only via `OrderManager`. The always-signalling test shows the session lockout, trades/day and news limits holding, and every sent volume equal to the approved one |
| Force-flat after simulated disconnect / restart | **Passed:** cutoff during disconnect, restart without state, restart past the cutoff |
| Property: risk after rounding ≤ ceiling | **Passed:** `tests/property/test_risk_properties.py` (200 examples by default, 1,000 under the `ci` profile), which also checks that one more step would break the budget |
| Kill switch + watchdog | **Passed:** engine, CLI (BACKTEST and DEMO on the fake terminal), heartbeat verdicts |
| Fault suite: disconnect mid-order, stale ticks, restart with open position, cutoff during disconnect | **Passed** (§3) |

Tests: **599 passed, 11 skipped** (MT5 integration, gated). Ruff, format and strict mypy are
clean. Coverage: 92% total; engine 97%, order manager 90%, reconciliation 92%, policy 94%,
watchdog 98%, SimBroker 94%. All on **Windows (this VM)**. **Not run on the Mac**: please run
`uv run ruff check && uv run mypy && uv run pytest` there. There is still no CI.

## 7. Known issues and limits

1. **Margin estimate.** The risk engine uses `lots × contract × price ÷ account leverage`. MT5
   may apply symbol-specific margin rates. The broker's own check still rejects; Phase 8
   should compare against `order_calc_margin`.
2. **Commission rounding at half cents** (0.02 lots → 0.045 booked as 0.04 here) is unmeasured
   at the broker. The difference is at most 0.01 per fill.
3. **Loss limits act at decisions.** A loss between decisions shows at the next one. Each trade
   is bounded by its broker-side stop. Hitting a limit doesn't close a position (your choice).
4. **No prospective budget check.** A new trade is allowed while the realised loss is below the
   limit, even if its own risk could take the day past it (for example 0.9% lost plus a 0.25%
   trade). Tell me if you want the remaining budget to have to cover the new trade's risk.
5. **Adopted positions** (state file lost) are managed only by force-flat and the kill switch.
   They have no timeout or invalidation.
6. **Watchdog not schedulable yet.** The trader loops that write heartbeats come in Phase 7/8.
   Scheduling `aurum watchdog` now would raise MISSING alarms every trading day.
7. **Cancel in a bar.** On bar-only days, since the `close_bar` fix, a cancel arriving inside an
   M1 bar takes effect at that bar's open, before a pending entry that the bar would trigger
   later. The order is unknowable on bars and the case isn't flagged. It's rare, and no
   strategy uses pending orders yet; Phase 5 will flag it if a strategy does.

## 8. Open from Phase 3

- **MT5 Strategy Tester parity:** waiting for the deals CSV and HTML report
  (`docs/strategies/reference_fixed_time.md`). Then `aurum backtest parity
  20261009T221629Z_reference_fixed_time_m5_be6c94 <csv>`.
- **Tolerances:** please confirm or tighten them (1 s, 1 point, same exit reason, 0.02).
- **Phase 3b (Dukascopy) rule:** your decision on the 1% worst-case-share threshold.

## 9. Next steps

- **You:** review this phase. Send the parity files. Answer the open questions in §7.4 and §8.
  Run the checks on the Mac.
- **Phase 5 (not started, waiting for your approval):** the baseline strategy lab. Trend
  pullback first, with M15 direct entry vs M15 setup + M5 trigger and everything else held
  constant. Then opening-range breakout and VWAP/structure reversion. Each gets a written spec
  (`docs/STRATEGY_SPEC_TEMPLATE.md`) before code, broad pre-declared parameter ranges, every
  trial in the ledger, and gross vs net cost attribution. Indicators needed: EMA, session
  VWAP, opening range, structure levels.
