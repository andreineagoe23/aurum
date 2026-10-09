# ADR-0004: MT5 server time is New York + 7h; convert in the adapter only

- **Status:** Accepted
- **Date:** 2026-10-09
- **Resolves:** the open issue in ADR-0003

## Context

The MetaTrader5 Python API represents time as *server wall-clock time written as Unix epoch
seconds*. This affects both directions:

- every `time` / `time_msc` it returns (ticks, bars, positions, orders, deals);
- every `date_from` / `date_to` it accepts (`copy_rates_range`, `copy_ticks_range`,
  `history_deals_get`, `history_orders_get`).

If these are treated as UTC, every timestamp is 2–3 hours in the future. That is look-ahead
contamination of the worst kind.

## Measurements (Fusion Markets demo, `FusionMarkets-Demo`, terminal build 6249, 2026-10-09)

1. **Live offset.** Latest `symbol_info_tick` `time_msc` minus NTP-synced system UTC
   (`w32tm`: synced to time.windows.com): +10799.1 s, +10799.9 s, +10799.7 s. That is **UTC+3**
   (US summer time). Deal timestamps from a demo fill showed the same offset (+10798 s).
2. **Which DST rule.** Gold has a daily break of 17:00–18:00 New York. In H1 history the missing
   hour is **server 00:00** on every trading day, in:
   - Mar 2–Apr 2 2026, including **Mar 9–27**, when the US is already on summer time and the EU
     is not;
   - Oct 20–Nov 7 2025, including **Oct 27–31**, when the EU is back on winter time and the US is
     not;
   - mid-March, July and December of every year 2020–2025.

   An EET/EEST server (EU DST rules) would move the break to server 23:00 during those mismatch
   weeks. It never moves. So the server clock is **New York wall clock + 7h**: UTC+2 in US winter
   time and UTC+3 in US summer time, with server midnight at 17:00 New York.
3. **Bars and ticks share the clock.** The ticks inside the last completed M1 bar reproduce its
   open, high and low exactly (bid prices).

## Decision

1. `configs/broker.yaml` `server_time: {timezone: America/New_York, shift_hours: 7}`. The rule is
   configuration, not code, so another broker or a rule change does not need a code change.
2. `aurum.adapters.mt5.servertime.ServerTimeZone` is the only place where conversion happens:
   `to_utc(server_seconds)` for every value read and `to_server_seconds(utc)` for every query
   bound. Nothing outside `aurum.adapters.mt5` ever sees a server timestamp.
3. **Verification on every connect.** `check_offset` compares the newest quote's server time
   with the rule at the current UTC time:
   - VERIFIED: within 120 s.
   - STALE_QUOTE: the quote is older than that, for example over the weekend. The rule cannot be
     checked; this is logged and the connection proceeds.
   - MISMATCH: the quote is *ahead* of the rule. `connect()` raises, because no timestamp from
     the server can be trusted.
4. **DST edges.** The New York clock repeats 01:00–02:00 in November and skips 02:00–03:00 in
   March. On the server that is Sunday 08:00–10:00, when gold does not trade. A repeated server
   wall time resolves to its first occurrence (`fold=0`).
5. The integration test `test_daily_break_is_server_midnight_in_utc` re-checks the break hour in
   both mismatch weeks against the live server, so a broker-side change fails loudly.

## Consequences

- Bars, ticks and deals leave the adapter in true UTC and keep the ADR-0003 conventions.
- Bars stay aligned: the offset is a whole number of hours, so M1–H1 open times are aligned in
  UTC as well. D1/W1 bars are **not**: they open at server midnight, 21:00 or 22:00 UTC. Aurum
  does not use them.
- Raw `symbol_info` snapshots are stored verbatim, so their integer times are server clock. The
  snapshot file says so.
- Phase 2 storage and data QA can rely on UTC from the adapter. The daily-break check is a
  natural data-QA rule.
