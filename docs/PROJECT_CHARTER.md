# Aurum Project Charter

Version 1. Source: `docs/MASTER_PROMPT.md` sections 0 and 2. Changes to the mandate require a new ADR and a revision of this charter (see Change control).

## Purpose

Aurum is a research and automated-trading platform for intraday XAUUSD on MetaTrader 5. It exists to:

- Collect clean, replayable gold market data.
- Test simple, explainable intraday strategies with realistic costs, no look-ahead and walk-forward validation.
- Kill weak ideas early.
- Promote only surviving strategies through shadow mode, then demo trading. A tiny live pilot is a much later, optional step and is out of scope for v1.

Profitability is an empirical question. The system is built to report what the data shows, not to make backtests look good.

## Non-goals

- Not a "magic AI bot". There is no claim that a profitable strategy exists.
- No live money in v1. Everything runs on a Fusion Markets MT5 Zero demo account.
- No deep learning or reinforcement learning in v1. ML is allowed only later, as a meta-filter on already-validated deterministic setups (Phase 9).
- No overnight or weekend exposure.
- No dashboards, auto-promotion of models or strategies, or features that enable live trading beyond the guard in `aurum.core.safety`.

## Trading mandate (frozen for v1)

| Item | Rule |
|---|---|
| Broker / account | Fusion Markets, MT5 Zero account type (raw spread + commission). Demo only. |
| Instrument | XAUUSD. The broker's exact symbol name is discovered at runtime, never hard-coded. |
| Style | Intraday only. No overnight, no weekend exposure. Every position and pending order is closed or cancelled before the daily force-flat cutoff. |
| Timeframe roles | H1 = regime and directional context. M15 = setup and invalidation. M5 = entry trigger. Ticks/M1 = execution realism only. |
| Candles | Only completed candles are used. Higher timeframes are joined to lower ones with an as-of join on close time. |
| Sessions (initial) | London morning and London-New York overlap, researched separately. Windows in `configs/sessions.yaml`. |
| Exposure | One XAUUSD position at a time. |
| Exits | Broker-side stop loss on every trade, plus target, setup invalidation, max holding time, or force-flat cutoff, whichever comes first. |
| Costs | Commission $4.50 per 1.0 lot round trip ($2.25/side), configurable. Variable spread from real bid/ask. Configurable slippage model. |
| Model policy | Deterministic rules first. ML only later as a meta-filter. No deep learning / RL in v1. |

## Timeframe roles

Each timeframe has exactly one responsibility. They are not three votes.

| Timeframe | Single responsibility |
|---|---|
| H1 | Regime and directional context |
| M15 | Setup and invalidation |
| M5 | Entry trigger (variant B only; variant A enters on M15 close) |
| Ticks / M1 | Execution realism (fills, spread, intra-bar ordering) |

## Completed-candle rule

Bars are keyed by open time and become visible only at close time (`close_time = open_time + timeframe`). A decision never uses a bar that has not closed.

Example: a decision at 10:35 UTC sees the H1 bar that opened at 09:00 and closed at 10:00. The H1 bar opened at 10:00 is still forming and is not visible. On M5 the latest visible bar at 10:35:00 is the one that opened at 10:30 and closed at 10:35; at 10:34:59 it is the one that opened at 10:25. Higher-timeframe values are attached to lower-timeframe rows with an as-of join on close time.

## Provisional risk limits

Safety controls for demo, not optimised and never tuned for performance. Authoritative values are in `configs/risk.yaml`.

| Setting | Value |
|---|---|
| `risk_per_trade_pct` | 0.25 (% of equity) |
| `max_open_positions` | 1 |
| `max_trades_per_day` | 3 |
| `daily_loss_limit_pct` | 1.0 |
| `weekly_loss_limit_pct` | 2.5 |
| `consecutive_loss_cooldown` | 2 losses, locked out for the rest of the session |
| `max_spread_points` | `null` until calibrated in Phase 2 from collected spread statistics |
| `max_spread_atr_fraction` | 0.15 (spread vs M15 ATR) |
| `min_minutes_before_cutoff` | 45 |
| `stale_quote_seconds` | 10 |

`max_spread_points` is `null` by design. While it is `null`, DEMO trading refuses to start. It is set only from measured spread statistics (median, p90, p99 by session).

## Sessions

Defined in `configs/sessions.yaml`, Europe/London wall-clock, converted to UTC per day with `zoneinfo` (DST-aware). Initial values: `london_morning` 07:00-11:00, `ldn_ny_overlap` 13:00-16:30, `pre_session_minutes` 30, `no_new_entries_after` 19:30, `force_flat_at` 20:00, trading days Monday to Friday. Do not copy these numbers into other documents; the config is the source.

## Phase gates

| Phase | Exit gate |
|---|---|
| 0 Charter, scaffold, controls | `uv run pytest` green on Mac; `aurum doctor` works; any decision at any timestamp can be described without subjective words. |
| 1 MT5 connectivity | 20 clean reconnect cycles, demo open/modify/close succeeds, full reconciliation, structured logs. |
| 2 Data foundation | Re-running ingestion + aggregation yields identical file hashes; no unexplained gaps inside trading sessions. |
| 3 Simulator | Synthetic edge cases behave as specified; a basic replay matches MT5 Strategy Tester within an agreed tolerance. |
| 4 Risk and execution | Strategies cannot bypass any limit; force-flat works after simulated disconnect/restart; risk after rounding never exceeds the ceiling. |
| 5 Baseline strategy lab | At least one family has positive net expectancy across multiple chronological out-of-sample folds, not dependent on one month, direction or parameter point. |
| 6 Honest validation | Survives conservative costs, stable neighbourhood, no single-period dependence, passes the pre-registered sealed-test threshold (opened once). |
| 7 Shadow mode | Several weeks of stable operation with zero causality, session, duplicate or data-integrity defects, across more than one volatility regime. |
| 8 Demo execution | Meaningful demo sample, no unresolved incidents, execution divergence within predeclared tolerance, demo expectancy compatible with the conservative backtest. |
| 9 ML meta-filter | Only if a strategy is validated. Improves multiple untouched folds and the sealed comparison, stays calibrated, survives cost stress, gain is not just fewer trades. |
| 10 Combination | Beats the best single component on drawdown/stability without hiding a weak strategy. |
| 11 Live pilot | Out of scope. Nothing is built beyond the safety guard. |

Work proceeds one phase at a time. Each phase ends with passing tests, updated docs, ADRs for important decisions and `reports/phase_<n>.md` stating whether the gate passed.

## Success definition

The system tells the truth. A successful outcome is a platform whose results can be trusted, including a result of "no strategy survives". Making a backtest look profitable is not success.

## Kill criteria

At the end of Phase 5, if no strategy family meets the exit gate: stop, write up findings, and revise hypotheses. Do not add ML to rescue a failing baseline.

## Change control

The mandate table, timeframe roles, safety rules and phase gates are frozen for v1. Any change requires a new ADR in `docs/DECISIONS/` and a revision of this charter, committed together. Risk limits in `configs/risk.yaml` may be changed for safety reasons, never to improve backtest performance, and each change is noted in the commit message.
