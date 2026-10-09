"""Golden replay scenarios: hand-built tapes with exact expected trades and P&L.

Every scenario runs the full stack (runner -> engine -> risk stand-in -> SimBroker ->
accounting) with realistic costs:

* 0.20 lots (1.00 USD move = 20 USD), commission 2.25 USD/lot/side = 0.45 per fill,
* slippage 5 points (0.05) on market fills and stop exits, none on targets,
* latency 200 ms, bar-day spread = bar minimum (10 points) + 2 = 0.12.

Day 2026-09-15 is a Tuesday in BST/EDT unless stated: London sessions 06:00-10:00 and
12:00-15:30 UTC, force-flat 19:00 UTC, market close 20:59 UTC.

The arithmetic is spelled out per scenario. If a simulator change alters any number here, the
change must be intended and the fixture updated with an explanation (tests/replay/README.md).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from aurum.backtest.broker import RejectionHook
from aurum.backtest.market import HistoricalMarket
from aurum.core.enums import Side
from aurum.core.models import SymbolSpec
from tests.simdata import DAY, BarRow, Plan, TickRow, at, flat_m1, market, spec


@dataclass(frozen=True)
class Scenario:
    name: str
    build: Callable[[], HistoricalMarket]
    plans: dict[Any, Plan]
    expected: list[dict[str, Any]]
    """Expected trades (subset of BACKTEST_TRADES columns), in closing order."""
    day: date = DAY
    sessions: dict[str, Any] = field(default_factory=dict)
    """Overrides of configs/sessions.yaml."""
    symbol_spec: SymbolSpec = field(default_factory=spec)
    hooks: tuple[RejectionHook, ...] = ()
    journal: tuple[tuple[str, str], ...] = ()
    """(event, substring of detail) pairs that must appear in the journal."""


def ticks_only(tape: list[TickRow], day: date = DAY) -> Callable[[], HistoricalMarket]:
    return lambda: market(ticks={day: tape}, day=day)


def bars_only(rows: list[BarRow], day: date = DAY) -> Callable[[], HistoricalMarket]:
    return lambda: market(m1=rows, extra_points=2, day=day)


BUY_08 = {at("08:00"): Plan(Side.BUY, 1995.00, take_profit=2010.00)}

# ---------------------------------------------------------------------------- gap through stop
GAP_TICKS = Scenario(
    name="gap_through_stop_ticks",
    build=ticks_only(
        [
            ("07:59:59.000", 1999.95, 2000.05),
            ("08:00:00.100", 2000.00, 2000.10),  # before arrival (08:00:00.200): not usable
            ("08:00:00.250", 2000.02, 2000.12),  # fill: ask 2000.12 + 0.05 = 2000.17
            ("08:00:30.000", 1998.00, 1998.10),
            ("08:01:00.000", 1996.00, 1996.10),  # stop 1995.00 not reached
            ("08:01:10.000", 1993.50, 1993.60),  # gap: bid jumps through 1995.00
            ("08:02:00.000", 1994.00, 1994.10),
        ]
    ),
    plans=BUY_08,
    expected=[
        {
            "side": "BUY",
            "entry_time": at("08:00:00.250"),
            "entry_price": 2000.17,
            "exit_time": at("08:01:10"),
            "exit_price": 1993.45,  # gapped bid 1993.50 - 0.05, not the stop price
            "exit_reason": "STOP",
            "net_pnl": -135.30,  # (1993.45 - 2000.17) x 20 = -134.40, - 0.90 commission
            "spread_cost": 2.00,  # (0.10/2 + 0.10/2) x 20
            "slippage_cost": 2.00,  # (0.05 + 0.05) x 20
            "commission": 0.90,
            "gross_pnl": -130.40,  # mid 2000.07 -> 1993.55 = -6.52 x 20
            "mae_points": 667.0,  # 2000.17 - 1993.50
            "entry_mode": "ticks",
            "intrabar_rule": "none",
        }
    ],
)

GAP_BARS = Scenario(
    name="gap_through_stop_bars",
    build=bars_only(
        [
            ("07:59", 2000.00, 2000.10, 1999.90, 2000.00, 10),  # quote seen by the 08:00 risk check
            ("08:00", 2000.00, 2000.40, 1999.70, 2000.20, 10),  # fill at open: ask 2000.12 + 0.05
            ("08:01", 2000.20, 2000.30, 1996.00, 1996.50, 10),  # low 1996.00 > stop 1995.00
            ("08:02", 1993.80, 1994.50, 1993.00, 1994.20, 10),  # opens below the stop
        ]
    ),
    plans=BUY_08,
    expected=[
        {
            "side": "BUY",
            "entry_time": at("08:00:00.200"),  # arrival; price = the 08:00 bar open
            "entry_price": 2000.17,
            "exit_time": at("08:02"),
            "exit_price": 1993.75,  # open 1993.80 - 0.05
            "exit_reason": "STOP",
            "net_pnl": -129.30,  # (1993.75 - 2000.17) x 20 = -128.40, - 0.90
            "spread_cost": 2.40,  # (0.12/2 + 0.12/2) x 20
            "slippage_cost": 2.00,
            "commission": 0.90,
            "gross_pnl": -124.00,  # mid 2000.06 -> 1993.86 = -6.20 x 20
            "entry_mode": "bars",
            "intrabar_rule": "none",  # a gap at the open is certain, no assumption
        }
    ],
)

# ---------------------------------------------------------------------- stop and target, one bar
SAME_BAR_PLAN = {at("08:00"): Plan(Side.BUY, 1997.00, take_profit=2003.00)}

SAME_BAR_TICKS = Scenario(
    name="sl_tp_same_bar_with_ticks",
    build=ticks_only(
        [
            ("07:59:59.000", 1999.95, 2000.05),
            ("08:00:00.250", 2000.00, 2000.10),  # fill 2000.15
            ("08:00:20.000", 2003.00, 2003.10),  # target first (bid >= 2003.00)
            ("08:00:40.000", 1996.00, 1996.10),  # the stop would come later in the same minute
            ("08:00:59.000", 2000.00, 2000.10),
        ]
    ),
    plans=SAME_BAR_PLAN,
    expected=[
        {
            "entry_price": 2000.15,
            "exit_time": at("08:00:20"),
            "exit_price": 2003.00,  # limit price, no slippage
            "exit_reason": "TARGET",
            "net_pnl": 56.10,  # (2003.00 - 2000.15) x 20 = 57.00, - 0.90
            "spread_cost": 2.00,
            "slippage_cost": 1.00,  # entry only
            "gross_pnl": 60.00,  # mid 2000.05 -> 2003.05
            "entry_mode": "ticks",
            "intrabar_rule": "none",
            "worst_case": False,
        }
    ],
)

SAME_BAR_NO_TICKS = Scenario(
    name="sl_tp_same_bar_without_ticks",
    build=bars_only(
        [
            ("07:59", 2000.00, 2000.10, 1999.90, 2000.00, 10),
            ("08:00", 2000.00, 2000.50, 1999.80, 2000.20, 10),
            ("08:01", 2000.20, 2003.50, 1996.50, 2000.00, 10),  # reaches 2003.00 and 1997.00
        ]
    ),
    plans=SAME_BAR_PLAN,
    expected=[
        {
            "entry_price": 2000.17,
            "exit_time": at("08:01:59.999"),
            "exit_price": 1996.95,  # stop assumed first (worst case): 1997.00 - 0.05
            "exit_reason": "STOP",
            "net_pnl": -65.30,  # (1996.95 - 2000.17) x 20 = -64.40, - 0.90
            "spread_cost": 2.40,
            "slippage_cost": 2.00,
            "gross_pnl": -60.00,  # mid 2000.06 -> 1997.06
            "entry_mode": "bars",
            "intrabar_rule": "sl_tp_same_bar",
            "worst_case": True,
        }
    ],
)

# ------------------------------------------------------------------------- spread spike at entry
SPIKE_TAPE: list[TickRow] = [
    ("07:59:59.000", 2000.00, 2000.10),
    ("08:00:00.250", 1999.50, 2002.50),  # 300-point spread at the fill quote
    ("08:00:01.000", 2000.00, 2000.10),
    ("08:10:00.300", 2001.00, 2001.10),  # first quote after the timeout decision + latency
]
SPIKE_PLAN = {
    at("08:00"): Plan(Side.BUY, 1990.00, take_profit=2015.00, max_hold=timedelta(minutes=10))
}

SPREAD_SPIKE = Scenario(
    name="spread_spike_at_entry",
    build=ticks_only(SPIKE_TAPE),
    plans=SPIKE_PLAN,
    expected=[
        {
            "entry_price": 2002.55,  # the spiked ask 2002.50 + 0.05
            "entry_bid": 1999.50,
            "entry_ask": 2002.50,
            "exit_time": at("08:10:00.300"),
            "exit_price": 2000.95,
            "exit_reason": "TIMEOUT",
            "net_pnl": -32.90,  # (2000.95 - 2002.55) x 20 = -32.00, - 0.90
            "spread_cost": 31.00,  # (3.00/2 + 0.10/2) x 20
            "slippage_cost": 2.00,
            "gross_pnl": 1.00,  # mid 2001.00 -> 2001.05
        }
    ],
)


def _spread_cap(points: int) -> RejectionHook:
    return lambda intent, q: (
        f"spread {q.spread / 0.01:.0f} points > {points}" if q.spread > points * 0.01 else None
    )


SPREAD_SPIKE_REJECTED = Scenario(
    name="spread_spike_rejected_by_hook",
    build=ticks_only(SPIKE_TAPE),
    plans=SPIKE_PLAN,
    hooks=(_spread_cap(50),),
    expected=[],
    journal=(("order_rejected", "spread 300 points > 50"),),
)

# ------------------------------------------------------------------------ early-close force-flat
EARLY = date(2026, 7, 3)  # early close 13:01 New York = 17:01 UTC (data.yaml)
EARLY_BARS = flat_m1("12:00", "17:00", day=EARLY)
EARLY_PLAN = {at("13:00", EARLY): Plan(Side.BUY, 1990.00, take_profit=2010.00)}


def _early_close(buffer: int, exit_hm: str) -> Scenario:
    return Scenario(
        name=f"early_close_force_flat_buffer_{buffer}",
        build=bars_only(EARLY_BARS, EARLY),
        plans=EARLY_PLAN,
        day=EARLY,
        sessions={"market_close_buffer_minutes": buffer},
        expected=[
            {
                "entry_time": at("13:00:00.200", EARLY),
                "entry_price": 2000.17,
                "exit_time": at(f"{exit_hm}:00.200", EARLY),  # 17:01 - buffer, + latency
                "exit_price": 1999.95,  # bar open 2000.00 - 0.05
                "exit_reason": "FORCE_FLAT",
                "net_pnl": -5.30,  # (1999.95 - 2000.17) x 20 = -4.40, - 0.90
            }
        ],
    )


EARLY_CLOSE_15 = _early_close(15, "16:46")
EARLY_CLOSE_30 = _early_close(30, "16:31")

# ------------------------------------------------------------------------------ daily break
# force_flat_at 23:30 London would fall in the daily break (21:59-23:00 London). The calendar
# flattens at 16:59 New York - 15 min = 16:44 New York = 20:44 UTC instead.
DAILY_BREAK = Scenario(
    name="daily_break_caps_late_force_flat",
    build=bars_only(flat_m1("14:00", "20:58") + flat_m1("22:00", "22:30")),
    plans={at("15:00"): Plan(Side.BUY, 1990.00, take_profit=2010.00)},
    sessions={"no_new_entries_after": "23:00", "force_flat_at": "23:30"},
    expected=[
        {
            "entry_time": at("15:00:00.200"),
            "exit_time": at("20:44:00.200"),  # before the 21:00 UTC close, not at 22:30 UTC
            "exit_reason": "FORCE_FLAT",
            "net_pnl": -5.30,
        }
    ],
)


# ---------------------------------------------------------------------------- rejected order
def _reject_first(intent: Any, q: Any) -> str | None:
    """Simulated broker requote of the 08:00 order only (stateless, so reruns match)."""
    return "requote" if q.time < at("08:05") else None


REJECTED_ORDER = Scenario(
    name="rejected_order_then_next_signal_fills",
    build=bars_only(flat_m1("07:55", "08:30")),
    plans={
        at("08:00"): Plan(Side.BUY, 1990.00, take_profit=2010.00),
        at("08:05"): Plan(Side.BUY, 1990.00, take_profit=2010.00, max_hold=timedelta(minutes=10)),
    },
    hooks=(_reject_first,),
    expected=[
        {
            "entry_time": at("08:05:00.200"),
            "exit_time": at("08:15:00.200"),
            "exit_reason": "TIMEOUT",
            "net_pnl": -5.30,
        }
    ],
    journal=(("order_rejected", "requote"),),
)

# --------------------------------------------------------------------------- min stop distance
MIN_STOP = Scenario(
    name="min_stop_distance_rejected",
    build=bars_only(flat_m1("07:55", "08:30")),
    plans={
        # Fill quote bid 2000.00: 3.00 from the stop, inside the 500-point stops level.
        at("08:00"): Plan(Side.BUY, 1997.00, take_profit=2010.00),
        # 6.00 away: accepted.
        at("08:05"): Plan(Side.BUY, 1994.00, take_profit=2010.00, max_hold=timedelta(minutes=10)),
    },
    symbol_spec=spec(stops_level=500),
    expected=[{"entry_time": at("08:05:00.200"), "exit_reason": "TIMEOUT", "stop_loss": 1994.00}],
    journal=(("order_rejected", "invalid stops: stop_loss 3.00 from price, stops level 5.00"),),
)

# ------------------------------------------------------------- close and a level in one bar
# Regression (found 2026-10-09 in the full-history run, 2021-09-16): a timeout close arriving
# inside an M1 bar that also reaches a level. Before the fix the whole bar was processed first:
# a stop was taken unflagged, and a target was credited although the close at the open almost
# certainly came first (optimistic). Now: stop first and flagged; target not credited, flagged.
CLOSE_BAR_PLAN = {
    at("08:00"): Plan(Side.BUY, 1999.00, take_profit=2000.40, max_hold=timedelta(minutes=5))
}


def _close_bar(name: str, bar: BarRow, expected: dict[str, Any]) -> Scenario:
    return Scenario(
        name=name,
        build=bars_only([*flat_m1("07:55", "08:04"), bar, *flat_m1("08:06", "08:30")]),
        plans=CLOSE_BAR_PLAN,
        expected=[
            {
                "entry_time": at("08:00:00.200"),
                "entry_price": 2000.17,  # open 2000.00 + spread 0.12 + 0.05
                "entry_mode": "bars",
                "intrabar_rule": "close_bar",
                "worst_case": True,
                **expected,
            }
        ],
    )


CLOSE_BAR_STOP = _close_bar(
    "timeout_close_and_stop_in_one_bar",
    ("08:05", 2000.00, 2000.20, 1998.50, 1999.80, 10),  # reaches the 1999.00 stop
    {
        "exit_time": at("08:05:59.999"),
        "exit_price": 1998.95,  # stop assumed first: 1999.00 - 0.05
        "exit_reason": "STOP",
        "net_pnl": -25.30,  # (1998.95 - 2000.17) x 20 = -24.40, - 0.90
    },
)
CLOSE_BAR_TARGET = _close_bar(
    "timeout_close_and_target_in_one_bar",
    ("08:05", 2000.00, 2000.50, 1999.90, 2000.30, 10),  # reaches the 2000.40 target
    {
        "exit_time": at("08:05:00.200"),
        "exit_price": 1999.95,  # closed at the open 2000.00 - 0.05; target not credited
        "exit_reason": "TIMEOUT",
        "net_pnl": -5.30,  # (1999.95 - 2000.17) x 20 = -4.40, - 0.90
    },
)

SCENARIOS = [
    GAP_TICKS,
    GAP_BARS,
    SAME_BAR_TICKS,
    SAME_BAR_NO_TICKS,
    SPREAD_SPIKE,
    SPREAD_SPIKE_REJECTED,
    EARLY_CLOSE_15,
    EARLY_CLOSE_30,
    DAILY_BREAK,
    REJECTED_ORDER,
    MIN_STOP,
    CLOSE_BAR_STOP,
    CLOSE_BAR_TARGET,
]
