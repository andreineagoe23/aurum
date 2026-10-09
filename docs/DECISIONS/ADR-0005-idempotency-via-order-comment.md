# ADR-0005: Idempotency through the MT5 order comment

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Every `OrderIntent` has a deterministic idempotency key, `"AU" + signal.key` (22 chars). A
restarted process that re-decides the same signal must never open a second position (MASTER_PROMPT
§4 rule 4). MT5 has no client-order-id. The candidates are the free-text order **comment** (which
some brokers rewrite or truncate) or the numeric **magic** number plus a local intent journal.

## Measurements (Fusion Markets demo, 2026-10-09)

- The MetaTrader5 Python API **refuses comments longer than 29 characters**:
  `order_check`/`order_send` return `None` with `(-2, 'Invalid "comment" argument')`. 29
  characters are accepted. (MQL5 documentation says 31; the Python binding is stricter.)
- A 0.01-lot demo BUY with comment `AU0123456789abcdef0123` (22 chars) and magic `20261009` kept
  the exact comment and magic on:
  - the open position, both before and after a `TRADE_ACTION_SLTP` modification;
  - the history order (`history_orders_get`);
  - the opening deal (`history_deals_get`).
- The closing order and deal kept the comment and magic sent with the close request.
- The position ticket equals the ticket of the order that opened it. The closing order has its
  own ticket.

## Decision

1. **Comment = idempotency key.** `IdempotencyKey` is capped at 29 characters (pattern
   `^[A-Za-z0-9_-]{8,29}$`). Every Aurum order also carries `broker.magic_number` (`7020261`).
2. **Before every send**, `MT5Broker.find_by_key` searches for the key in:
   - open positions;
   - pending orders;
   - order history from `intent.created_at − 1h` to now. A history order counts only if it
     **opened** something: `position_id == ticket`, or `0` for a pending order that was cancelled
     or expired. Closing orders reuse the position's comment, and this rule excludes them.

   A hit returns `ExecutionReport(duplicate=True)` and sends nothing.
3. **Lost reply.** If `order_send` returns `None`, the same search decides what happened: if the
   key is found the order went through, otherwise it is reported as rejected. A retry with the same
   intent is always safe.
4. **No local intent journal.** The fallback was conditional on Fusion rewriting comments, and it
   does not. If a future broker does, the journal is the documented fallback.
5. **Stops.** SL/TP go on the market request itself. After a fill the adapter re-reads the position
   and closes it at once if it shows no stop (`SafetyViolationError`, logged as critical).

## Verified in integration tests (demo, 0.01 lot)

- Open → modify → close → reconcile: deals are IN + OUT, both carry the key, balance change equals
  the sum of profit and commission, and resending the same intent afterwards is a duplicate.
- 20 full `shutdown`/`initialize` cycles, each followed by resending the same intent: 20
  duplicates, exactly one position, one IN deal and one OUT deal.

## Consequences

- Idempotency survives process restarts and terminal reconnects without local state.
- The 1-hour lookback assumes an intent is sent within an hour of its creation. Phase 4's order
  manager must not send stale intents. It shouldn't anyway: a stale signal is a new decision.
- Manual trades or other EAs are unaffected: comments that don't start with `AU` are never read
  as keys.
