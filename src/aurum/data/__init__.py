"""Market-data foundation: ingestion, normalisation, resampling and quality checks.

TODO(Phase 2): idempotent bounded-chunk backfill of ticks and M1/M5/M15/H1 bars from MT5.
TODO(Phase 2): normalisation (dedupe, out-of-order, bid/ask/spread, extreme spreads, stale quotes).
TODO(Phase 2): deterministic resampling ticks -> M1 -> M5/M15/H1, compared against broker bars.
TODO(Phase 2): QA (gap and session-integrity checks, spread stats by session, quality reports).
"""
