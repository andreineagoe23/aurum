"""Risk engine: the only path from a Signal to an approved order.

TODO(Phase 4): position sizing (equity x risk % / (stop distance x tick value), round down).
TODO(Phase 4): limits (risk/trade, one position, daily/weekly loss, max trades/day).
TODO(Phase 4): lockouts (consecutive losses) and rejections (stale data, spread, margin, cutoff).
TODO(Phase 4): kill switch and watchdog.
"""
