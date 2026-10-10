# Status — read this second (after CLAUDE.md)

Where the project stands and what comes next. Update it whenever a phase, a decision or the
work queue changes. History lives in `reports/phase_<n>.md`; rules in `CLAUDE.md`; the full spec
in `docs/MASTER_PROMPT.md`; the code map in `docs/ARCHITECTURE.md`.

_Last updated: 2026-10-10._

## Phases

| Phase | State | Report |
|---|---|---|
| 0 Charter, scaffold | Done | `reports/phase_0.md` |
| 1 MT5 connectivity | Done | `reports/phase_1.md` |
| 2 Data foundation | Done | `reports/phase_2.md` |
| 3 Simulator | Done except **tester parity** (waiting for the user's MT5 Strategy Tester export) | `reports/phase_3.md` |
| 4 Risk + execution | **Approved** by the user's reviewer 2026-10-10 | `reports/phase_4.md` |
| 5 Strategy lab | **Approved with conditions; not started.** Blocked on the pre-Phase-5 queue below | — |

## Work queue (in order)

### A. Before Phase 5 starts (reviewer decisions, 2026-10-10)

1. **Prospective loss budget** (decision on Phase 4 known issue 4). Entry is allowed only if the
   new trade's worst-case risk fits what is left:
   `remaining = limit − realised loss − open risk`; require `new trade risk ≤ remaining`.
   Compute risk exactly as sizing does (`risk/policy.py::size_volume`: stop distance × value +
   round-trip commission + stop slippage). Apply to **daily 1%** and **weekly 2.5%**.
   Without it, 0.9% lost + a 0.25% trade can end the day at 1.15%.
   - Fault test: 0.9% already lost, then a signal → **rejected**.
   - Update ADR-0010 and the risk section of `CLAUDE.md`.
   - Open question to settle in code review, not by guessing: "open risk" of an open position
     = distance from current price to its stop × value (+ exit commission). Ask if unclear.
2. **Parity check additions** (`backtest/parity.py`, `aurum backtest parity`). Keep tolerances
   (1 s fill time, 1 point price, same exit reason, $0.02 money). Add: trade count must match
   **exactly**; total net P&L within a small **cumulative** tolerance. Never loosen a tolerance
   to pass — find the cause. Doc for the tester run must state: same commission, leverage,
   deposit, and "Every tick based on real ticks" (`docs/strategies/reference_fixed_time.md`).
3. **Cancel inside a bar** (Phase 4 known issue 7): flag it as a worst-case case now
   (an `IntrabarRule`, like `close_bar`) instead of an unflagged assumption. Golden scenario.
4. **Phase 3b (Dukascopy) trigger** — record in ADR-0008/0009 and enforce in the Phase 5 report:
   open Phase 3b if a candidate has **> 1%** of trades decided by the worst-case rule, **or** if
   flipping every flagged trade to its best case changes the **sign of net P&L or the pass/fail
   verdict**.
5. **Stop-size cap in every strategy spec.** At 0.25% of $10k ($25 budget) a stop needs
   ≲ $20–25 at 0.01 lot or the risk engine rejects every trade (all 1,524 14:30 SELLs of the
   reference strategy were rejected: 3,000 points ≈ $30). Add the cap to
   `docs/STRATEGY_SPEC_TEMPLATE.md`.
6. **CI** (GitHub Actions or similar: ruff, format check, mypy, pytest on Linux/macOS). "Windows
   only, no CI" is the weakest point of Phase 4. MT5 tests auto-skip off Windows.

### B. Later, tracked

- Adopted positions (state file lost) get no timeout/invalidation, only force-flat + kill
  switch. Acceptable on demo; **fix before any live pilot** (Phase 4 issue 5).
- Margin estimate vs `order_calc_margin` — Phase 8 (Phase 4 issue 1).
- Watchdog scheduling waits for the Phase 7/8 trader loops (Phase 4 issue 6).
- Add each year's FOMC dates to `configs/risk.yaml` in time (`aurum doctor` warns 60 days
  ahead; current list ends 2026-12-09) and future market holidays to `configs/data.yaml`.

### C. User actions (not Claude's)

- **Tester run**: second MT5 in portable mode (`terminal64.exe /portable`, separate folder, own
  data dir) so the live collector keeps running. Run `mql5/AurumReference.mq5`, export deals CSV
  + HTML report, then `aurum backtest parity 20261009T221629Z_reference_fixed_time_m5_be6c94
  <deals.csv>`.
- **Mac checks**: `uv run ruff check && uv run mypy && uv run pytest` (only ever run on the VM).

## Phase 5 conditions (from the reviewer; part of the gate)

- Write each strategy spec (`docs/STRATEGY_SPEC_TEMPLATE.md`) **before any code**, including the
  stop-size cap. The reviewer offered to draft the trend-pullback spec — ask the user.
- Every trial in the ledger. A **hold-out period** never touched during search.
- Report **gross vs net**. Apply a **Deflated-Sharpe-style** correction to the best candidate.
- Judge M15-direct vs M5-trigger on **net expectancy and drawdown after costs**, not win rate.
- **Null baseline**: `reference_fixed_time` with the policy risk engine, 2020-11-12 … 2026-10-08,
  run `…233326910002Z_reference_fixed_time_m5_ae3d92`: 1,520 trades, **−$381.76**, max DD 5.4%.
  This is what no edge looks like after costs; judge every candidate against it.

## Environment facts

- This checkout runs on the **Windows 11 ARM VM** (`C:\code\aurum`), venv `C:\venvs\aurum`
  (x64 Python under emulation), data `C:\aurum-data` (`AURUM_DATA_DIR`). The live tick
  collector runs there continuously; don't stop it.
- Backtest runs: `reports/runs/<run_id>/` (git-ignored). Trial ledger: `<data>/ledger/`.
- Test suite: ~380 tests, ~70 s with coverage, 92% coverage. Keep it lean (see `CLAUDE.md`).
