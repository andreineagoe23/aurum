# Data Dictionary (Phase 0)

Authoritative definitions: `src/aurum/core/models.py`, `enums.py`, `timeframes.py`, `timeutil.py`. The tables below mirror the code field by field; if they disagree, the code wins and this document must be fixed. The column-level Parquet schemas are completed in Phase 2.

## Conventions

- **Timestamps**: always timezone-aware UTC. Naive datetimes are rejected (`aurum.core.timeutil`). Fields typed `UtcDatetime` accept any aware datetime and store it converted to UTC. `SessionState.local_time` is the one exception: any aware datetime (session-timezone wall clock).
- **Bar timestamps are OPEN time.** A bar covers `[open_time, close_time)` with `close_time = open_time + timeframe duration`. A bar becomes visible to any decision only once `now >= close_time`. Bars are aligned to multiples of their duration since the Unix epoch.
- **Prices**: quote currency per unit, USD per troy ounce for XAUUSD. `Price` type: float, `> 0`, no inf/NaN. Bar prices are bid.
- **Points**: multiples of the symbol's `point`. For a 2-digit XAUUSD symbol, `point = 0.01` USD, so 1 point = 0.01 USD and 1.00 USD = 100 points.
- **Volume**: lots. `Lots` type: float, `> 0`, no inf/NaN. Constrained by `volume_min`, `volume_max`, `volume_step` from the broker.
- **Contract size**: taken from the broker `SymbolSpec` (typically 100 oz per lot). Never hard-coded.
- **Money** (P&L, commission, equity, balance): account currency, USD. `Money` type: float, no inf/NaN, may be negative.
- **Models**: Pydantic v2, frozen (immutable), extra fields forbidden.
- **Buys are priced at ask, sells at bid.** Closing a BUY uses bid, closing a SELL uses ask.
- **Symbol names**: string, length 1 to 32.
- **Idempotency key**: string matching `^[A-Za-z0-9_-]{8,31}$` (fits the 31-char MT5 order comment).

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
| `idempotency_key` | str | | Pattern `^[A-Za-z0-9_-]{8,31}$`. Sent as the MT5 order comment. |
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

## Storage layout (Phase 2)

TODO(Phase 2): confirm schemas, partitioning and file naming. Planned folders under `AURUM_DATA_DIR`, partitioned by symbol/date:

- `raw_ticks/` (immutable)
- `raw_broker_bars/` (immutable)
- `normalized_ticks/`
- `canonical_bars/{M1,M5,M15,H1}/`
- `features/`
- `quality_reports/`

Also TODO(Phase 2): normalisation rules (dedupe, out-of-order, negative or extreme spreads, stale quotes), resampling rules ticks -> M1 -> M5/M15/H1, and spread statistics by session.
