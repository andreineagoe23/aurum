# Phase 1 report: MT5 connectivity spike

- **Date:** 2026-10-09
- **Platform:** Windows 11 ARM64 VM (VMware Fusion), x64 Python 3.12.15 (emulated, `win-amd64`),
  uv 0.12.24, MetaTrader5 package 5.0.6231, MT5 terminal build 6249, `FusionMarkets-Demo`. Work
  done over Cursor Remote-SSH.
- **Exit gate:** **PASSED** (see below). Phase 2 has not started; it waits for owner sign-off.

## Key findings

| Question | Answer (measured) |
|---|---|
| Does `mt5.initialize()` work from an SSH session? | **Yes.** It attaches to the desktop terminal from Remote-SSH with no IPC or session-isolation errors. No desktop PowerShell is needed. |
| MT5 server time vs UTC | **New York + 7h**: UTC+3 now (US summer time), UTC+2 in US winter time. Live: tick `time_msc` − NTP-synced UTC = +10799.1…+10799.9 s. History: the gold daily break is server 00:00 on every day checked from 2020 to 2026, **including the weeks where US and EU DST disagree**, which rules out EET. Details in ADR-0004. |
| Does Fusion keep the order comment (idempotency key)? | **Yes**, on the position (also after an SL/TP modify), the history order and both deals. Magic is kept too. **No journal fallback needed** (ADR-0005). |
| Comment length limit | **29 chars** in the Python API (30+ → `None`, `-2 Invalid "comment" argument`). Our keys are 22. `IdempotencyKey` is now capped at 29 (it allowed 31). |
| Account | Demo, **GBP**, leverage 1:500, hedging margin mode (`margin_mode=2`). |
| XAUUSD spec | 2 digits, point 0.01, contract 100 oz, volume 0.01–100 step 0.01, stops level 0, freeze level 0, filling IOC+FOK, market execution. `tick_value` is in GBP (≈0.756 per 0.01 per lot). Snapshot: `C:\aurum-data\specs\XAUUSD\20261009T003542Z.json`. |
| Bars | Bid-based (the ticks inside a completed M1 bar reproduce its open, high and low exactly). |

## What was built

| Area | Contents |
|---|---|
| `adapters/mt5/servertime.py` | `ServerTimeZone` (server ↔ UTC, both directions, DST-correct) and `check_offset` (VERIFIED / STALE_QUOTE / MISMATCH against the newest quote). Pure Python. |
| `adapters/mt5/broker.py` | `MT5Broker` now implements the full `BrokerPort`. **Connection:** connect with retry + exponential backoff, `reconnect()`, `is_connected()` via `terminal_info`, and on connect an account-currency check and a server-clock check (MISMATCH refuses to connect). **Data:** symbol discovery (`find_symbols`, `resolve_symbol` from config candidates), `symbol_info` → `SymbolSpec`, raw `symbol_snapshot`, UTC `latest_tick` / `bars` (completed only) / `ticks` / `positions` / `orders` / `history_deals` / `position_deals`. **Writes:** `check_order`, `send_order`, `modify_position`, `close_position` and `cancel_order`, each behind the live-trading guard with a fresh `account_info` read. **Idempotency:** `find_by_key` before every send and after a lost reply. **Stops:** if a fill shows no broker-side stop, the position is closed at once. Every action is logged with structlog. |
| `adapters/mt5/snapshot.py` | Immutable spec snapshots in `<data_dir>/specs/<symbol>/<UTC stamp>.json` (refuses to overwrite). |
| Config | `broker.yaml`: `account_currency: auto` (an explicit code becomes an expectation checked on connect), `server_time: {America/New_York, +7}`, `magic_number: 7020261`, `max_deviation_points: 50`. All validated. |
| CLI | `aurum mt5 ping` (account, DEMO flag, currency, last quote in UTC, server-clock check), `aurum mt5 symbols --find gold` (discovery + snapshot), `aurum mt5 bars --tf M5 --count 5` (UTC and London close times). JSON logs to stderr. |
| Logging fix | structlog now writes to the *current* `sys.stderr`. Cached loggers used to write to a closed stream after a redirection. |
| Docs | ADR-0004 (server time), ADR-0005 (idempotency via comment). ADR-0003's open issue resolved. ARCHITECTURE, VM_SETUP §10/§11, CLAUDE.md, `.cursor/rules`, integration README updated. |

## Tests

`ruff check`, `ruff format --check` and `mypy --strict` (whole `src/`) are clean.
`uv run pytest`: **304 passed, 5 skipped**. The skips are 3 macOS-only checks plus the 2
order-sending tests, which are gated. Line coverage is 94% overall; `MT5Broker` is at 90% and
`servertime` at 100%.

- **Unit (any OS)**, against a new stateful fake MetaTrader5 module (`tests/fake_mt5.py`, with
  constants copied from 5.0.6231):
  - retry/backoff, give-up, currency expectation, server-clock VERIFIED/STALE/MISMATCH;
  - every write blocked on REAL/CONTEST/unknown accounts and in BACKTEST/SHADOW, with a fresh
    account read on every write;
  - UTC conversion of ticks, bars (including winter +2h), positions and deals; query bounds sent
    in server time; forming bars excluded;
  - buys fill at ask and sells at bid; duplicate, duplicate-after-close and lost-reply
    reconciliation; a closing order doesn't block a new key; rejection and invalid volume; a fill
    without a stop is force-closed; pending place → dedupe → cancel → still a duplicate;
  - spec snapshot written once.
- **Server time:** known anchors around both 2026 US switches, the weeks between US and EU
  switches, and the Phase 1 live sample. Hypothesis properties over 2015–2035: round trip, offset ∈
  {2h, 3h}, server midnight = 17:00 New York, monotonic.
- **CLI:** `mt5 ping` / `symbols` against the fake. Unit tests never touch the real terminal. The
  old placeholder test did, and it was fixed.
- **Integration, read-only (`-m mt5`), 9 passed on the real demo:** DEMO account; server clock
  VERIFIED; latest tick within 60 s of UTC now; the latest completed M5/M15/H1 bar is the one
  that just closed; M1 bar OHLC = bid ticks inside it; daily break at 21:00 UTC in both
  DST-mismatch weeks.
- **Integration, demo orders (`-m mt5_orders`, needs `AURUM_MT5_ORDER_TESTS=1`), 2 passed:**
  - `order_check` → open 0.01 → modify SL → close → reconcile. Deals are IN + OUT with the key,
    the balance delta equals profit + commission, and a resend is a duplicate.
  - **20 `shutdown`/`initialize` cycles**, each resending the same intent: 20 duplicates, exactly
    one position, one IN and one OUT deal.

## Exit gate

| Criterion | Result |
|---|---|
| 20 clean reconnect cycles | ✅ 20/20 reconnects and server-clock re-verifications; the resend was a duplicate every time |
| Demo open / modify / close | ✅ 0.01 lot XAUUSD, broker-side SL/TP from the first request |
| Full reconciliation | ✅ Positions, deals and balance agree. The key is found in positions, orders and history; no orphan positions left |
| Structured logs | ✅ Every connect, server-time check, send, fill, duplicate, modify, close and reject is a structlog event. The CLI emits JSON lines; under pytest structlog uses its console renderer |

**Demo footprint:** 3 round trips of 0.01 lot (1 manual comment probe, 2 tests), −£0.42 net
including £0.02 commission per side. Balance is £999.58. The account is flat, with no pending
orders.

## Known issues and risks

1. **Account currency is GBP, commission is configured in USD.** The broker books commission in
   GBP. At 0.01 lot it rounds to £0.02 per side, which is consistent with $2.25/lot but too
   coarse to confirm it. The Phase 3 cost model must convert USD → account currency (or use
   broker-booked commission). Risk sizing must use `tick_value`, which is already in GBP. This
   needs a decision before Phase 3: should backtests report P&L in GBP or USD?
2. **History depth is limited in the terminal right now.** H1 bars go back to about 2020 (before
   that only sparse bars). The M1 and tick probes over past dates returned almost nothing. MT5
   downloads history on demand and is bounded by "Max bars in chart". Phase 2 backfill must
   measure the real depth before committing to "from 2023-01-01".
3. **The reconnect test covers terminal IPC** (`shutdown`/`initialize`), not a terminal↔server
   network drop. Broker-side disconnects (terminal `connected=False`) raise
   `BrokerDisconnectedError` (unit-tested). Auto-recovery belongs to the Phase 4/7 loops via
   `reconnect()`.
4. **Idempotency lookback is 1 hour** after `intent.created_at`. The Phase 4 order manager must
   never send an older intent.
5. `max_deviation_points: 50` ($0.50) and `magic_number: 7020261` are my choices, not
   calibrated. Deviation should be revisited with real fills in Phase 8.
6. D1/W1 bars from this server open at 21:00/22:00 UTC. Aurum doesn't use them, but any future
   daily feature must be built from H1/M1 instead.
7. A 0.01-lot demo order can't measure slippage or the commission rate precisely. Those come in
   Phase 8.
8. `aurum doctor` still reports DEMO mode as not ready until `max_spread_points` is calibrated
   (Phase 2). That is unchanged from Phase 0.

## Next steps (Phase 2, after sign-off)

Measure the available history depth per timeframe and for ticks. Then: idempotent bounded-chunk
backfill to Parquet using the UTC adapter; a live tick collector with heartbeat and a server-clock
check on start; normalisation, resampling and broker-bar comparison (bid bars); gap checks,
including the daily break at server midnight; spread statistics to calibrate
`max_spread_points`; and `DATA_DICTIONARY.md`.
