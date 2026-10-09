# Integration tests (MT5)

Real-terminal checks: data retrieval in UTC, the server-clock rule, order placement,
modification, close and reconciliation. DEMO accounts only; the live-trading guard stays on.

| Marker | What | Runs when |
|---|---|---|
| `mt5` | Read-only: ping, symbols, ticks/bars in UTC, daily-break check | Windows + MetaTrader5 + running terminal |
| `mt5_orders` | Sends minimum-volume demo orders and closes them | All of the above **and** `AURUM_MT5_ORDER_TESTS=1` |

Live-market checks skip (not fail) when the latest quote is stale (weekend, daily break).

```powershell
uv run pytest -m mt5                                              # read-only
$env:AURUM_MT5_ORDER_TESTS = "1"; uv run pytest -m mt5_orders     # demo orders, deliberately
```
