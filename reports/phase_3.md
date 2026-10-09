# Phase 3 report: event-driven simulator

> **Addendum (2026-10-09, during Phase 4).** The Phase 4 full-history run found a bars-mode
> case this report missed. A requested close (timeout, force-flat) arriving inside an M1 bar
> that also reached the stop took the stop unflagged. If the bar reached the target instead,
> it credited the target, which is optimistic. Fixed in 6c202cb (rule `close_bar`, ADR-0009),
> with two new golden scenarios. Effect on §4: the reference strategy now has **1 worst-case
> trade in 3,048** (2021-09-16; P&L unchanged). The parity window is unaffected (tick days):
> the trades hash is still `c6bc07d2…`.

- **Date:** 2026-10-09
- **Exit gate:** **every item passed except the MT5 Strategy Tester parity check**, which is
  waiting for your tester run (§5). Phase 4 has not started.
- Commits: bc7e81c … 039bf52 on `main`.

## 1. Commission, measured (Phase 2 item 1)

Read with `history_deals_get` on demo **504691** (USD), read-only:

| Deal | Position | Time (UTC) | Side | Volume | Price | Commission | Fee | Swap | Profit |
|---|---|---|---|---|---|---|---|---|---|
| 403504311 | 573971612 | 2026-10-09 16:10:20.436 | SELL (in) | 1.00 | 4192.50 | **-2.25** | 0 | 0 | 0.00 |
| 403504789 | 573971612 | 2026-10-09 16:11:30.319 | BUY (out) | 1.00 | 4191.72 | **-2.25** | 0 | 0 | 78.00 |

- **2.25 USD per lot per side**, exactly as advertised. `broker.yaml` keeps 2.25 and records
  the deal tickets. Because it matches the advertised value, no ADR was written.
- Each deal's commission is booked rounded to the cent. An earlier 0.01-lot round trip on the
  same account booked -0.02 per side, not -0.0225. The simulator rounds the same way.

## 2. Other Phase 2 decisions, applied

- ADR-0008 (Dukascopy) status is **Accepted (deferred)**, with your two conditions: the terms
  allow personal research, and validation passes against Fusion exact days. Not implemented.
- `risk.yaml`: `max_spread_points: 20`, with a note to re-derive it from one month of collector
  data. `max_spread_atr_fraction` kept. DEMO stays disabled until Phase 8.

## 3. What was built

| Area | Module | What it does |
|---|---|---|
| Shared interfaces | `core/models.py` | `MarketEvent`, `Signal`, `RiskDecision`, `OrderIntent`, `ExecutionReport`, `PositionState`, `DealRecord`, plus deal reasons and trade exit reasons |
| No look-ahead | `features/view.py`, `features/asof.py` | `MarketView` shows a strategy only bars with `close_time <= now`; higher timeframes are as-of joined on close time. Property-tested with hypothesis |
| Data | `backtest/market.py`, `backtest/loading.py` | Fusion canonical bars and normalised ticks. Ticks drive fills on exact days (ADR-0007); every other day runs on M1 bars |
| Broker | `backtest/broker.py` | `SimBroker` implements `BrokerPort`. Execution model in **ADR-0009**: next quote at or after `now + latency`, buys at ask and sells at bid, slippage, commission, market/stop/limit orders, volume step/min/max, stops and freeze levels, margin, rejection hooks, worst-case intra-bar rule with a flag on each affected trade |
| Engine | `execution/engine.py` | Mode-agnostic decision loop shared with SHADOW and DEMO: session gating, timeout, invalidation, force-flat, journal |
| Calendar | `sessions/calendar.py` | Force-flat at `min(force_flat_at, market close - buffer)`. The buffer is `market_close_buffer_minutes` (default 15) and is tested at 15 and 30. Covers the daily break and early closes from `configs/data.yaml`, and skips closed dates |
| Replay | `backtest/runner.py` | Wakes at M5 closes, phase boundaries and `max_hold` deadlines |
| Accounting | `backtest/accounting.py` | Gross and net P&L per trade, spread/slippage/commission attribution, MAE/MFE, holding time, exit reason, equity curve, summary |
| Results | `backtest/pipeline.py`, `backtest/storage.py` | Manifest (code commit, config hash, data hash, parameters, symbol spec). Trades, equity and journal as Parquet under `reports/runs/<id>/` with SHA-256 hashes. A trial-ledger row for every run |
| Reference | `strategies/reference.py` | Fixed-time entries (08:00 UTC BUY, 14:30 UTC SELL) with fixed stops and targets. Spec: `docs/strategies/reference_fixed_time.md` |
| Parity | `mql5/AurumReference.mq5`, `backtest/parity.py`, `adapters/mt5/tester.py` | The tester EA, plus `aurum backtest parity <run_id> <deals.csv>` |
| CLI | `cli.py` | `aurum backtest run <strategy> --from --to [--variant m15\|m5] [--latency-ms] [--slippage-points]`, `report <run_id>`, `compare <a> <b>`, `parity <run_id> <csv>` |

**Deviation, stated plainly.** The engine consumes a `MarketView` snapshot at each wake-up, not
a stream of `MarketEvent`s. `MarketEvent` exists and is validated, but it is not on the replay
path. The view enforces the no-look-ahead rule by construction, because nothing after `now` is
reachable from it. SHADOW (Phase 7) will build the same view from live completed bars.

## 4. Real-data runs and the worst-case intra-bar rule

All runs use `reference_fixed_time` (no edge by design), 0.10 lots, and the default costs
(latency 200 ms, slippage 5 points, commission 2.25). Every run is recorded in the trial ledger.

| Run | Period | Trades | On ticks / bars | **Worst-case trades** | Net P&L | PF |
|---|---|---|---|---|---|---|
| `…221804Z_…_m5_acb36b` | 2020-11-12 … 2026-10-08 | 3,048 | 150 / 2,898 | **0 (0.0%)** | -2,512.98 | 0.98 |
| `…221903Z_…_m15_213601` | same | 3,048 | 150 / 2,898 | **0 (0.0%)** | same | same |

- The m5 and m15 runs have **byte-identical trade files**. That is expected: 08:00 and 14:30 are
  closes on both grids, so both decide on the same bid close.
- Costs reconcile by hand. Slippage = 3,048 × 1.00 − 132 target exits × 0.50 = 2,982.00.
  Commission = 3,048 × 2 × 0.23 = 1,402.08.
- Nothing looks too good. PF 0.98 is what a strategy with no edge should show.

**Zero worst-case trades does not mean the question is settled.** The reference stops and
targets ($10/$15 and $30/$45) are far wider than an M1 bar's range. Phase 5 strategies will
use tighter stops, so the share was measured directly (`scripts/intrabar_ambiguity.py`).

Method: hypothetical BUY entries every 30 minutes inside the London sessions, with stop `s`
and target `1.5 s`, held up to 4 hours. "Ambiguous" means the first M1 bar to reach either
level reaches both, so the worst-case rule decides the trade.

| Stop (points) | Bar-only days, 2020-11 … 2026-06 | Of which 2026 bar days (Jan–Jun) | Exact tick days, 2026-06-26 … 10-08 |
|---|---|---|---|
| 200 | 0.83% of level exits | **6.9%** | 2.2% |
| 300 | 0.39% | **2.9%** | 1.0% |
| 500 | 0.11% | 0.5% | 0.1% |
| 1000 | 0.01% | 0.1% | 0.2% |

Resolving the ambiguous bars of exact days with ticks: the stop really did come first in **19 of
25** cases at 200 points, **7 of 11** at 300, 1 of 1 at 500 and 1 of 2 at 1000. "Stop first" is
the right call about 70% of the time, so it is pessimistic but not extreme.

**Assessment.** The share is immaterial for stops of 500 points or more. It becomes material,
at roughly 3–7%, for stops of 200–300 points in the 2026 volatility regime, which is the period
Phase 5 will weigh most. Suggested rule for the Phase 3b decision: **open Phase 3b (Dukascopy)
only if a Phase 5 candidate's `worst_case_share` exceeds 1% of its trades**, or if scoring its
flagged trades as target-first changes its net expectancy by more than 10%. A cheaper interim
option is a "best case" switch, so every run can report both bounds. Not built; tell me if you
want it in Phase 5.

## 5. Parity with the MT5 Strategy Tester (**waiting for your run**)

- EA source: `mql5/AurumReference.mq5`. It compiles with **0 errors, 0 warnings** in this
  machine's MetaEditor. I compiled it only; I did not run the tester.
- Steps: `docs/strategies/reference_fixed_time.md`, section "Steps".
- Window: **2026-09-14 … 2026-10-08**. That is 19 trading days, all exact tick days, with no
  holidays or early closes.
- Aurum side: run `20261009T221629Z_reference_fixed_time_m5_be6c94`, with latency 0 and
  slippage 0. It has 38 trades: 16 stops, 4 targets, 6 timeouts, 12 force-flats, all on ticks.
- Proposed tolerances: fill times ≤ 1 s, prices ≤ 1 point, identical exit reason, and money
  ≤ 0.02. Please confirm them or tighten them.
- Expected explainable differences are listed in the doc: tester commission if it isn't
  applied, stop fills if the tester fills at the stop price rather than the triggering tick,
  and any broker revision of tick history.

**What I need from you:** the `AurumReference_deals.csv` from
`%APPDATA%\MetaQuotes\Terminal\Common\Files`, and the HTML report. Then I run
`aurum backtest parity` and either report it within tolerance or explain each difference.

## 6. Golden replay scenarios (`tests/replay`)

Each scenario runs the full stack: runner → engine → risk stand-in → SimBroker → accounting,
using the repo's real sessions and calendar. Every number is derived by hand in
`scenarios.py`. All 11 pass, and each one is re-run to check that both runs are identical.

| Scenario | Checks |
|---|---|
| gap through stop (ticks / bars) | Stop fills at the gapped bid (ticks) or the bar open (bars), not at the stop price. A gap at an open is certain, so it isn't flagged |
| SL and TP in one bar, with ticks | Ticks show the target came first: TARGET, not flagged |
| SL and TP in one bar, without ticks | Stop assumed first: STOP, flagged `sl_tp_same_bar` |
| spread spike at entry | Fills at the spiked ask (300-point spread); spread cost attributed |
| spread spike, rejected by a hook | `order_rejected: spread 300 points > 50`, no trade |
| early-close force-flat, 15 and 30 min buffer | 2026-07-03, close 17:01 UTC: flat at 16:46 and 16:31 (+ latency) |
| daily break | A force-flat configured inside the break is pulled to 20:44 UTC |
| rejected order | Requote on the first signal; the next one fills |
| minimum stop distance | Stop inside the 500-point stops level is rejected; a wider stop is accepted |

Fixture fix (bcc2692): the two bars-only scenarios started at the 08:00 bar, so at the 08:00
decision no completed bar existed and the risk check correctly found no quote. A quiet 07:59
bar was added. No expected number changed. The requote hook was also made stateless, so
reruns in one process behave the same.

## 7. Tests

- **541 passed, 11 skipped** (the MT5 integration tests, which need the terminal or demo-order
  opt-in). Ruff, ruff format and strict mypy on `src/` are clean. All on **Windows (this ARM
  VM)**. **There is no CI, and I could not run the suite on the Mac.** Please run
  `uv run ruff check && uv run mypy && uv run pytest` there. No Phase 3 code is
  platform-specific: no MetaTrader5 imports outside `aurum.adapters.mt5`, which is enforced by
  `test_architecture.py`.
- Coverage: total 91%. Backtest modules 91–98% (`broker.py` 94%), engine 96%, sessions 97–99%,
  core models 98%, `risk/fixed.py` 91%.
- New in this session: golden replay runner (22 tests); parity comparison, CSV reader and CLI
  (9); SimBroker reads never past `now`, `check_order`, sell limit and sell stop on ticks (5);
  run-id ordering regression (1).

## 8. Exit gate

| Item | Result |
|---|---|
| Commission measured from the 1.00-lot deals and recorded in `broker.yaml` | **Passed**: 2.25/side, deals 403504311 and 403504789 |
| All golden scenarios behave exactly as specified | **Passed**: 11 of 11, each also checked for determinism |
| No-look-ahead property tests pass | **Passed** (`tests/property/test_no_lookahead.py`), plus a unit test that `bars()`/`ticks()` never return data after `now` |
| Re-running a backtest gives identical trade files | **Passed** on real data: `…be6c94` vs `…b99b00` have identical trades, equity and journal hashes (trades `c6bc07d2…`) |
| Parity with the Strategy Tester within tolerance, or each difference explained | **Pending**: needs your tester run (§5) |
| Share of trades relying on the worst-case rule | **Reported**: 0 of 3,048 for the reference strategy. Estimated by stop distance in §4 |

## 9. Known issues and risks

1. **Parity not yet run.** The EA's own UTC conversion (US and UK DST rules in MQL5) is
   untested until then. The comparison converts the EA's raw server times with Aurum's
   `ServerTimeZone`, so a DST mistake in the EA would show up as an entry-time difference.
2. **No spread or loss controls in backtests yet.** `FixedVolumeRisk` is a stand-in: fixed
   0.10 lots, no `max_spread_points`, no daily or weekly limits. Phase 4 replaces it.
3. **Bars-mode latency.** Sub-minute latency can't be resolved on M1 bars, so a bar-day fill is
   the next M1 open at or after arrival.
4. **Commission rounding at 0.10 lots.** The simulator books 0.225 as 0.23 per side. How the
   broker rounds a half cent is unmeasured. The difference is at most 0.01 per fill.
5. **The EA does not model early closes.** That's why the parity window avoids them.
6. A latent bug was found and fixed: run ids had second resolution, so runs started in the same
   second sorted randomly. That made `test_rerun_is_byte_identical` flaky. Ids are now stamped
   to the microsecond (a55e669).

## 10. Next steps

- **You:** run the tester (§5) and send the CSV and HTML report. Confirm or tighten the
  tolerances. Run the checks on the Mac. Decide the Phase 3b rule (§4).
- **Me, after the parity result:** compare, explain or fix each difference, update this report,
  then stop for approval before Phase 4.
- **Phase 4 (your notes, not started):**
  - Reject `OrderIntent`s older than about 60 s.
  - Ownership requires magic 7020261 **and** the "AU" comment prefix.
  - Force-flat uses the early-close time on US-holiday early-close days (the backtest calendar
    already does; live must match).
  - A dedicated news blackout around high-impact US releases (08:30 and 10:00 ET, and FOMC),
    not only the spread filter.
  - Replace `FixedVolumeRisk` with equity-based sizing, loss limits, lockouts, and spread and
    staleness checks.
