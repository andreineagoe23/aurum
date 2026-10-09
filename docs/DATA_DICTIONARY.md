# Data Dictionary

Authoritative definitions: `src/aurum/core/models.py`, `enums.py`, `timeframes.py`, `timeutil.py`. The tables below mirror the code field by field; if they disagree, the code wins and this document must be fixed. Stored datasets (Phase 2) are defined in `src/aurum/data/schemas.py` and described in [Stored datasets](#stored-datasets-phase-2) below.

## Conventions

- **Timestamps**: always timezone-aware UTC. Naive datetimes are rejected (`aurum.core.timeutil`). Fields typed `UtcDatetime` accept any aware datetime and store it converted to UTC. `SessionState.local_time` is the one exception: any aware datetime (session-timezone wall clock).
- **Bar timestamps are OPEN time.** A bar covers `[open_time, close_time)` with `close_time = open_time + timeframe duration`. A bar becomes visible to any decision only once `now >= close_time`. Bars are aligned to multiples of their duration since the Unix epoch.
- **Prices**: quote currency per unit, USD per troy ounce for XAUUSD. `Price` type: float, `> 0`, no inf/NaN. Bar prices are bid.
- **Points**: multiples of the symbol's `point`. For a 2-digit XAUUSD symbol, `point = 0.01` USD, so 1 point = 0.01 USD and 1.00 USD = 100 points.
- **Volume**: lots. `Lots` type: float, `> 0`, no inf/NaN. Constrained by `volume_min`, `volume_max`, `volume_step` from the broker.
- **Contract size**: taken from the broker `SymbolSpec` (typically 100 oz per lot). Never hard-coded.
- **Money** (P&L, commission, equity, balance): account currency, read from the broker (the demo account is USD; `broker.account_currency: USD` refuses any other). `Money` type: float, no inf/NaN, may be negative.
- **Models**: Pydantic v2, frozen (immutable), extra fields forbidden.
- **Buys are priced at ask, sells at bid.** Closing a BUY uses bid, closing a SELL uses ask.
- **Symbol names**: string, length 1 to 32.
- **Idempotency key**: string matching `^[A-Za-z0-9_-]{8,29}$` (the MetaTrader5 Python API refuses comments over 29 characters, ADR-0005).

## Enums

All are `StrEnum`; the value equals the name.

| Enum | Values | Meaning |
|---|---|---|
| `Mode` | `BACKTEST`, `SHADOW`, `DEMO`, `LIVE` | Run mode. Only `DEMO` and `LIVE` may ever send orders (`aurum.core.safety`). |
| `Side` | `BUY`, `SELL` | Trade direction. `sign` is +1 for BUY, -1 for SELL; `opposite` flips it. |
| `OrderType` | `MARKET`, `LIMIT`, `STOP` | Order entry type. LIMIT/STOP require a price. |
| `AccountTradeMode` | `DEMO`, `CONTEST`, `REAL` | Mirror of MT5 `ACCOUNT_TRADE_MODE_*`. Anything not `DEMO` is treated as real money. |
| `SessionPhase` | `CLOSED`, `PRE_SESSION`, `OPEN`, `NO_NEW_ENTRIES`, `EXIT_ONLY`, `FORCE_FLAT` | Phase of the trading day: CLOSED -> PRE_SESSION -> OPEN <-> NO_NEW_ENTRIES -> EXIT_ONLY -> FORCE_FLAT -> CLOSED. Exact boundaries in `aurum.sessions.clock`. |
| `ExecutionStatus` | `FILLED`, `PLACED`, `MODIFIED`, `CLOSED`, `CANCELLED`, `REJECTED` | Outcome of one broker action. |
| `DealEntry` | `IN`, `OUT` | Whether a deal opened (`IN`) or closed (`OUT`) exposure. |
| `Timeframe` | `M1`, `M5`, `M15`, `H1` | Bar timeframe; durations 1 min, 5 min, 15 min, 1 h. `parse()` is case-insensitive. |

`SessionPhase` properties:

| Phase | `entries_allowed` | `must_be_flat` |
|---|---|---|
| `CLOSED` | no | yes |
| `PRE_SESSION` | no | yes |
| `OPEN` | yes (risk checks still apply) | no |
| `NO_NEW_ENTRIES` | no | no |
| `EXIT_ONLY` | no | no |
| `FORCE_FLAT` | no | yes |

`entries_allowed` is true only in `OPEN`. `must_be_flat` means any exposure seen in the phase must be closed/cancelled immediately.

## Domain models

### Tick

One quote. Crossed/zero spreads are allowed on purpose (raw data must be representable; Phase 2 QA flags them and risk refuses to trade on them).

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `symbol` | str | | Symbol name. |
| `time` | UtcDatetime | UTC | Broker quote time. |
| `bid` | Price | price | `> 0`. |
| `ask` | Price | price | `> 0`. |
| `last` | float or None | price | Last trade price; default None. |
| `volume` | float | | `>= 0`; default 0.0. |

Derived: `spread = ask - bid` (price units), `mid = (ask + bid) / 2`, `price_for(side)` (ask for BUY, bid for SELL), `exit_price_for(side)` (bid for BUY, ask for SELL).

### Bar

OHLC bar keyed by `open_time`; prices are bid.

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `symbol` | str | | Symbol name. |
| `timeframe` | Timeframe | | `M1`, `M5`, `M15`, `H1`. |
| `open_time` | UtcDatetime | UTC | Must be aligned to the timeframe (multiple of its duration since the epoch), else rejected. |
| `open`, `high`, `low`, `close` | Price | price | `> 0`. OHLC consistency: `high >= max(open, close, low)` and `low <= min(open, close)`, else rejected. |
| `tick_volume` | int | ticks | `>= 0`; default 0. |
| `spread_points` | int or None | points | `>= 0`; default None. |
| `real_volume` | float | | `>= 0`; default 0.0. |

Derived: `close_time = open_time + timeframe.duration` (first instant the bar is complete and usable).

### MarketEvent

A tick or completed bar delivered to the core at `time` (when it became known).

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `time` | UtcDatetime | UTC | Delivery time. |
| `symbol` | str | | Must equal the payload's symbol. |
| `tick` | Tick or None | | Exactly one of `tick` / `bar` must be set. |
| `bar` | Bar or None | | See above. |

Causality checks: tick symbol must match and `tick.time <= time`; bar symbol must match and `bar.close_time <= time` (otherwise rejected as look-ahead).

### SessionState

Session phase at instant `time`.

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `time` | UtcDatetime | UTC | Instant evaluated. |
| `local_time` | AwareDatetime | session tz | Wall clock in the session timezone. |
| `phase` | SessionPhase | | See enums. |
| `session` | str or None | | Name of the active session window (only set while `OPEN`). |
| `trading_day` | date or None | local date | None on non-trading days. |
| `force_flat_at` | UtcDatetime or None | UTC | When positions must be flat. |

Derived: `entries_allowed`, `must_be_flat` (from `phase`).

### SymbolSpec

Contract specification snapshot from the broker (never hard-coded for real trading).

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `name` | str | | Symbol name. |
| `digits` | int | decimals | `0..8`. |
| `point` | Price | price | Smallest quote increment (0.01 for 2-digit XAUUSD). |
| `tick_size` | Price | price | Minimum price change. |
| `tick_value` | Price | account ccy | Value of a `tick_size` move for 1.0 lot. |
| `contract_size` | Price | units per lot | e.g. 100 oz. |
| `volume_min`, `volume_max`, `volume_step` | Lots | lots | `volume_min <= volume_max`, else rejected. |
| `stops_level_points` | int | points | `>= 0`; default 0. |
| `freeze_level_points` | int | points | `>= 0`; default 0. |
| `currency_profit` | str | | Default `"USD"`. |
| `currency_margin` | str | | Default `"USD"`. |
| `captured_at` | UtcDatetime or None | UTC | When the snapshot was taken. |

Helpers: `money_per_price_unit_per_lot = tick_value / tick_size`, `price_to_points`, `points_to_price`, `round_price` (to tick size, `digits` decimals), `is_valid_volume` (within min/max and an integer multiple of `volume_step`).

### AccountSnapshot

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `login` | int | | Broker account login. |
| `server` | str | | Broker server name. |
| `currency` | str | | Account currency. |
| `trade_mode` | AccountTradeMode | | `DEMO`, `CONTEST`, `REAL`. |
| `balance` | Money | account ccy | |
| `equity` | Money | account ccy | |
| `margin` | Money | account ccy | Default 0.0. |
| `margin_free` | Money | account ccy | |
| `leverage` | int | x | `>= 1`. |
| `time` | UtcDatetime | UTC | Snapshot time. |

Derived: `is_demo` (`trade_mode is DEMO`).

### PositionState

An open position.

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `position_id` | int | | Broker position id. |
| `symbol` | str | | |
| `side` | Side | | |
| `volume` | Lots | lots | `> 0`. |
| `open_price` | Price | price | |
| `open_time` | UtcDatetime | UTC | |
| `stop_loss` | Price or None | price | Required argument (no default). None means the broker shows no stop; reconciliation must treat that as an incident. |
| `take_profit` | Price or None | price | Default None. |
| `idempotency_key` | str or None | | Default None (no pattern check here). |
| `magic` | int | | Default 0. |
| `unrealized_pnl` | Money | account ccy | Default 0.0. |

### OrderState

A pending (not yet filled) order at the broker.

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `order_id` | int | | Broker order id. |
| `symbol` | str | | |
| `side` | Side | | |
| `order_type` | OrderType | | |
| `volume` | Lots | lots | `> 0`. |
| `price` | Price | price | Order price (required). |
| `stop_loss` | Price or None | price | Required argument (no default); None is allowed to represent a broker order without a stop. |
| `take_profit` | Price or None | price | Default None. |
| `created_at` | UtcDatetime | UTC | |
| `idempotency_key` | str or None | | Default None. |
| `magic` | int | | Default 0. |

No cross-field validators.

### DealRecord

A fill from the broker's deal history.

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `deal_id` | int | | |
| `order_id` | int or None | | Required argument; None allowed. |
| `position_id` | int | | |
| `symbol` | str | | |
| `side` | Side | | |
| `entry` | DealEntry | | `IN` opens, `OUT` closes exposure. |
| `volume` | Lots | lots | `> 0`. |
| `price` | Price | price | |
| `time` | UtcDatetime | UTC | |
| `commission` | Money | account ccy | Signed as the broker books it: costs are negative. Default 0.0. |
| `profit` | Money | account ccy | Default 0.0. |
| `idempotency_key` | str or None | | Default None. |

There is no `swap` or `ticket` field.

### Signal

A strategy's proposal. Strategies return Signals only; they cannot create orders. There is no sizing.

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `strategy_id` | str | | Pattern `^[a-z0-9_.:-]{1,64}$`. |
| `symbol` | str | | |
| `side` | Side | | |
| `time` | UtcDatetime | UTC | Decision time. |
| `data_as_of` | UtcDatetime | UTC | Latest close/tick time of any data used; must be `<= time`. |
| `entry_type` | OrderType | | Default `MARKET`. |
| `entry_price` | Price or None | price | Required for LIMIT/STOP; for MARKET an optional reference price. |
| `stop_loss` | Price | price | Mandatory. |
| `take_profit` | Price or None | price | Default None. |
| `max_hold` | timedelta or None | time | Must be `> 0` if set. |
| `reasons` | tuple[str, ...] | | At least one entry; objective (numbers and comparisons). |
| `features` | dict[str, float, int, bool or str] | | Default empty. |

Validators: `data_as_of <= time`; non-MARKET `entry_type` requires `entry_price`; if `entry_price` is set, SL/TP must be on the correct side of it (BUY: `stop_loss < entry_price < take_profit`; SELL: `take_profit < entry_price < stop_loss`; strict inequalities). With no `entry_price` (MARKET) the side check is skipped here.

`Signal.key` (derived): first 20 hex chars of `sha256("{strategy_id}|{symbol}|{side}|{time.isoformat()}")`. Deterministic, so a restart re-deciding the same signal yields the same key and never a second order.

### RiskDecision

The risk engine's verdict on one Signal. Only `approved=True` decisions become orders.

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `decision_id` | str | | Non-empty. |
| `signal_key` | str | | Non-empty; the `Signal.key` it judges. |
| `approved` | bool | | |
| `volume` | float | lots | `>= 0`, no inf/NaN. Approved size; 0 when rejected. |
| `stop_distance_points` | float or None | points | `> 0` if set. |
| `risk_amount` | Money or None | account ccy | Loss if the stop is hit at the approved volume, including costs. |
| `reasons` | tuple[str, ...] | | Default empty. |
| `decided_at` | UtcDatetime | UTC | |

Validators: approved requires `volume > 0`; rejected requires `volume == 0` and at least one reason.

### OrderIntent

An order ready for a broker; construction enforces the safety invariants.

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `idempotency_key` | str | | Pattern `^[A-Za-z0-9_-]{8,29}$`. Sent as the MT5 order comment. |
| `symbol` | str | | |
| `side` | Side | | |
| `order_type` | OrderType | | |
| `volume` | Lots | lots | `> 0`; must be `<= risk_decision.volume` (1e-12 tolerance). |
| `price` | Price or None | price | Required for LIMIT/STOP; for MARKET an optional reference price for SL/TP checks. |
| `stop_loss` | Price | price | Mandatory, broker-side. |
| `take_profit` | Price or None | price | Default None. |
| `created_at` | UtcDatetime | UTC | |
| `risk_decision` | RiskDecision | | Must have `approved=True`. |
| `magic` | int | | `>= 0`; default 0. |

Validators: approved `RiskDecision`; volume not above approved volume; non-MARKET order needs `price`; if `price` is set, SL/TP must be on the correct side (same rule as Signal).

`OrderIntent.from_signal(signal, decision, created_at=..., magic=0)`: raises if `decision.signal_key != signal.key`; sets `idempotency_key = "AU" + signal.key` (22 chars, matches the pattern), `order_type = signal.entry_type`, `volume = decision.volume`, `price = signal.entry_price`, and copies symbol, side, SL, TP.

### OrderCheckResult

Pre-trade check outcome (mirrors MT5 `order_check`).

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `ok` | bool | | |
| `reasons` | tuple[str, ...] | | Default empty. |
| `margin_required` | Money or None | account ccy | |

### ExecutionReport

Outcome of one broker action.

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `status` | ExecutionStatus | | |
| `time` | UtcDatetime | UTC | |
| `idempotency_key` | str or None | | |
| `order_id` | int or None | | |
| `deal_id` | int or None | | |
| `position_id` | int or None | | |
| `requested_price` | float or None | price | |
| `fill_price` | float or None | price | |
| `volume` | float or None | lots | |
| `retcode` | int or None | | Broker return code. |
| `message` | str | | Default `""`. |
| `commission` | Money | account ccy | Default 0.0. Same sign convention as `DealRecord` (costs negative). |
| `duplicate` | bool | | True when the idempotency key was already known and nothing new was sent. Default False. |

### TrialRecord

One immutable row of the trial ledger (every backtest/parameter trial, good or bad).

| Field | Type | Unit | Meaning / constraints |
|---|---|---|---|
| `trial_id` | str | | |
| `created_at` | UtcDatetime | UTC | |
| `strategy` | str | | |
| `variant` | str | | |
| `params` | dict[str, Any] | | Trial parameters. |
| `data_start` | UtcDatetime | UTC | |
| `data_end` | UtcDatetime | UTC | |
| `metrics` | dict[str, float or None] | | |
| `notes` | str | | Default `""`. |
| `code_version` | str or None | | |
| `config_hash` | str or None | | |
| `sealed_test_opened` | bool | | Default False. |
| `record_hash` | str | | Required; integrity hash of the record. Computed by the trial ledger, not by the model (no validator in `models.py`). |

## Stored datasets (Phase 2)

Authoritative schemas: `src/aurum/data/schemas.py`. Paths: `src/aurum/adapters/storage/layout.py`.
Storage rules: ADR-0006. Bar source and tick fidelity: ADR-0007.

### Common rules

- Everything lives under `AURUM_DATA_DIR` (Windows default `C:\aurum-data`, macOS `./data`).
- **Partitioning:** one Parquet file per symbol and **UTC** date (`YYYY-MM-DD.parquet`). The
  symbol is the broker's name, discovered at runtime (`XAUUSD` on Fusion).
- **Time columns:** `timestamp[ms, UTC]`, true UTC. MT5 server time (New York + 7h) is converted
  in the MT5 adapter before anything is stored (ADR-0004).
- **Prices:** USD per troy ounce, `float64`. **Spreads:** integer points (`round((ask − bid) /
  point)`, point = 0.01 USD). **Bars:** bid OHLC, keyed by open time, covering
  `[open_time, close_time)`, usable only from `close_time`.
- **Immutable** (never rewritten): `raw_ticks/`, `raw_broker_bars/`, `specs/`, `quality_reports/`,
  `collector/<symbol>/events.jsonl`. **Rebuilt** deterministically by `aurum data resample`:
  `normalized_ticks/`, `canonical_bars/`.
- **Parquet writer:** pyarrow, zstd level 9, dictionary on, statistics on, format 2.6. Identical
  rows give identical bytes (`aurum data hashes`).

| Path | Written by | Contents | Mutability |
|---|---|---|---|
| `raw_ticks/mt5_history/<symbol>/<date>.parquet` | `aurum data backfill` | Broker tick history for one complete UTC day | immutable (empty → data allowed with `--refetch-empty`) |
| `raw_ticks/mt5_live/<symbol>/<date>/<first_ms>_<last_ms>_<rows>.parquet` | `aurum data collect` | Live-collected ticks, one chunk per flush and UTC date | immutable, append-only |
| `raw_broker_bars/mt5/<symbol>/<tf>/<date>.parquet` | `aurum data backfill` | Broker bars (M1/M5/M15/H1), completed bars of one UTC day | immutable |
| `normalized_ticks/<symbol>/<date>.parquet` | `aurum data resample` | Cleaned ticks with spread and flags | rebuilt |
| `canonical_bars/<tf>/<symbol>/<date>.parquet` | `aurum data resample` | Canonical M1/M5/M15/H1 bars | rebuilt |
| `canonical_bars/days/<symbol>.parquet` | `aurum data resample` | One row per UTC day: bar source, tick fidelity | rebuilt (merged) |
| `features/` | Phase 5 | reserved | – |
| `quality_reports/<name>/<UTC stamp>.json` | backfill, resample, qa, spreads, hashes | One JSON report per run | immutable |
| `collector/<symbol>/events.jsonl` | collector | Journal: started, connected, gap, outage, pause, disconnected, stopped … | append-only |
| `collector/<symbol>/heartbeat.json` | collector | Latest heartbeat (status, cursor, last tick age) | replaced each minute |
| `collector/STOP` | the owner | Create it to stop a running collector gracefully | deleted on stop |
| `specs/<symbol>/<UTC stamp>.json` | collector, `aurum mt5 symbols` | `symbol_info` snapshot: on every connect if the contract changed, and once per UTC day | immutable |
| `logs/*.jsonl` | `--log-file` | structlog JSON lines | append-only |

### `raw_ticks` (RAW_TICKS)

Rows exactly as `copy_ticks_range(COPY_TICKS_ALL)` returned them, in received order. Nothing is
dropped or reordered.

| Column | Type | Unit | Meaning |
|---|---|---|---|
| `time` | timestamp[ms, UTC] | UTC | Quote time: MT5 `time_msc` converted from server clock |
| `bid` | float64 | price | Bid |
| `ask` | float64 | price | Ask |
| `last` | float64 | price | Last trade price; always 0 for this CFD |
| `volume` | uint64 | | Always 0 for this CFD |
| `flags` | uint32 | bit set | MT5 `TICK_FLAG_*`: 2 = bid changed, 4 = ask changed. History ticks also carry 128 (undocumented) on bid changes, so history uses 4/130/134 and live uses 2/4/6 |
| `volume_real` | float64 | | Always 0 for this CFD |

### `raw_broker_bars` (RAW_BARS)

| Column | Type | Unit | Meaning |
|---|---|---|---|
| `open_time` | timestamp[ms, UTC] | UTC | Bar open (converted from server clock) |
| `open`, `high`, `low`, `close` | float64 | price | Bid OHLC |
| `tick_volume` | uint64 | ticks | Bid-changing ticks in the bar (measured: equals ticks with flag bit 2) |
| `spread` | int32 | points | **Minimum** spread in the bar (measured, ADR-0007), not the average |
| `real_volume` | uint64 | | Always 0 for this CFD |

### `normalized_ticks` (NORMALIZED_TICKS)

Built from `raw_ticks/mt5_history` if the day exists there, else from the live chunks.

| Column | Type | Unit | Meaning |
|---|---|---|---|
| `time` | timestamp[ms, UTC] | UTC | Sorted ascending; same-millisecond rows keep their received order |
| `bid`, `ask` | float64 | price | Both > 0 and finite (invalid rows dropped) |
| `spread_points` | int32 | points | `round((ask − bid) / point)` |
| `flags` | uint32 | bit set | As in raw |
| `crossed` | bool | | `ask < bid` (kept, flagged) |
| `spread_outlier` | bool | | Spread > `qa.spread_outlier_factor` (5) × the UTC day's median spread |
| `source` | string | | `mt5_history` or `mt5_live` |

Normalisation rules, in order: drop rows without a positive finite bid and ask; count
out-of-order rows and sort stably by time; drop **consecutive** exact duplicates (all raw fields
equal to the previous row); compute spread; flag crossed and outlier spreads. Measured on 39.9M
Fusion ticks: 0 invalid, 0 out of order, 0 consecutive duplicates, 0 crossed, 133,638 outliers.
Non-adjacent identical rows in the same millisecond (A, B, A) are real quote flicker and kept.

### `canonical_bars` (CANONICAL_BARS)

| Column | Type | Unit | Meaning |
|---|---|---|---|
| `open_time` | timestamp[ms, UTC] | UTC | Bar open; aligned to the timeframe |
| `close_time` | timestamp[ms, UTC] | UTC | `open_time + duration`: the first instant the bar may be used. Join higher timeframes as-of on this column |
| `open`, `high`, `low`, `close` | float64 | price | Bid OHLC |
| `tick_count` | int64 | ticks | Bid-changing ticks (MT5 `tick_volume` convention) |
| `quote_count` | int64, nullable | ticks | All ticks (bid or ask changed); weight of `spread_mean`. Null when built from broker M1 |
| `spread_open`, `spread_close` | int32, nullable | points | Spread of the first / last tick. Null from broker M1 |
| `spread_min` | int32 | points | Minimum spread (from broker M1: the broker's `spread`) |
| `spread_max` | int32, nullable | points | Maximum spread. Null from broker M1 |
| `spread_mean` | float64, nullable | points | Quote-weighted mean spread (exact integer sum / count). Null from broker M1 |
| `source` | string | | `mt5_history` / `mt5_live` (built from ticks) or `mt5_bars_m1` (from the broker's M1) |

Construction: M1 from ticks when the day's tick-built M1 equals the broker's M1 exactly, else
from the broker's M1 (ADR-0007). M5/M15/H1 always from canonical M1: first open, max high,
min low, last close, summed counts, min/max of spread extremes, quote-weighted mean. Bars exist
only for minutes with ticks (like MT5). Every canonical bar equals the broker's bar at the same
timeframe (verified over 2,157 days by `aurum data resample`).

### `canonical_bars/days` (DAY_SOURCES)

| Column | Type | Meaning |
|---|---|---|
| `day` | date32 | UTC date |
| `m1_source` | string, nullable | Source of that day's canonical bars; null = no data (holiday, weekend) |
| `tick_source` | string, nullable | `mt5_history` / `mt5_live` if the day has ticks |
| `tick_rows` | int64, nullable | Normalized ticks kept |
| `tick_fidelity` | string, nullable | `exact` (tick M1 = broker M1), `incomplete` (it is not: thinned history), `unverified` (no broker M1) |
| `tick_m1_mismatch` | int64, nullable | Minutes whose OHLC from ticks differs from the broker's |
| `broker_only_minutes` | int64, nullable | Broker minutes with no ticks |
| `tick_only_minutes` | int64, nullable | Tick minutes the broker has no bar for |

### Market hours and data QA

`configs/data.yaml` `market` (validated in `settings.py`, used by `aurum.data.market_hours`):
trading date D runs 18:00 New York on D−1 to 16:59 New York on D (measured: last tick 16:58:5x,
first tick 18:00:00–18:01:00), Monday–Friday. `closed_dates` and `early_closes` (each with a
reason) were measured from the broker's M1 history and matched to US exchange holidays. QA
(`aurum data qa`) expects an M1 bar for every minute of every session, after a 2-minute opening
grace. It reports gaps inside the trading sessions (London/NY windows from sessions.yaml) and
missing trading dates as unexplained unless covered by `qa.known_gaps` (each with a reason). It
also reports gaps elsewhere in market hours, any bar outside market hours (daily break
16:59–18:00 New York = server 23:59–01:00, weekends, closed dates), stale quotes (no tick for
more than `risk.stale_quote_seconds` inside a session) and live-vs-history differences.

### Spread statistics

`aurum data spreads` (DuckDB over `normalized_ticks`) reports quote-weighted spread
distributions per session, all market hours and London hour; the spread in force at every M5
close inside a session ("decision-time"); and news-like spikes (minutes whose mean spread
exceeds `qa.spread_spike_factor` × the session median). By default it uses tick days of `exact`
fidelity only.
