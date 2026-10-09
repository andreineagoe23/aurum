# ADR-0003: Time conventions

- **Status:** Accepted
- **Date:** 2026-10-08

## Decision

1. Every datetime inside Aurum is timezone-aware and normalised to UTC. Naive datetimes are
   rejected (`NaiveDatetimeError` / pydantic `ValidationError`), never assumed to be UTC. Ruff's
   `DTZ` rules ban creating naive datetimes in code.
2. Bars are keyed by **open time** (as MT5 does). `close_time = open_time + duration`; a bar
   covers `[open_time, close_time)` and becomes visible to decisions only at `close_time`.
   `Timeframe.last_completed_bar(now)` returns the newest bar with `close_time <= now`.
3. Bars align to multiples of their duration since the Unix epoch. For M1–H1 this equals
   alignment on any whole-hour-offset broker server clock.
4. Higher-timeframe data joins lower-timeframe data **as-of on close time** (Phase 5).
5. A `MarketEvent` is stamped with the time it became known; validation rejects events
   delivered before their data exists.
6. Session times are London wall-clock and converted per date (ADR-0002).

## MT5 server time (resolved in Phase 1)

MASTER_PROMPT says MT5 returns UTC seconds. It does not: the MetaTrader5 Python API speaks the
trade server's wall clock. Fusion Markets runs **New York + 7h** (UTC+2 / UTC+3 following US
DST). The MT5 adapter converts every timestamp and query bound; see
[ADR-0004](ADR-0004-mt5-server-time.md).
