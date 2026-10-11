"""vwap_reversion 0.1.0 draft revision 4: B1 feasibility counts (read-only, descriptive, no P&L).

Authorised by the owner at approval boundary B1 (2026-10-11), with rulings R1-R6 and the
numerical rulings recorded in spec section 18c. It applies the accepted rules (spec sections
3-9 and 12) to development-period canonical bid bars and counts what they would do at all 12
declared points (variants m15 and m5, `k_dev` {1.0, 1.5, 2.0}, `er_max` {0.30, 0.50}): setup
bars, the setup life cycle, the first-trigger spend, the stop and reward:risk checks, and the
session-date statuses. Its output is geometry-eligible signals, never trades. It is not the
strategy implementation and changes no rule.

Not done: no fill, exit, return, P&L or executable price (no tick and no ask is read); no trial,
attempt or ledger access; no symbol snapshot (`point` 0.01 and `trade_stops_level` 0 are
provisional inputs copied from the frozen opening_range_breakout spec, sections 8 and 8a; B2
verifies both); no partition dated after 2025-09-30 (files are selected by name before any is
opened); nothing is written except the two evidence files.

Before any market data is read, the script refuses to run unless the worktree is a clean commit
(recorded as the methodology commit) and the spec is revision 4, runs its synthetic tests
(`synthetic_tests`), and checks the access cut against the market calendar. Every bar read names
the instant it is made for and is refused if the bar closes after it. Each decision is also
recomputed on a store physically truncated at that decision, and the spend instant is
re-derived by an independent scan that never looks at the geometry.

Run from the repository root: ``uv run --no-sync python scripts/vwap_reversion_feasibility.py``
(``--synthetic-tests``: only the synthetic tests; no config, data or git access).
Writes docs/strategies/evidence/vwap_reversion_2026-10-11_b1_feasibility.{md,csv}. CSV columns:
variant, k_dev, er_max, scope, slice, metric, count (one row per count; spec section 18c).
"""

# ruff: noqa: E501, I001  (report prose; aurum is imported before polars, see below)
# The VM's polars needs aurum._platform to run first, so aurum is imported before it.
import aurum  # noqa: F401

import bisect
import csv
import hashlib
import math
import platform
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from aurum.adapters.storage.layout import DataLayout, days_in
from aurum.core.timeframes import Timeframe
from aurum.data.market_hours import MarketHours
from aurum.features.indicators import atr
from aurum.sessions.calendar import TradingCalendar
from aurum.sessions.news import NewsBlackout
from aurum.settings import load_settings

OUT = Path("docs/strategies/evidence")
STEM = "vwap_reversion_2026-10-11_b1_feasibility"
SPEC = Path("docs/strategies/vwap_reversion.md")
SPEC_BLOB = "22d1ffacaf618e39e687d77563b491a8d57809db"  # git blob of draft revision 4
SYMBOL = "XAUUSD"
FIRST, LAST = date(2020, 12, 15), date(2025, 9, 30)  # evaluated period (spec section 16)
# Ruling R6: rows with open_time >= CUT (trading date 2025-10-01, held in the file dated LAST)
# are excluded at load time; only their number per timeframe is reported.
CUT = datetime(2025, 9, 30, 22, 0, tzinfo=UTC)
FOLDS = (
    ("training", date(2020, 12, 15), date(2021, 9, 30)),
    ("fold 1", date(2021, 10, 1), date(2022, 9, 30)),
    ("fold 2", date(2022, 10, 1), date(2023, 9, 30)),
    ("fold 3", date(2023, 10, 1), date(2024, 9, 30)),
    ("fold 4", date(2024, 10, 1), date(2025, 9, 30)),
)
LONDON, NEW_YORK = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")
SESSIONS = ("london_morning", "ldn_ny_overlap")
VARIANTS = {"m15": 15, "m5": 5}  # decision timeframe in minutes (section 6)
K_DEV, ER_MAX = (1.0, 1.5, 2.0), (0.30, 0.50)  # declared grid (section 14)
POINTS = tuple((k, e) for k in K_DEV for e in ER_MAX)
DEFAULT = (1.5, 0.30)
# Fixed rule constants of draft 0.1.0 (section 14; unchanged in revision 4).
WARMUP = {Timeframe.H1: 13, Timeframe.M15: 100}  # section 3.3, by bar count
ER_BARS = 12  # section 4: 13 H1 closes
ATR_PERIOD, ATR_BARS = 14, 100  # section 3.2 (RiskConfig.atr_period, engine.ATR_BARS)
SETUP_FROM = timedelta(minutes=60)  # Ts >= A + 60 (rule 1)
SETUP_TO = timedelta(minutes=45)  # Ts <= E - 45 (rule 1)
EXPIRY = timedelta(minutes=30)  # section 6.2
LAST_DECISION = timedelta(minutes=15)  # T <= E - 15 (section 6.1)
MAX_HOLD = timedelta(minutes=90)  # section 10
HOLD_BOUND = {"london_morning": time(12, 15), "ldn_ny_overlap": time(17, 45)}  # section 10.1
BUFFER_POINTS, MIN_STOP_POINTS, CAP_POINTS = 25, 500, 2000  # sections 8, 8a
NECESSARY_POINTS = 975  # section 13.1: TWV(T) - X >= 9.75 USD is necessary for a long signal
# Provisional recorded B1 inputs (numerical ruling), copied from the frozen
# docs/strategies/opening_range_breakout.md: point 0.01 (section 8a) and trade_stops_level 0
# (section 8 feasibility note), both recorded there from the pinned snapshot
# 20261010T000046Z.json. Not a snapshot verification: B1 does not open the snapshot; B2 verifies
# both. The minimum is 500 for any stops level <= 490.
POINT, STOPS_LEVEL_POINTS = 0.01, 0
MIN_POINTS = max(MIN_STOP_POINTS, STOPS_LEVEL_POINTS + 10)
# Official classifications use exact float64 comparisons. A comparison this close to its
# threshold is only reported (near_boundary_*); the tolerance never changes a classification.
NEAR = 1e-9
M5, M15, H1 = timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1)
STATUSES = (
    "signal",
    "stop_below_minimum",
    "stop_above_cap",
    "reward_risk_below_min",
    "no_decision_time",
    "data_gap",
    "twv_undefined",
    "no_trigger",
)
SPENT = STATUSES[:4]
# Spec section 6.1, London times: decision-timeframe closes after A + 60 removed by a news window
# (every other session, week and variant: none) and setup closes excluded by rule 6.
EXPECTED_REMOVED = {
    ("ldn_ny_overlap", "normal", 5): ("14:50", "14:55", "15:00", "15:05", "15:10"),
    ("ldn_ny_overlap", "normal", 15): ("15:00",),
    ("ldn_ny_overlap", "mismatch", 5): ("14:05", "14:10"),
}
EXPECTED_EXCLUDED = {
    ("ldn_ny_overlap", "normal"): ("15:00", "15:15"),
    ("ldn_ny_overlap", "mismatch"): ("14:00", "14:15"),
}
EXPECTED_SPAN = {  # first and last decision instant before blackout removal, London
    ("london_morning", 15): ("08:15", "10:45"),
    ("london_morning", 5): ("08:05", "10:45"),
    ("ldn_ny_overlap", 15): ("14:15", "16:15"),
    ("ldn_ny_overlap", 5): ("14:05", "16:15"),
}
FATES = ("triggered", "broken", "replaced", "expired")  # CSV: setups_<fate>
COUNTERS = (  # section 6.5 counter name -> Outcome field (setups and fates are listed apart)
    ("ambiguous_setup", "ambiguous"),
    ("setup_regime_off", "regime_off"),
    ("data_gap", "data_gap"),
    ("twv_undefined", "twv_undefined"),
    ("zero_weight_bar", "zero_weight"),
    ("blackout_skipped", "blackout_skipped"),
)
NONNORMATIVE = (
    "nonnormative_new_setup_after_break",
    "nonnormative_new_setup_after_break_same_instant",
    "nonnormative_new_setup_at_expiry",
)
FORBIDDEN_MODULES = (
    "aurum.ledger",
    "aurum.backtest",
    "aurum.adapters.storage.specs",
    "aurum.adapters.mt5",
    "aurum.execution",
    "aurum.risk",
    "aurum.strategies",
)

Bar = tuple[float, float, float, int]  # bid high, low, close, tick_count (null -> 0)


class LookAheadError(AssertionError):
    pass


class Store:
    """Bars by open time. Every read names the instant ``now`` it is made for and is refused if
    the bar closes after it (``close_time = open_time + timeframe``)."""

    def __init__(
        self,
        m5: dict[datetime, Bar],
        m15: dict[datetime, Bar],
        m15_open: list[datetime],
        m15_hlc: tuple[np.ndarray, np.ndarray, np.ndarray],
        h1_open: list[datetime],
        h1_close: list[float],
    ) -> None:
        self._m5, self._m15 = m5, m15
        self._m15_open, self._m15_hlc = m15_open, m15_hlc
        self._h1_open, self._h1_close = h1_open, h1_close

    @staticmethod
    def _guard(close: datetime, now: datetime) -> None:
        if close > now:
            raise LookAheadError(f"bar closing {close} read at {now}")

    def m5(self, open_time: datetime, now: datetime) -> Bar | None:
        self._guard(open_time + M5, now)
        return self._m5.get(open_time)

    def m15(self, open_time: datetime, now: datetime) -> Bar | None:
        self._guard(open_time + M15, now)
        return self._m15.get(open_time)

    def bar(self, step: int, open_time: datetime, now: datetime) -> Bar | None:
        return self.m5(open_time, now) if step == 5 else self.m15(open_time, now)

    def atr(self, now: datetime) -> float | None:
        """Section 3.2: ``indicators.atr`` over exactly the 100 newest M15 bars closing at or
        before now (the warm-up by bar count guarantees them; a shortfall stops the script)."""
        i = bisect.bisect_right(self._m15_open, now - M15)
        if i < ATR_BARS:
            raise AssertionError(f"only {i} M15 bars before {now}; warm-up needs {ATR_BARS}")
        self._guard(self._m15_open[i - 1] + M15, now)
        h, low, c = self._m15_hlc
        return atr(h[i - ATR_BARS : i], low[i - ATR_BARS : i], c[i - ATR_BARS : i], ATR_PERIOD)

    def er(self, now: datetime) -> float | None:
        """Section 4: efficiency ratio over the 13 newest H1 closes at or before now (bar
        sequence); None with fewer bars or a zero path. The path is summed with math.fsum."""
        i = bisect.bisect_right(self._h1_open, now - H1)
        if i < ER_BARS + 1:
            return None
        self._guard(self._h1_open[i - 1] + H1, now)
        cs = self._h1_close[i - ER_BARS - 1 : i]
        path = math.fsum(abs(cs[j + 1] - cs[j]) for j in range(ER_BARS))
        return None if path == 0 else abs(cs[-1] - cs[0]) / path

    def truncated(self, a: datetime, cutoff: datetime) -> "Store":
        """A physical copy holding only the session's M5/M15 bars (open >= a) and the newest
        M15/H1 history (200 and 50 bars, more than ATR and ER need) that close at or before
        ``cutoff``: no later bar exists in it."""
        m5 = dict(self._session(self._m5, a, M5, cutoff))
        m15 = dict(self._session(self._m15, a, M15, cutoff))
        i = bisect.bisect_right(self._m15_open, cutoff - M15)
        j = bisect.bisect_right(self._h1_open, cutoff - H1)
        lo, lo1 = max(0, i - 200), max(0, j - 50)
        h, low, c = self._m15_hlc
        return Store(
            m5,
            m15,
            self._m15_open[lo:i],
            (h[lo:i].copy(), low[lo:i].copy(), c[lo:i].copy()),
            self._h1_open[lo1:j],
            self._h1_close[lo1:j],
        )

    @staticmethod
    def _session(
        bars: dict[datetime, Bar], a: datetime, step: timedelta, cutoff: datetime
    ) -> list[tuple[datetime, Bar]]:
        out, o = [], a
        while o + step <= cutoff:
            if o in bars:
                out.append((o, bars[o]))
            o += step
        return out


@dataclass(frozen=True)
class Cal:
    """Calendar facts of one session-date, from the calendar and news config only (no prices)."""

    day: date
    session: str
    a: datetime
    e: datetime
    regime: str  # "normal" (10:00 New York = 15:00 London) or "mismatch" (14:00 London)
    decision: dict[int, tuple[datetime, ...]]  # section 6.1, per variant step in minutes
    blackout: dict[int, frozenset[datetime]]  # closes in the session inside a news window
    off_session: int  # closes in (A, E - 15] whose calendar state is not (session, OPEN)
    setup_closes: tuple[datetime, ...]  # rule 1: A + 60 <= Ts <= E - 45, M15 closes
    setup_blackout: frozenset[datetime]  # rule 6: setup bars touching a news window
    hold_ok: bool  # section 10.1: E - 15 + 90 min <= the session's London bound
    clipped: bool  # E differs from the configured session end


@dataclass(frozen=True)
class Candidate:
    """One M15 bar of the setup window with what rules 2-6 need; side rules per point later."""

    ts: datetime
    read_ok: bool  # rule 5: read set complete, TWV(Ts) and ATR(Ts) exist
    blackout: bool  # rule 6 fails
    x: float
    y: float
    prior_low: float
    prior_high: float
    twv: float
    atr: float
    er: float | None


@dataclass(frozen=True)
class Day:
    """Bar-derived facts of one session-date, every value read through the look-ahead guard."""

    first_gap: datetime  # close of the first missing M5 or M15 slot with open >= A
    missing5: int
    missing15: int
    twv: dict[datetime, float | None]  # TWV(t) at each M5 close t in (A, last]; None = no TWV
    zero_weight: tuple[datetime, ...]  # opens of M5 bars with tick_count 0
    candidates: tuple[Candidate, ...]


@dataclass
class Active:
    ts: datetime
    side: str
    level: float  # X (long) or Y (short)
    broken: bool = False
    fate: str | None = None
    broken_at: datetime | None = None


@dataclass
class Outcome:
    """One session-date for one variant and point."""

    status: str = "no_trigger"
    setups: Counter[str] = field(default_factory=Counter)  # by side, counted while unspent
    counted: list[tuple[datetime, str]] = field(default_factory=list)
    fates: Counter[str] = field(default_factory=Counter)
    ambiguous: int = 0
    regime_off: int = 0
    data_gap: int = 0
    twv_undefined: int = 0
    zero_weight: int = 0
    blackout_skipped: int = 0
    # Non-normative diagnostics (ruling R2 allows them; not section 6.5 counters): a newer setup
    # arriving at exactly Ts_old + 30 (the old one counts expired, R3), or while the old one was
    # already broken (it counts broken, R2), or broken at that same instant.
    nonnormative_new_setup_at_expiry: int = 0
    nonnormative_new_setup_after_break: int = 0
    nonnormative_new_setup_after_break_same_instant: int = 0
    spend: datetime | None = None
    setup: tuple[datetime, str] | None = None
    d_s: int | None = None
    d_t: int | None = None
    twv_gap: int | None = None  # TWV(T) - X (long) or Y - TWV(T) (short), in points
    # Ruling R1: which geometry checks would fail at the spend, each regardless of precedence.
    # Only the first failure (min, cap, rr) sets the status and its official counter.
    would_fail_min: bool = False
    would_fail_cap: bool = False
    would_fail_rr: bool = False
    setup_twv_gap: int | None = None  # TWV(Ts) - X or Y - TWV(Ts) of the spent setup, points
    near: Counter[str] = field(default_factory=Counter)


def grid(x: float, rounding) -> float:  # type: ignore[no-untyped-def]
    """Section 3: q = round(x / point, 6), then floor or ceil, x point."""
    return round(rounding(round(x / POINT, 6)) * POINT, 2)


def build_cal(
    day: date,
    session: str,
    a: datetime,
    e: datetime,
    configured_end: time,
    calendar: TradingCalendar,
    news: NewsBlackout,
) -> Cal:
    last = e - LAST_DECISION
    decision: dict[int, tuple[datetime, ...]] = {}
    blackout: dict[int, frozenset[datetime]] = {}
    off = 0
    for step in VARIANTS.values():
        st, dec, blk, t = timedelta(minutes=step), [], set(), a + timedelta(minutes=step)
        while t <= last:
            state = calendar.state_at(t)
            if state.session == session and state.phase.entries_allowed:
                if news.reason_at(t) is not None:
                    blk.add(t)
                elif t > a + SETUP_FROM:
                    dec.append(t)
            else:
                off += 1
            t += st
        decision[step], blackout[step] = tuple(dec), frozenset(blk)
    closes, ts = [], a + SETUP_FROM
    while ts <= e - SETUP_TO:
        closes.append(ts)
        ts += M15
    # News windows have whole-minute bounds (checked in main), so a closed bar interval touches
    # a half-open window exactly when one of its whole minutes lies in it.
    touching = frozenset(
        ts
        for ts in closes
        if any(news.reason_at(ts - M15 + timedelta(minutes=m)) for m in range(16))
    )
    ny10 = datetime.combine(day, time(10), NEW_YORK).astimezone(LONDON).time()
    regime = {time(15): "normal", time(14): "mismatch"}[ny10]
    bound = datetime.combine(day, HOLD_BOUND[session], LONDON)
    return Cal(
        day=day,
        session=session,
        a=a,
        e=e,
        regime=regime,
        decision=decision,
        blackout=blackout,
        off_session=off,
        setup_closes=tuple(closes),
        setup_blackout=touching,
        hold_ok=last + MAX_HOLD <= bound,
        clipped=e != datetime.combine(day, configured_end, LONDON).astimezone(UTC),
    )


def london(ts: object) -> tuple[str, ...]:
    return tuple(f"{t.astimezone(LONDON):%H:%M}" for t in sorted(ts))  # type: ignore[attr-defined]


def matches_table_61(cal: Cal) -> bool:
    """The session-date's decision instants, blackout removals and rule-6 exclusions equal spec
    table 6.1 (and the decision instants are every close in (A + 60, E - 15] not removed)."""
    for step in VARIANTS.values():
        st, full = timedelta(minutes=step), []
        t = cal.a + SETUP_FROM + st
        while t <= cal.e - LAST_DECISION:
            full.append(t)
            t += st
        removed = [t for t in full if t in cal.blackout[step]]
        if london(removed) != EXPECTED_REMOVED.get((cal.session, cal.regime, step), ()):
            return False
        if cal.decision[step] != tuple(t for t in full if t not in cal.blackout[step]):
            return False
        if (london(full[:1]) + london(full[-1:])) != EXPECTED_SPAN[(cal.session, step)]:
            return False
    return london(cal.setup_blackout) == EXPECTED_EXCLUDED.get((cal.session, cal.regime), ())


def build_day(cal: Cal, store: Store, cutoff: datetime | None = None) -> Day:
    a = cal.a
    last = cal.e - LAST_DECISION if cutoff is None else min(cutoff, cal.e - LAST_DECISION)
    never = cal.e + timedelta(days=1)
    gap, missing5, missing15 = never, 0, 0
    twv: dict[datetime, float | None] = {}
    zero: list[datetime] = []
    weighted: list[float] = []
    weight, complete, t = 0, True, a + M5
    while t <= last:  # section 3.1, M5 bars in [A, t)
        b = store.m5(t - M5, now=t)
        if b is None:
            missing5 += 1
            complete = False
            gap = min(gap, t)
        else:
            if b[3] == 0:
                zero.append(t - M5)
            if complete:
                weighted.append(b[3] * ((b[0] + b[1] + b[2]) / 3))
                weight += b[3]
        twv[t] = math.fsum(weighted) / weight if complete and weight > 0 else None
        t += M5
    t = a + M15
    while t <= last:
        if store.m15(t - M15, now=t) is None:
            missing15 += 1
            gap = min(gap, t)
        t += M15
    candidates = []
    for ts in cal.setup_closes:
        if ts > last:
            break
        b = store.m15(ts - M15, now=ts)
        tw, at = twv.get(ts), store.atr(now=ts)
        read_ok = ts < gap and b is not None and tw is not None and at is not None
        nan = math.nan
        prior_low = prior_high = nan
        if read_ok:
            prior = [store.m15(a + i * M15, now=ts) for i in range((ts - a) // M15 - 1)]
            assert all(p is not None for p in prior)
            assert len(prior) >= 3
            prior_low = min(p[1] for p in prior)  # type: ignore[index]
            prior_high = max(p[0] for p in prior)  # type: ignore[index]
        candidates.append(
            Candidate(
                ts=ts,
                read_ok=read_ok,
                blackout=ts in cal.setup_blackout,
                x=b[1] if b else nan,
                y=b[0] if b else nan,
                prior_low=prior_low,
                prior_high=prior_high,
                twv=tw if tw is not None else nan,
                atr=at if at is not None else nan,
                er=store.er(now=ts),
            )
        )
    return Day(gap, missing5, missing15, twv, tuple(zero), tuple(candidates))


def classify(c: Candidate, k: float, er_max: float, near: Counter[str]) -> tuple[str, str | None]:
    """Section 5 for one point, ruling R5 (mutually exclusive): rules 1, 5 and 6 are
    prerequisites (rule 1 holds for every candidate); both sides' rules 3-4 -> ambiguous,
    whatever the regime; exactly one side with rule 2 failing or ER unavailable -> regime_off;
    exactly one side with rule 2 -> setup."""
    if not c.read_ok or c.blackout:
        return "ineligible", None
    dev_l, dev_s, need = c.twv - c.x, c.y - c.twv, k * c.atr
    for dev in (dev_l, dev_s):
        if abs(dev - need) < NEAR:
            near["deviation"] += 1
    for a, b in ((c.x, c.prior_low), (c.y, c.prior_high)):
        if abs(a - b) < NEAR:
            near["extreme"] += 1
    long_ = c.x < c.prior_low and dev_l >= need
    short = c.y > c.prior_high and dev_s >= need
    if long_ and short:
        return "ambiguous", None
    if not (long_ or short):
        return "no_side", None
    if c.er is not None and abs(c.er - er_max) < NEAR:
        near["er"] += 1
    if c.er is None or c.er > er_max:
        return "regime_off", "long" if long_ else "short"
    return "setup", "long" if long_ else "short"


def setups_of(day: Day, k: float, er_max: float) -> dict[datetime, tuple[str, float]]:
    """Every setup of the session-date for one point (variant-independent), whether or not it is
    reached before the spend: Ts -> (side, X or Y)."""
    out = {}
    for c in day.candidates:
        kind, side = classify(c, k, er_max, Counter())
        if kind == "setup":
            out[c.ts] = (side, c.x if side == "long" else c.y)  # type: ignore[assignment]
    return out


def walk(cal: Cal, day: Day, store: Store, step: int, k: float, er_max: float) -> Outcome:
    """Sections 6.1-6.5 and 8-9 for one variant and point: the stateless re-walk from A.

    Order inside an instant T (each close of the variant's timeframe):
      1. the active setup's break check by the decision bar closing at T (trigger rule 3);
      2. a decision instant evaluates rules 2-6 and spends; a blackout close counts
         ``blackout_skipped`` if rules 2-6 hold; a spend ends the session-date at T, so a
         setup bar also closing at T is neither classified nor counted (ruling R4);
      3. at T = Ts + 30, a setup still untriggered and unbroken after steps 1-2 counts expired
         (ruling R3);
      4. a setup closing at T is counted and becomes active for later instants; it replaces
         the active setup only if that one is unbroken and has not expired (R2, R3).
    A setup has one terminal outcome, the first in time: triggered, broken, replaced or
    expired; at one instant breaking precedes replacement (ruling R2).
    """
    out = Outcome()
    st = timedelta(minutes=step)
    candidates = {c.ts: c for c in day.candidates}
    decision = set(cal.decision[step])
    active: Active | None = None
    saw_gap = saw_twv = False
    last = cal.e - LAST_DECISION
    t = cal.a + st

    def holds(t: datetime) -> bool:  # trigger rules 2-6 at t
        if active is None or active.broken or not (active.ts < t <= active.ts + EXPIRY):
            return False
        if t >= day.first_gap:
            return False
        tw = day.twv[t]
        if tw is None:
            return False
        d, p = store.bar(step, t - st, now=t), store.bar(step, t - 2 * st, now=t)
        assert d is not None, (cal.day, cal.session, t)
        assert p is not None, (cal.day, cal.session, t)
        if abs(d[2] - tw) < NEAR:
            out.near["room"] += 1
        if abs(d[2] - (p[0] if active.side == "long" else p[1])) < NEAR:
            out.near["rejection"] += 1
        if active.side == "long":
            return d[2] > p[0] and d[2] < tw
        return d[2] < p[1] and d[2] > tw

    while t <= last:
        if active is not None and active.ts < t:  # 1. trigger rule 3, every decision bar
            d = store.bar(step, t - st, now=t)
            level_side = 1 if active.side == "long" else 0
            if not active.broken and d is not None and abs(d[level_side] - active.level) < NEAR:
                out.near["break"] += 1
            passes = d is not None and (
                d[1] < active.level if active.side == "long" else d[0] > active.level
            )
            if passes and not active.broken:
                active.broken, active.broken_at = True, t
                if active.fate is None:
                    active.fate = "broken"
                    out.fates["broken"] += 1
        if t in decision:  # 2.
            if t >= day.first_gap:
                out.data_gap += 1
                saw_gap = True
            elif day.twv[t] is None:
                out.twv_undefined += 1
                saw_twv = True
            elif holds(t):
                assert active is not None
                assert active.fate is None
                active.fate = "triggered"
                out.fates["triggered"] += 1
                spend(out, cal, day, store, step, t, active)
                break
        elif t in cal.blackout[step] and holds(t):
            out.blackout_skipped += 1
        dropped: Active | None = None
        if active is not None and t == active.ts + EXPIRY:  # 3.
            if active.fate is None:
                active.fate = "expired"
                out.fates["expired"] += 1
            dropped, active = active, None
        c = candidates.get(t)  # 4.
        if c is not None:
            kind, side = classify(c, k, er_max, out.near)
            if kind == "setup":
                old = active or dropped  # dropped: it reached Ts + 30 at this same instant
                if active is not None and active.fate is None:
                    active.fate = "replaced"
                    out.fates["replaced"] += 1
                elif old is not None and old.fate == "broken":
                    out.nonnormative_new_setup_after_break += 1
                    out.nonnormative_new_setup_after_break_same_instant += old.broken_at == t
                elif dropped is not None and dropped.fate == "expired":
                    out.nonnormative_new_setup_at_expiry += 1
                active = Active(t, side, c.x if side == "long" else c.y)  # type: ignore[arg-type]
                out.setups[side] += 1
                out.counted.append((t, side))
            elif kind == "ambiguous":
                out.ambiguous += 1
            elif kind == "regime_off":
                out.regime_off += 1
        t += st
    end = out.spend or last
    out.zero_weight = sum(o + M5 <= end for o in day.zero_weight)
    if out.spend is None:
        if not cal.decision[step]:
            out.status = "no_decision_time"
        elif saw_gap:
            out.status = "data_gap"
        elif saw_twv:
            out.status = "twv_undefined"
    return out


def spend(
    out: Outcome, cal: Cal, day: Day, store: Store, step: int, t: datetime, active: Active
) -> None:
    """Section 6.3 then sections 8, 8a and 9 at the spend instant t. Ruling R1: evaluated in
    the order minimum, cap, reward:risk; the first failure is the sole status (and official
    counter); every failing check is kept as a would_fail_* flag."""
    d = store.bar(step, t - timedelta(minutes=step), now=t)
    assert d is not None
    ref, tw = d[2], day.twv[t]
    assert tw is not None
    setup_twv = next(c.twv for c in day.candidates if c.ts == active.ts)
    if active.side == "long":
        stop = grid(active.level - BUFFER_POINTS * POINT, math.floor)
        target = grid(tw, math.floor)
        out.d_s = round((ref - stop) / POINT)
        out.d_t = round((target - ref) / POINT)
        out.twv_gap = math.floor(round((tw - active.level) / POINT, 6))
        out.setup_twv_gap = math.floor(round((setup_twv - active.level) / POINT, 6))
    else:
        stop = grid(active.level + BUFFER_POINTS * POINT, math.ceil)
        target = grid(tw, math.ceil)
        out.d_s = round((stop - ref) / POINT)
        out.d_t = round((ref - target) / POINT)
        out.twv_gap = math.floor(round((active.level - tw) / POINT, 6))
        out.setup_twv_gap = math.floor(round((active.level - setup_twv) / POINT, 6))
    out.would_fail_min = out.d_s < MIN_POINTS
    out.would_fail_cap = out.d_s > CAP_POINTS
    out.would_fail_rr = out.d_t < out.d_s
    out.status = (
        "stop_below_minimum"
        if out.would_fail_min
        else "stop_above_cap"
        if out.would_fail_cap
        else "reward_risk_below_min"
        if out.would_fail_rr
        else "signal"
    )
    out.spend, out.setup = t, (active.ts, active.side)


def first_trigger(
    cal: Cal, day: Day, store: Store, step: int, setups: dict[datetime, tuple[str, float]]
) -> datetime | None:
    """Independent re-derivation of the spend instant: for each decision instant in order, the
    active setup straight from section 6.2 (largest Ts < T with T <= Ts + 30), breaking by
    scanning every decision bar closing in (Ts, T], then rules 4-6. Never looks at geometry."""
    st = timedelta(minutes=step)
    for t in cal.decision[step]:
        if t >= day.first_gap:
            return None  # no later read set can be complete
        tw = day.twv[t]
        prior = [ts for ts in setups if ts < t]
        if tw is None or not prior or t > max(prior) + EXPIRY:
            continue
        ts = max(prior)
        side, level = setups[ts]
        n = (t - ts) // st
        bars = [store.bar(step, ts + i * st, now=t) for i in range(n)]
        assert all(b is not None for b in bars)
        if side == "long" and any(b[1] < level for b in bars):  # type: ignore[index]
            continue
        if side == "short" and any(b[0] > level for b in bars):  # type: ignore[index]
            continue
        d, p = bars[-1], store.bar(step, t - 2 * st, now=t)
        assert d is not None
        assert p is not None
        if (d[2] > p[0] and d[2] < tw) if side == "long" else (d[2] < p[1] and d[2] > tw):
            return t
    return None


def fold_of(d: date) -> str:
    return next(name for name, a, b in FOLDS if a <= d <= b)


def files_digest(hashes: dict[Path, str]) -> str:
    h = hashlib.sha256()
    for p in sorted(hashes):
        h.update(p.name.encode())
        h.update(hashes[p].encode())
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def methodology_commit() -> str:
    """The run must start from a clean commit: no tracked change and no untracked file except
    this script's own two outputs (present on the byte-identity rerun). Returns HEAD."""
    outputs = {f"{OUT.as_posix()}/{STEM}.{ext}" for ext in ("md", "csv")}
    dirty = [
        line
        for line in git("status", "--porcelain", "--untracked-files=all").splitlines()
        if not (line.startswith("?? ") and line[3:] in outputs)
    ]
    if dirty:
        raise SystemExit(f"worktree is not a clean commit: {dirty}")
    if git("hash-object", SPEC.as_posix()) != SPEC_BLOB:
        raise SystemExit(f"{SPEC} is not draft revision 4 (blob {SPEC_BLOB})")
    return git("rev-parse", "HEAD")


def load_bars(paths: list[Path]) -> tuple[pl.DataFrame, int, int]:
    """Ruling R6: each file is scanned with the predicate open_time < CUT. Returns the frame,
    the files' total row count (Parquet metadata) and the number of rows excluded; the excluded
    rows' values are never collected."""
    cols = ["open_time", "high", "low", "close", "tick_count"]
    cut = pl.lit(CUT, dtype=pl.Datetime("ms", "UTC"))
    parts, total = [], 0
    for p in paths:
        lf = pl.scan_parquet(p)
        total += int(lf.select(pl.len()).collect().item())
        parts.append(lf.filter(pl.col("open_time") < cut).select(cols).collect())
    frame = pl.concat(parts)
    if frame.filter(pl.col("open_time") >= cut).height:
        raise AssertionError(f"a returned frame holds a row at or after {CUT}")
    return frame, total, total - frame.height


def verify(
    cal: Cal, day: Day, store: Store, step: int, k: float, e: float, o: Outcome, checks: Counter
) -> None:
    """Invariants of one walk, independent of the walk's own bookkeeping."""
    full = setups_of(day, k, e)
    # spend = the first instant meeting rules 1-6, re-derived without geometry
    assert first_trigger(cal, day, store, step, full) == o.spend, (cal, step, k, e)
    checks["spend re-derived independently"] += 1
    # counted setups = the shared setup list truncated at this variant's spend (ruling R4)
    expect = [(ts, sd) for ts, (sd, _) in sorted(full.items())]
    expect = [x for x in expect if o.spend is None or x[0] < o.spend]
    assert o.counted == expect, (cal, step, k, e)
    checks["counted setups = shared list up to the spend"] += 1
    assert sum(o.fates.values()) == len(o.counted), (cal, step, k, e)
    assert o.fates["triggered"] == (o.spend is not None)
    assert o.spend is None or o.spend not in cal.blackout[step]
    assert o.spend is None or o.spend < day.first_gap
    assert (o.spend is None) == (o.status not in SPENT)
    if o.status == "signal":
        assert o.twv_gap is not None
        assert o.twv_gap >= NECESSARY_POINTS, (cal, step, k, e)


# Synthetic tests: run before any market data is read (and alone with --synthetic-tests). They
# use hand-built bars only: no config, data directory, git or snapshot.

_D0 = datetime(2021, 1, 5, tzinfo=UTC)  # a UK-winter Tuesday: London = UTC, A = 07:00Z


def _t(hm: str) -> datetime:
    h, m = hm.split(":")
    return _D0.replace(hour=int(h), minute=int(m))


def _syn_cal(blackout: dict[int, tuple[str, ...]] | None = None, decisions: bool = True) -> Cal:
    """london_morning 07:00-11:00Z with the decision instants of section 6.1, minus `blackout`."""
    a, e = _t("07:00"), _t("11:00")
    decision: dict[int, tuple[datetime, ...]] = {}
    removed: dict[int, frozenset[datetime]] = {}
    for step in VARIANTS.values():
        st = timedelta(minutes=step)
        removed[step] = frozenset(_t(x) for x in (blackout or {}).get(step, ()))
        full, t = [], a + SETUP_FROM + st
        while t <= e - LAST_DECISION:
            full.append(t)
            t += st
        decision[step] = tuple(t for t in full if t not in removed[step]) if decisions else ()
    closes = tuple(a + SETUP_FROM + i * M15 for i in range(10))  # 08:00 ... 10:15
    return Cal(
        _D0.date(),
        "london_morning",
        a,
        e,
        "normal",
        decision,
        removed,
        0,
        closes,
        frozenset(),
        True,
        False,
    )


def _syn_store_from(m5: dict[datetime, Bar], *, trend: bool = False, history: int = 120) -> Store:
    """Session M15 bars aggregated from the M5 bars present; `history` M15 bars before A with
    true range 1; 24 H1 bars (20 before A) with closes alternating (ER 0) or rising (ER 1)."""
    m15: dict[datetime, Bar] = {
        _t("07:00") - (i + 1) * M15: (2000.5, 1999.5, 2000.0, 1000) for i in range(history)
    }
    o = _t("07:00")
    while o < _t("11:00"):
        bars = [m5[o + i * M5] for i in range(3) if o + i * M5 in m5]
        if bars:
            m15[o] = (
                max(b[0] for b in bars),
                min(b[1] for b in bars),
                bars[-1][2],
                sum(b[3] for b in bars),
            )
        o += M15
    m15_open = sorted(m15)
    hlc = tuple(np.array([m15[x][i] for x in m15_open], dtype=np.float64) for i in range(3))
    h1_open = [_t("07:00") + (i - 20) * H1 for i in range(24)]
    h1_close = [2000.0 + (i if trend else 0.5 * (i % 2)) for i in range(24)]
    return Store(m5, m15, m15_open, hlc, h1_open, h1_close)  # type: ignore[arg-type]


def _syn_store(
    shapes: dict[str, tuple[float, float, float]],
    base: tuple[float, float, float] = (2000.5, 1999.5, 2000.0),
    *,
    missing: tuple[str, ...] = (),
    weightless: bool = False,
    trend: bool = False,
) -> Store:
    """Session M15 shapes (high, low, close) by open time, each split into three M5 bars
    (h c c, c l c, c c c). Slots before the first shape are `base` bars (weight 1000, or 0 if
    `weightless`); slots after it are flat at the previous close. Shape and flat bars weigh 0,
    so TWV is exactly the base typical price. `missing` M5 slots are left out."""
    m5: dict[datetime, Bar] = {}
    prev: float | None = None
    o = _t("07:00")
    while o < _t("11:00"):
        key = f"{o:%H:%M}"
        if key in shapes:
            h, lo, c = shapes[key]
            parts, w, prev = [(h, c, c), (c, lo, c), (c, c, c)], 0, c
        elif prev is None:
            parts, w = [base] * 3, 0 if weightless else 1000
        else:
            parts, w = [(prev, prev, prev)] * 3, 0
        for i, (h, lo, c) in enumerate(parts):
            if f"{o + i * M5:%H:%M}" not in missing:
                m5[o + i * M5] = (h, lo, c, w)
        o += M15
    return _syn_store_from(m5, trend=trend)


def _syn_run(cal: Cal, store: Store, step: int, k: float = 1.5, e: float = 0.30) -> Outcome:
    """The walk plus every invariant the real run checks, including the truncated store."""
    day = build_day(cal, store)
    o = walk(cal, day, store, step, k, e)
    verify(cal, day, store, step, k, e, o, Counter())
    cutoff = o.spend or cal.e - LAST_DECISION
    small = store.truncated(cal.a, cutoff)
    assert walk(cal, build_day(cal, small, cutoff), small, step, k, e) == o, "truncated store"
    return o


def synthetic_tests() -> int:
    """Rules, rulings R1-R6 and the numerical rulings on hand-built bars. Returns the number of
    cases; any failure raises and stops the script before market data is read."""
    n = 0

    def check(name: str, got: object, want: object) -> None:
        nonlocal n
        assert got == want, f"synthetic test {name}: got {got!r}, expected {want!r}"
        n += 1

    # R5 classification, exact comparisons, near-boundary reporting.
    def cand(**kw: object) -> Candidate:
        v = {
            "x": 1997.0,
            "y": 2001.0,
            "prior_low": 1998.0,
            "prior_high": 2002.0,
            "twv": 2000.0,
            "atr": 2.0,
            "er": 0.2,
        }
        v.update(kw)
        return Candidate(
            _t("08:00"), bool(v.pop("read_ok", True)), bool(v.pop("blackout", False)), **v
        )  # type: ignore[arg-type]

    near: Counter[str] = Counter()
    check(
        "R5 deviation equal to k x ATR passes", classify(cand(), 1.5, 0.30, near), ("setup", "long")
    )
    check("near-boundary deviation reported", near["deviation"], 1)
    for name, kw, er_max, want in (
        ("equal to the prior low fails", {"x": 1998.0, "twv": 2001.0}, 0.30, ("no_side", None)),
        (
            "equal to the prior high fails",
            {"x": 1999.0, "y": 2002.0, "twv": 1999.0},
            0.30,
            ("no_side", None),
        ),
        ("deviation 0.01 short fails", {"x": 1997.01}, 0.30, ("no_side", None)),
        ("ER equal to er_max passes", {"er": 0.30}, 0.30, ("setup", "long")),
        ("ER equal to er_max 0.50 passes", {"er": 0.50}, 0.50, ("setup", "long")),
        ("R5 one side, regime fails", {"er": 0.31}, 0.30, ("regime_off", "long")),
        ("R5 one side, ER unavailable", {"er": None}, 0.30, ("regime_off", "long")),
        ("short side", {"x": 1999.0, "y": 2003.0}, 0.30, ("setup", "short")),
        ("R5 both sides", {"y": 2003.0}, 0.30, ("ambiguous", None)),
        ("R5 both sides, ER unavailable", {"y": 2003.0, "er": None}, 0.30, ("ambiguous", None)),
        ("R5 both sides, regime fails", {"y": 2003.0, "er": 0.9}, 0.30, ("ambiguous", None)),
        ("R5 rule 5 fails first", {"y": 2003.0, "read_ok": False}, 0.30, ("ineligible", None)),
        ("R5 rule 6 fails first", {"y": 2003.0, "blackout": True}, 0.30, ("ineligible", None)),
    ):
        check(name, classify(cand(**kw), 1.5, er_max, Counter()), want)

    # ER (13 H1 closes, path with math.fsum), ATR over exactly 100 bars, look-ahead guard.
    def h1_store(closes: list[float]) -> Store:
        opens = [_t("08:00") - (len(closes) - i) * H1 for i in range(len(closes))]
        return Store({}, {}, [], (np.zeros(0), np.zeros(0), np.zeros(0)), opens, closes)

    alt = [2000.0, 2001.0] * 6
    check("ER hand value", h1_store([*alt, 2003.0]).er(_t("08:00")), 3 / 13)
    check("ER uses only the 13 newest", h1_store([1900.0, *alt, 2003.0]).er(_t("08:00")), 3 / 13)
    check("ER needs 13 closes", h1_store([*alt]).er(_t("08:00")), None)
    check("ER zero path", h1_store([2000.0] * 13).er(_t("08:00")), None)
    check("ER trending store", _syn_store({}, trend=True).er(_t("08:00")), 1.0)
    check("ER alternating store", _syn_store({}).er(_t("08:00")), 0.0)
    m15o = [_t("07:00") - (150 - i) * M15 for i in range(150)]
    hi = np.array([2000.0 + (i % 7) for i in range(150)])
    lo = hi - np.array([1.0 + (i % 5) for i in range(150)])
    cl = (hi + lo) / 2
    s150 = Store({}, {}, m15o, (hi, lo, cl), [], [])
    check(
        "ATR over exactly the 100 newest",
        s150.atr(_t("07:00")),
        atr(hi[50:], lo[50:], cl[50:], ATR_PERIOD),
    )
    check("ATR window matters", s150.atr(_t("07:00")) != atr(hi, lo, cl, ATR_PERIOD), True)
    check(
        "ATR ignores bars closing after now",
        s150.atr(_t("06:45")),
        atr(hi[49:149], lo[49:149], cl[49:149], ATR_PERIOD),
    )
    for name, fn in (
        (
            "ATR shortfall stops",
            lambda: Store({}, {}, m15o[:99], (hi[:99], lo[:99], cl[:99]), [], []).atr(_t("07:00")),
        ),
        ("look-ahead read refused", lambda: _syn_store({}).m5(_t("08:00"), now=_t("08:04"))),
    ):
        try:
            fn()
        except AssertionError:
            check(name, True, True)
        else:
            raise AssertionError(f"synthetic test {name}: no error")

    # TWV (section 3.1): slots [A, t), fsum / integer weights, zero weights, a missing slot.
    m5: dict[datetime, Bar] = {}
    for i in range(48):
        p = 2000.0 + (i % 9) * 0.37
        m5[_t("07:00") + i * M5] = (p + 0.4, p - 0.3, p, i % 4)
    del m5[_t("08:30")]
    day = build_day(_syn_cal(), _syn_store_from(m5))
    for hm in ("07:05", "07:10", "08:00", "08:30"):
        t = _t(hm)
        bars = [m5[o] for o in sorted(m5) if o + M5 <= t]
        w = sum(b[3] for b in bars)
        want = math.fsum(b[3] * ((b[0] + b[1] + b[2]) / 3) for b in bars) / w if w else None
        check(f"TWV at {hm}", day.twv[t], want)
    check("TWV undefined with zero weight", day.twv[_t("07:05")], None)
    check("TWV undefined after a missing slot", day.twv[_t("08:35")], None)
    check("first gap", day.first_gap, _t("08:35"))
    check(
        "zero-weight bars",
        len(day.zero_weight),
        sum(1 for o, b in m5.items() if b[3] == 0 and o + M5 <= _t("10:45")),
    )

    # R6: the load predicate excludes open_time >= CUT and only counts what it excludes.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "2025-09-30.parquet"
        schema = {
            "open_time": pl.Datetime("ms", "UTC"),
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "tick_count": pl.Int64,
        }
        rows = [(CUT + i * M5, 1.0, 1.0, 1.0, 1) for i in (-2, -1, 0, 1)]
        pl.DataFrame(rows, schema=schema, orient="row").write_parquet(path)
        frame, total, excluded = load_bars([path])
        check("R6 rows read / excluded", (total, excluded, frame.height), (4, 2, 2))
        check("R6 nothing at or after the cut", frame["open_time"].max() < CUT, True)  # type: ignore[operator]

    # Walk scenarios: R1-R4, geometry boundaries, both sides, statuses (spec 6.2, 6.3, 6.5, 8, 9).
    pre, setup = {"07:30": (2000.0, 1990.0, 1990.5)}, {"07:45": (1989.5, 1985.0, 1986.0)}
    long_ = {**pre, **setup}  # long setup at Ts 08:00, X = 1985.00
    trig = {"08:00": (1990.5, 1986.0, 1990.0)}  # rejection: m15 at 08:15, m5 at 08:05
    flat86 = {"08:00": (1986.0, 1986.0, 1986.0)}
    off_grid = (2000.51, 1999.5, 2000.0)  # typical price 2000.00333...

    def sig(
        spend: dict[int, str], d_s: int = 525, d_t: int = 1000, side: str = "long"
    ) -> dict[str, object]:
        return {
            "status": "signal",
            "spend": spend,
            "d_s": d_s,
            "d_t": d_t,
            "setups": {side: 1},
            "fates": {"triggered": 1},
        }

    def geo(status: str, d_s: int, d_t: int, flags: tuple[bool, bool, bool]) -> dict[str, object]:
        return {
            "status": status,
            "spend": {15: "08:15", 5: "08:05"},
            "d_s": d_s,
            "d_t": d_t,
            "flags": flags,
        }

    def lb(
        low: float, base: float, trigger: tuple[float, float, float] = trig["08:00"]
    ) -> dict[str, object]:
        return {
            "shapes": {
                "07:30": (base, 1990.0, 1990.5),
                "07:45": (1989.5, low, 1986.0),
                "08:00": trigger,
            }
        }

    early = {15: "08:15", 5: "08:05"}
    scenarios: list[tuple[str, dict[str, object], dict[str, object], dict[str, object]]] = [
        ("long signal", {"shapes": {**long_, **trig}}, {}, sig(early)),
        (
            "long signal, floor of an off-grid TWV",
            {"shapes": {**long_, **trig}, "base": off_grid},
            {},
            sig(early),
        ),
        (
            "short signal, ceil of an off-grid TWV",
            {
                "shapes": {
                    "07:30": (2010.0, 2000.0, 2009.5),
                    "07:45": (2015.0, 2010.5, 2014.0),
                    "08:00": (2014.0, 2009.0, 2010.0),
                },
                "base": off_grid,
            },
            {},
            sig(early, d_t=999, side="short"),
        ),
        (
            "R2 a same-side newer setup breaks the old one",
            {"shapes": {**long_, "08:00": (1986.0, 1983.0, 1984.0)}},
            {},
            {
                "status": "no_trigger",
                "setups": {"long": 2},
                "fates": {"broken": 1, "expired": 1},
                "nonnormative_new_setup_after_break": 1,
                "nonnormative_new_setup_after_break_same_instant": {15: 1, 5: 0},
            },
        ),
        (
            "R2 an opposite-side newer setup replaces an unbroken one",
            {"shapes": {**long_, "08:00": (2015.0, 1986.0, 2010.0)}},
            {},
            {
                "status": "no_trigger",
                "setups": {"long": 1, "short": 1},
                "fates": {"replaced": 1, "expired": 1},
            },
        ),
        (
            "R3 expiry at Ts + 30, a new setup then does not replace",
            {"shapes": {**long_, **flat86, "08:15": (2015.0, 1986.0, 2010.0)}},
            {},
            {
                "status": "no_trigger",
                "setups": {"long": 1, "short": 1},
                "fates": {"expired": 2},
                "nonnormative_new_setup_at_expiry": 1,
            },
        ),
        (
            "R3 final trigger at Ts + 30; R4 a setup closing at the spend is not counted",
            {"shapes": {**long_, **flat86, "08:15": (2015.0, 1986.0, 1990.0)}},
            {},
            sig({15: "08:30", 5: "08:20"}),
        ),
        (
            "a bar touching X exactly does not break the setup",
            {
                "shapes": {
                    **long_,
                    "08:00": (1986.0, 1985.0, 1986.0),
                    "08:15": (1990.5, 1986.0, 1990.0),
                }
            },
            {},
            {**sig({15: "08:30", 5: "08:20"}), "near_break": 1},
        ),
        (
            "R1 below the minimum",
            {"shapes": {**long_, "08:00": (1989.7, 1986.0, 1989.7)}},
            {},
            geo("stop_below_minimum", 495, 1030, (True, False, False)),
        ),
        (
            "R1 below the minimum and reward:risk: minimum first",
            {**lb(1985.0, 1994.0, (1989.7, 1986.0, 1989.7)), "base": (1994.5, 1993.5, 1994.0)},
            {},
            geo("stop_below_minimum", 495, 430, (True, False, True)),
        ),
        (
            "R1 reward:risk only",
            {**lb(1985.0, 1994.0), "base": (1994.5, 1993.5, 1994.0)},
            {},
            geo("reward_risk_below_min", 525, 400, (False, False, True)),
        ),
        (
            "R1 above the cap and reward:risk: cap first",
            {**lb(1965.0, 2000.0)},
            {},
            geo("stop_above_cap", 2525, 1000, (False, True, True)),
        ),
        (
            "d_S = d_T = 500 and TWV(T) - X = 9.75 USD",
            {**lb(1985.25, 1995.0), "base": (1995.5, 1994.5, 1995.0)},
            {},
            {**geo("signal", 500, 500, (False, False, False)), "twv_gap": 975},
        ),
        (
            "d_S = 499",
            {**lb(1985.26, 1995.0), "base": (1995.5, 1994.5, 1995.0)},
            {},
            geo("stop_below_minimum", 499, 500, (True, False, False)),
        ),
        (
            "d_T = d_S - 1 (float-noise target)",
            {**lb(1985.25, 1994.99), "base": (1995.49, 1994.49, 1994.99)},
            {},
            geo("reward_risk_below_min", 500, 499, (False, False, True)),
        ),
        (
            "d_S = d_T = 2,000",
            {**lb(1970.25, 2010.0, (1990.5, 1971.0, 1990.0)), "base": (2010.5, 2009.5, 2010.0)},
            {},
            geo("signal", 2000, 2000, (False, False, False)),
        ),
        (
            "d_S = 2,001",
            {**lb(1970.24, 2010.0, (1990.5, 1971.0, 1990.0)), "base": (2010.5, 2009.5, 2010.0)},
            {},
            geo("stop_above_cap", 2001, 2000, (False, True, True)),
        ),
        (
            "blackout instant: skipped, never spends",
            {"shapes": {**long_, **trig}},
            {"blackout": {15: ("08:15",), 5: ("08:05",)}},
            {
                "status": "no_trigger",
                "spend": None,
                "blackout_skipped": 1,
                "setups": {"long": 1},
                "fates": {"expired": 1},
            },
        ),
        (
            "missing M5 slot: data_gap at every later decision instant",
            {"shapes": {**long_, **trig}, "missing": ("08:00",)},
            {},
            {
                "status": "data_gap",
                "data_gap": {15: 11, 5: 33},
                "setups": {"long": 1},
                "fates": {"expired": 1},
            },
        ),
        (
            "zero total weight: twv_undefined",
            {"shapes": {}, "weightless": True},
            {},
            {
                "status": "twv_undefined",
                "twv_undefined": {15: 11, 5: 33},
                "zero_weight": 45,
                "setups": {},
            },
        ),
        (
            "R5 regime off (trending H1)",
            {"shapes": {**long_, **trig}, "trend": True},
            {},
            {"status": "no_trigger", "regime_off": 1, "ambiguous": 0, "setups": {}},
        ),
        (
            "R5 ambiguous",
            {"shapes": {"07:45": (2015.0, 1985.0, 2000.0)}},
            {},
            {"status": "no_trigger", "ambiguous": 1, "regime_off": 0, "setups": {}},
        ),
        (
            "R5 ambiguous regardless of the regime",
            {"shapes": {"07:45": (2015.0, 1985.0, 2000.0)}, "trend": True},
            {},
            {"status": "no_trigger", "ambiguous": 1, "regime_off": 0, "setups": {}},
        ),
        (
            "no decision instant",
            {"shapes": {**long_, **trig}},
            {"decisions": False},
            {"status": "no_decision_time", "spend": None},
        ),
    ]
    for name, store_kw, cal_kw, want in scenarios:
        store = _syn_store(**store_kw)  # type: ignore[arg-type]
        cal = _syn_cal(**cal_kw)  # type: ignore[arg-type]
        for v, step in VARIANTS.items():
            for e in ER_MAX:
                o = _syn_run(cal, store, step, 1.5, e)
                for key, value in want.items():
                    value = (
                        value.get(step, value)
                        if isinstance(value, dict) and set(value) <= {5, 15} and value
                        else value
                    )
                    if key == "spend" and value is not None:
                        value = _t(value)  # type: ignore[arg-type]
                    got = (
                        (o.would_fail_min, o.would_fail_cap, o.would_fail_rr)
                        if key == "flags"
                        else o.near["break"]
                        if key == "near_break"
                        else getattr(o, key)
                    )
                    check(f"{name} [{v}, er_max {e}] {key}", got, value)
    return n


def main() -> int:
    if sys.argv[1:] == ["--synthetic-tests"]:
        print(f"synthetic tests passed: {synthetic_tests()} cases")
        return 0
    if sys.argv[1:]:
        raise SystemExit(f"unknown arguments {sys.argv[1:]}")
    # Gates before any market data is read: clean commit and spec, synthetic tests, config.
    commit = methodology_commit()
    synthetic = synthetic_tests()
    settings = load_settings()
    cfg = settings.config
    layout = DataLayout(settings.data_dir)
    sealed_first = cfg.backtest.sealed_period.first
    if sealed_first <= LAST:
        raise SystemExit(f"{LAST} is not before the sealed period ({sealed_first})")
    if cfg.risk.atr_period != ATR_PERIOD:
        raise SystemExit(f"atr_period {cfg.risk.atr_period} != {ATR_PERIOD} (spec section 3.2)")
    nb = cfg.risk.news_blackout
    for w in [*nb.daily_windows, nb.fomc_window]:
        if w.start.second or w.start.microsecond or w.end.second or w.end.microsecond:
            raise SystemExit(f"news window {w} is not on whole minutes")
    market = MarketHours(cfg.data.market)
    calendar = TradingCalendar(cfg.sessions, market)
    news = NewsBlackout(nb)
    # R6's cut is the market open of trading date LAST + 1 (calendar config only, no data).
    if market.session(LAST + timedelta(days=1)).open != CUT:  # type: ignore[union-attr]
        raise SystemExit(f"market open of {LAST + timedelta(days=1)} is not {CUT}")

    # File selection by name only (row counts from Parquet metadata for the warm-up); no
    # partition dated after LAST is ever opened. Warm-up exactly by bar count (13 H1, 100 M15):
    # the fewest whole days before FIRST holding that many bars; no calendar-day buffer.
    selected: dict[Timeframe, list[date]] = {}
    for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1):
        days = [d for d in days_in(layout.canonical_bars_dir(SYMBOL, tf)) if d <= LAST]
        lo = FIRST
        if tf in WARMUP:  # section 3.3 by bar count, as backtest.loading.plan_market counts
            counted = 0
            for d in sorted((d for d in days if d < FIRST), reverse=True):
                path = layout.canonical_bars(SYMBOL, tf, d)
                counted += int(pl.scan_parquet(path).select(pl.len()).collect().item())
                if counted >= WARMUP[tf]:
                    lo = d
                    break
            else:
                raise SystemExit(f"warm-up needs {WARMUP[tf]} {tf} bars before {FIRST}")
        selected[tf] = [d for d in days if lo <= d <= LAST]
        assert max(selected[tf]) <= LAST < sealed_first
    paths = {tf: [layout.canonical_bars(SYMBOL, tf, d) for d in ds] for tf, ds in selected.items()}
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for ps in paths.values() for p in ps}

    frames: dict[Timeframe, pl.DataFrame] = {}
    rows_total: dict[Timeframe, int] = {}
    excluded: dict[Timeframe, int] = {}
    for tf, ps in paths.items():
        frames[tf], rows_total[tf], excluded[tf] = load_bars(ps)
    null_ticks = int(frames[Timeframe.M5]["tick_count"].null_count())

    def as_dict(f: pl.DataFrame) -> dict[datetime, Bar]:
        out: dict[datetime, Bar] = {}
        for o, h, lo, c, w in f.iter_rows():
            assert o not in out, o
            assert o < CUT, "row at or after the R6 cut"
            out[o.astimezone(UTC)] = (h, lo, c, 0 if w is None else int(w))
        return out

    m5, m15 = as_dict(frames[Timeframe.M5]), as_dict(frames[Timeframe.M15])
    m15_open = sorted(m15)
    m15_hlc = tuple(np.array([m15[o][i] for o in m15_open], dtype=np.float64) for i in range(3))
    h1 = as_dict(frames[Timeframe.H1])
    h1_open = sorted(h1)
    store = Store(m5, m15, m15_open, m15_hlc, h1_open, [h1[o][2] for o in h1_open])  # type: ignore[arg-type]

    trading = market.trading_dates(FIRST, LAST)
    weekdays = sum(
        (FIRST + timedelta(days=i)).weekday() < 5 for i in range((LAST - FIRST).days + 1)
    )
    cals: list[Cal] = []
    missing_sessions: list[tuple[date, str]] = []
    for d in trading:
        sched = calendar.schedule_for(d)
        windows = {w.name: w for w in sched.sessions} if sched else {}
        for s in SESSIONS:
            if s not in windows:
                missing_sessions.append((d, s))
                continue
            w = windows[s]
            cals.append(
                build_cal(d, s, w.start, w.end, cfg.sessions.sessions[s].end, calendar, news)
            )

    keys = [(v, k, e) for v in VARIANTS for k, e in POINTS]
    results: dict[tuple[str, float, float], list[Outcome]] = {key: [] for key in keys}
    days: list[Day] = []
    checks: Counter[str] = Counter()
    for cal in cals:
        day = build_day(cal, store)
        days.append(day)
        for v, step in VARIANTS.items():
            outs = {}
            for k, e in POINTS:
                o = walk(cal, day, store, step, k, e)
                results[(v, k, e)].append(o)
                outs[(k, e)] = o
                verify(cal, day, store, step, k, e, o, checks)
            # the same decisions on a store physically truncated at each decision instant
            for cutoff in sorted({o.spend or cal.e - LAST_DECISION for o in outs.values()}):
                small = store.truncated(cal.a, cutoff)
                small_day = build_day(cal, small, cutoff)
                for (k, e), o in outs.items():
                    if (o.spend or cal.e - LAST_DECISION) == cutoff:
                        assert walk(cal, small_day, small, step, k, e) == o, (cal, v, k, e)
                        checks["identical on a store truncated at the decision"] += 1

    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in hashes}
    assert after == hashes, "an input file changed during the run"
    loaded = sorted(m for m in sys.modules if m.startswith(FORBIDDEN_MODULES))
    assert not loaded, f"forbidden modules imported: {loaded}"

    ctx = Context(
        cfg=cfg,
        commit=commit,
        synthetic=synthetic,
        selected=selected,
        paths=paths,
        hashes=hashes,
        rows_total=rows_total,
        excluded=excluded,
        null_ticks=null_ticks,
        trading=trading,
        weekdays=weekdays,
        missing_sessions=missing_sessions,
        cals=cals,
        days=days,
        results=results,
        checks=checks,
    )
    rows = count_rows(ctx)
    with (OUT / f"{STEM}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["variant", "k_dev", "er_max", "scope", "slice", "metric", "count"])
        w.writerows(rows)
    (OUT / f"{STEM}.md").write_text(render(ctx), encoding="utf-8", newline="\n")
    print("written to", OUT)
    return 0


@dataclass
class Context:
    cfg: object
    commit: str
    synthetic: int
    selected: dict[Timeframe, list[date]]
    paths: dict[Timeframe, list[Path]]
    hashes: dict[Path, str]
    rows_total: dict[Timeframe, int]
    excluded: dict[Timeframe, int]
    null_ticks: int
    trading: list[date]
    weekdays: int
    missing_sessions: list[tuple[date, str]]
    cals: list[Cal]
    days: list[Day]
    results: dict[tuple[str, float, float], list[Outcome]]
    checks: Counter[str]


def pt(k: float, e: float) -> str:
    return f"{k:.1f}, {e:.2f}"


def table(header: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def share(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "–"


def setup_bar_counts(ctx: Context) -> dict[tuple[float, float], Counter[tuple[str, str]]]:
    """Every setup-window bar of every session-date classified per point, ignoring the spend
    (shared by both variants): (session, class) -> bars."""
    out: dict[tuple[float, float], Counter[tuple[str, str]]] = {}
    for k, e in POINTS:
        c: Counter[tuple[str, str]] = Counter()
        for cal, day in zip(ctx.cals, ctx.days, strict=True):
            for cand in day.candidates:
                kind, side = classify(cand, k, e, Counter())
                label = f"{kind}_{side}" if side else kind
                for s in (cal.session, "all"):
                    c[(s, label)] += 1
                    if kind == "setup":
                        gap = (cand.twv - cand.x) if side == "long" else (cand.y - cand.twv)
                        need = "ge" if round(gap / POINT, 6) >= NECESSARY_POINTS else "lt"
                        c[(s, f"setup_twv_gap_{need}_975")] += 1
        out[(k, e)] = c
    return out


def boundary_counts(outs: list[Outcome]) -> list[int]:
    """Geometry-eligible signals strictly before each fold-1..4 start, then over the period."""
    starts = [datetime.combine(a, time(0), UTC) for _, a, _ in FOLDS[1:]]
    sig = [o.spend for o in outs if o.status == "signal"]
    return [sum(t < s for t in sig) for s in starts] + [len(sig)]  # type: ignore[operator]


def count_rows(ctx: Context) -> list[list[object]]:
    """Every count as (variant, k_dev, er_max, scope, slice, metric, count), summed per key, in
    the deterministic order keys first appear."""
    acc: dict[tuple[object, ...], int] = {}

    def add(v: str, k: object, e: object, scope: str, sl: str, metric: str, n: int) -> None:
        key = (v, k, e, scope, sl, metric)
        acc[key] = acc.get(key, 0) + int(n)

    for tf, ds in ctx.selected.items():
        add("", "", "", "inputs", tf.value, "files", len(ds))
        add("", "", "", "inputs", tf.value, "rows_in_files", ctx.rows_total[tf])
        add("", "", "", "inputs", tf.value, "rows_excluded_open_time_ge_cut", ctx.excluded[tf])
    add("", "", "", "inputs", "M5", "null_tick_count", ctx.null_ticks)
    add("", "", "", "dates", "all", "weekdays", ctx.weekdays)
    add("", "", "", "dates", "all", "market_trading_dates", len(ctx.trading))
    add("", "", "", "dates", "all", "session_dates", len(ctx.cals))
    add("", "", "", "dates", "all", "missing_session_windows", len(ctx.missing_sessions))
    for cal, day in zip(ctx.cals, ctx.days, strict=True):
        complete = day.missing5 == 0 and day.missing15 == 0
        for sl in ("all", cal.session, f"fold:{fold_of(cal.day)}"):
            add("", "", "", "read_set", sl, "complete" if complete else "incomplete", 1)
            add("", "", "", "read_set", sl, "missing_m5_slots", day.missing5)
            add("", "", "", "read_set", sl, "missing_m15_slots", day.missing15)
            add("", "", "", "read_set", sl, "zero_weight_m5_bars", len(day.zero_weight))
        for v, step in VARIANTS.items():
            sl = f"{cal.session}|{cal.regime}"
            add(v, "", "", "calendar", sl, "session_dates", 1)
            add(v, "", "", "calendar", sl, "decision_instants", len(cal.decision[step]))
            add(v, "", "", "calendar", sl, "blackout_closes", len(cal.blackout[step]))
            add(v, "", "", "calendar", sl, "off_session_closes", cal.off_session)
            add(v, "", "", "calendar", sl, "setup_closes", len(cal.setup_closes))
            add(v, "", "", "calendar", sl, "setup_bars_touching_blackout", len(cal.setup_blackout))
            add(v, "", "", "calendar", sl, "hold_bound_violations", int(not cal.hold_ok))
            add(v, "", "", "calendar", sl, "clipped_session_end", int(cal.clipped))
            add(v, "", "", "calendar", sl, "matches_spec_table_6_1", int(matches_table_61(cal)))
    for (k, e), c in setup_bar_counts(ctx).items():
        for (s, label), n in sorted(c.items()):
            add("both", k, e, "setup_bars", s, label, n)
    for (v, k, e), outs in ctx.results.items():
        for cal, o in zip(ctx.cals, outs, strict=True):
            slices = [
                "all",
                f"fold:{fold_of(cal.day)}",
                f"session:{cal.session}",
                f"year:{cal.day.year}",
            ]
            if o.setup:
                slices.append(f"side:{o.setup[1]}")
            for sl in slices:
                add(v, k, e, "point", sl, "session_dates", 1)
                add(v, k, e, "point", sl, f"status_{o.status}", 1)
                # section 6.5 counters, by their spec names
                for side, n in o.setups.items():
                    add(v, k, e, "point", sl, f"setups_{side}", n)
                for fate, n in o.fates.items():
                    add(v, k, e, "point", sl, f"setups_{fate}", n)
                for metric, name in COUNTERS:
                    add(v, k, e, "point", sl, metric, getattr(o, name))
                if o.status in SPENT[1:]:  # ruling R1: only the first failure's counter
                    add(v, k, e, "point", sl, o.status, 1)
                for name in NONNORMATIVE:
                    add(v, k, e, "point", sl, name, getattr(o, name))
                if o.spend:
                    add(v, k, e, "point", sl, "spends", 1)
                    # ruling R1 combination flags (not section 6.5 counters)
                    for flag in ("min", "cap", "rr"):
                        add(
                            v,
                            k,
                            e,
                            "point",
                            sl,
                            f"would_fail_{flag}",
                            getattr(o, f"would_fail_{flag}"),
                        )
                    add(v, k, e, "point", sl, would_fail_combination(o), 1)
                    gap = "ge" if o.twv_gap >= NECESSARY_POINTS else "lt"  # type: ignore[operator]
                    add(v, k, e, "point", sl, f"spend_twv_gap_{gap}_975", 1)
                for name, n in o.near.items():
                    add(v, k, e, "point", sl, f"near_boundary_{name}", n)
        for (_, a, _), n in zip(FOLDS[1:], boundary_counts(outs), strict=False):
            add(v, k, e, "selection", f"before {a}", "prior_signals", n)
        add(v, k, e, "selection", "whole period", "signals", boundary_counts(outs)[-1])
    for v in VARIANTS:
        for e in ER_MAX:
            for k1, k2 in ((1.0, 1.5), (1.5, 2.0), (1.0, 2.0)):
                for name, n in kdev_pair(ctx, v, e, k1, k2).items():
                    add(v, f"{k1}-{k2}", e, "k_dev", "all", name, n)
    for k, e in POINTS:
        for name, n in overlap(ctx, k, e).items():
            add("m15-m5", k, e, "overlap", "all", name, n)
    for name, n in sorted(ctx.checks.items()):
        add("", "", "", "checks", "all", name, n)
    return [[*key, n] for key, n in acc.items()]


def would_fail_combination(o: Outcome) -> str:
    """Ruling R1: the spend's three geometry checks as one flag combination."""
    return (
        f"would_fail_combination_min{int(o.would_fail_min)}"
        f"_cap{int(o.would_fail_cap)}_rr{int(o.would_fail_rr)}"
    )


def kdev_pair(ctx: Context, v: str, e: float, k1: float, k2: float) -> Counter[str]:
    c: Counter[str] = Counter()
    for o1, o2 in zip(ctx.results[(v, k1, e)], ctx.results[(v, k2, e)], strict=True):
        s1, s2 = o1.spend is not None, o2.spend is not None
        if s1 and s2:
            c[
                "spent_both_same_setup" if o1.setup == o2.setup else "spent_both_different_setup"
            ] += 1
        elif s1 or s2:
            c["spent_only_lower_k" if s1 else "spent_only_higher_k"] += 1
        g1, g2 = o1.status == "signal", o2.status == "signal"
        if g1 and g2:
            c[
                "signal_both_same_setup" if o1.setup == o2.setup else "signal_both_different_setup"
            ] += 1
        elif g2:
            c["signal_only_higher_k"] += 1
            if s1 and o1.spend < o2.spend:  # type: ignore[operator]
                c["signal_only_higher_k_lower_k_spent_earlier_geometry_fail"] += 1
            elif not s1:
                c["signal_only_higher_k_lower_k_not_spent"] += 1
            else:
                c["signal_only_higher_k_other"] += 1
        elif g1:
            c["signal_only_lower_k"] += 1
            if not s2:
                c["signal_only_lower_k_higher_k_not_spent"] += 1
            elif o2.setup == o1.setup:
                c["signal_only_lower_k_same_setup"] += 1
            else:
                c["signal_only_lower_k_higher_k_other_setup"] += 1
    return c


def overlap(ctx: Context, k: float, e: float) -> Counter[str]:
    c: Counter[str] = Counter()
    sig_dates: dict[str, set[date]] = {"m15": set(), "m5": set()}
    for cal, a, b in zip(
        ctx.cals, ctx.results[("m15", k, e)], ctx.results[("m5", k, e)], strict=True
    ):
        if a.spend and b.spend:
            c["spent_both"] += 1
            if a.setup == b.setup:
                c["spent_both_same_setup"] += 1
                c["spent_both_same_setup_m5_earlier"] += b.spend < a.spend
            if a.status == "signal" and b.status == "signal":
                c["signal_both"] += 1
                c["signal_both_same_setup"] += a.setup == b.setup
        elif a.spend:
            c["spent_only_m15"] += 1
        elif b.spend:
            c["spent_only_m5"] += 1
        if a.status == "signal" and b.status != "signal":
            c["signal_only_m15"] += 1
        if b.status == "signal" and a.status != "signal":
            c["signal_only_m5"] += 1
        for v, o in (("m15", a), ("m5", b)):
            if o.status == "signal":
                sig_dates[v].add(cal.day)
    c["signal_dates_m15"] = len(sig_dates["m15"])
    c["signal_dates_m5"] = len(sig_dates["m5"])
    c["signal_dates_both"] = len(sig_dates["m15"] & sig_dates["m5"])
    c["signal_dates_either"] = len(sig_dates["m15"] | sig_dates["m5"])
    return c


def render(ctx: Context) -> str:
    cfg = ctx.cfg
    tfs = list(ctx.selected)
    out = [
        "# vwap_reversion 0.1.0 draft revision 4: B1 feasibility counts (2026-10-11)",
        "",
        "Generated read-only by `scripts/vwap_reversion_feasibility.py` under the owner's B1",
        "authorisation and rulings R1–R6 (spec section 18c), from the clean methodology commit",
        f"`{ctx.commit}` (spec blob `{SPEC_BLOB}`, both checked before any market data was read).",
        f"Machine-readable counts: `{STEM}.csv` (long format: variant, k_dev, er_max, scope, slice,",
        "metric, count).",
        "",
        "- **Descriptive counts only.** No fill, exit, return, P&L or executable price is computed;",
        "  no tick and no ask is read. No trial, attempt or ledger row; no symbol snapshot; no sealed",
        "  file; no strategy implementation; no rule or parameter changed.",
        "- **Data-informed from here on.** Any later choice about this family is made knowing",
        "  these counts and must be listed as data-informed (spec section 18b, B1).",
        "- **Geometry-eligible signals, never trades.** `signal` means the spend passed sections 8,",
        "  8a and 9 on bid bars. The risk engine, the broker and the quotes (section 13.2) are not",
        "  applied, so each count is an upper bound on what a backtest could take.",
        "",
        "## 1. Inputs and reproducibility",
        "",
        f"- Evaluated period {FIRST} … {LAST}: {ctx.weekdays} weekdays, {len(ctx.trading)} market trading dates",
        f"  (`MarketHours.trading_dates`, `configs/data.yaml`), {len(ctx.cals)} session-dates",
        "  (`TradingCalendar` windows `london_morning`, `ldn_ny_overlap`); session windows missing on a",
        f"  trading date: {len(ctx.missing_sessions)}.",
        f"- Sealed period (`configs/backtest.yaml`) starts {cfg.backtest.sealed_period.first}. Files were selected by",  # type: ignore[attr-defined]
        f"  name (UTC date ≤ {LAST}) before any was opened; no partition dated later was opened.",
        "- Warm-up exactly by bar count (spec section 3.3): H1 13, M15 100, M5 none; the fewest whole",
        f"  UTC days before {FIRST} holding that many bars (Parquet row counts), no calendar-day buffer.",
        "  ATR(Ts) uses exactly the 100 newest M15 bars and ER(Ts) exactly the 13 newest H1 closes.",
    ]
    for tf in tfs:
        ps = ctx.paths[tf]
        digest = files_digest({p: ctx.hashes[p] for p in ps})
        out.append(
            f"- Canonical {tf.value}: {len(ps)} files, {ps[0].stem} … {ps[-1].stem}, {ctx.rows_total[tf]:,} rows;"
            f" digest of (name, sha256) `{digest}`."
        )
    out += [
        f"- Ruling R6: every file was scanned with the predicate `open_time < {CUT:%Y-%m-%dT%H:%M:%SZ}`",
        f"  (the market open of trading date {LAST + timedelta(days=1)}, checked against `configs/data.yaml`).",
        "  Rows excluded, counted only (their values were not collected): "
        + ", ".join(f"{tf.value} {ctx.excluded[tf]}" for tf in tfs)
        + ". No returned frame holds a row at or after the cut (asserted).",
        f"- Null `tick_count` in M5 bars read: {ctx.null_ticks}. Input files re-hashed after the run: unchanged.",
        f"- Provisional recorded inputs (not a snapshot verification; B2 verifies both): point {POINT}",
        f"  and `trade_stops_level` {STOPS_LEVEL_POINTS}, copied from the frozen `docs/strategies/opening_range_breakout.md`",
        f"  (sections 8a and 8); minimum `max(500, {STOPS_LEVEL_POINTS} + 10)` = {MIN_POINTS} points, the same for any",
        f"  stops level ≤ 490; cap {CAP_POINTS}; buffer {BUFFER_POINTS}; ATR({ATR_PERIOD}) over {ATR_BARS} M15 bars; ER over",
        f"  {ER_BARS + 1} H1 closes, path summed with `math.fsum`; expiry 30 min; decision instants `A + 60 < T ≤ E − 15`.",
        f"- Official classifications use exact float64 comparisons; comparisons within {NEAR:g} of their",
        "  threshold are reported separately (section 6) and never change a classification.",
        f"- Synthetic tests run before any market data was read: {ctx.synthetic} cases passed.",
        f"- Interpreter: Python {platform.python_version()}, `{Path(sys.executable).name}`, site disabled (`-S`):"
        f" {bool(sys.flags.no_site)}.",
        "- Command: `uv run --no-sync python scripts/vwap_reversion_feasibility.py`. The launch",
        "  actually used, the byte-for-byte rerun check and both output files' sha256 are recorded in",
        f"  `{STEM.replace('_feasibility', '_run')}.md` and `docs/STATUS.md` (queue A4), not here, because",
        "  this file cannot know them when it is written.",
        "",
    ]
    out += render_calendar(ctx)
    out += render_read_set(ctx)
    out += render_setup_bars(ctx)
    out += render_points(ctx)
    out += render_folds(ctx)
    out += render_selection(ctx)
    out += render_kdev(ctx)
    out += render_overlap(ctx)
    out += render_checks(ctx)
    return "\n".join(out) + "\n"


def render_calendar(ctx: Context) -> list[str]:
    out = [
        "## 2. Calendar: decision instants, blackouts and hold bounds (spec 6.1, 10.1)",
        "",
        "From the calendar and `NewsBlackout` only (no prices). London wall clock. `removed` = closes",
        "of the variant's timeframe in the session that fall in a news window; `excluded setups` =",
        "setup closes whose bar touches a window (rule 6).",
        "",
    ]
    rows = []
    groups: dict[tuple[str, str, str], list[Cal]] = {}
    for cal in ctx.cals:
        for v in VARIANTS:
            groups.setdefault((cal.session, cal.regime, v), []).append(cal)
    for (s, regime, v), cals in sorted(groups.items()):
        step = VARIANTS[v]
        shapes: Counter[tuple[str, str, int, str, str]] = Counter()
        for cal in cals:
            dec = cal.decision[step]
            lt = [t.astimezone(LONDON) for t in dec]
            removed = ", ".join(
                f"{t.astimezone(LONDON):%H:%M}"
                for t in sorted(cal.blackout[step])
                if t > cal.a + SETUP_FROM
            )
            excl = ", ".join(f"{t.astimezone(LONDON):%H:%M}" for t in sorted(cal.setup_blackout))
            shapes[
                (
                    f"{lt[0]:%H:%M}" if lt else "–",
                    f"{lt[-1]:%H:%M}" if lt else "–",
                    len(dec),
                    removed or "none",
                    excl or "none",
                )
            ] += 1
        for (f0, l0, n, removed, excl), days in sorted(shapes.items()):
            rows.append([s, regime, v, f0, l0, n, removed, excl, days])
    out += [
        table(
            [
                "session",
                "week",
                "variant",
                "first",
                "last",
                "instants",
                "removed (blackout)",
                "excluded setups",
                "session-dates",
            ],
            rows,
        ),
        "",
    ]
    hold = sum(not c.hold_ok for c in ctx.cals)
    clipped = sum(c.clipped for c in ctx.cals)
    off = sum(c.off_session for c in ctx.cals)
    bad = [c for c in ctx.cals if not matches_table_61(c)]
    out += [
        f"- Session-dates matching spec table 6.1 exactly (instants, removals, rule-6 exclusions):"
        f" {len(ctx.cals) - len(bad)} of {len(ctx.cals)}"
        + (": deviating " + ", ".join(f"{c.day} {c.session}" for c in bad) if bad else "")
        + ".",
        f"- Hold-bound check `E − 15 + 90 min ≤ 12:15 / 17:45 London` (section 10.1): {hold} violations.",
        f"- Session ends clipped by an early close: {clipped}. Closes in `(A, E − 15]` whose calendar state",
        f"  is not the session with entries allowed: {off}.",
        "",
    ]
    return out


def render_read_set(ctx: Context) -> list[str]:
    out = [
        "## 3. Read-set completeness and data quality",
        "",
        "A session-date's read set is complete when every M5 and M15 slot with `open_time ≥ A` and",
        "`close_time ≤ E − 15` has a bar (the largest read set of trigger rule 6).",
        "",
    ]
    rows = []
    for s in (*SESSIONS, "all"):
        sel = [(c, d) for c, d in zip(ctx.cals, ctx.days, strict=True) if s in ("all", c.session)]
        inc = [(c, d) for c, d in sel if d.missing5 or d.missing15]
        rows.append(
            [
                s,
                len(sel),
                len(sel) - len(inc),
                len(inc),
                sum(d.missing5 for _, d in sel),
                sum(d.missing15 for _, d in sel),
                sum(len(d.zero_weight) for _, d in sel),
            ]
        )
    out += [
        table(
            [
                "session",
                "session-dates",
                "complete",
                "incomplete",
                "missing M5 slots",
                "missing M15 slots",
                "M5 bars with tick_count 0",
            ],
            rows,
        ),
        "",
    ]
    inc = sorted(
        (c.day, c.session)
        for c, d in zip(ctx.cals, ctx.days, strict=True)
        if d.missing5 or d.missing15
    )
    if inc:
        out += ["Incomplete session-dates: " + ", ".join(f"{d} {s}" for d, s in inc) + ".", ""]
    no_atr = sum(1 for d in ctx.days for c in d.candidates if math.isnan(c.atr))
    no_er = sum(1 for d in ctx.days for c in d.candidates if c.er is None)
    out += [
        f"- Setup-window bars without ATR(Ts): {no_atr}; without ER(Ts) (fewer than 13 H1 bars or a zero path): {no_er}.",
        "",
    ]
    return out


def render_setup_bars(ctx: Context) -> list[str]:
    counts = setup_bar_counts(ctx)
    out = [
        "## 4. Setup-window bars (shared by both variants, ignoring the spend)",
        "",
        "Every M15 bar with `A + 60 ≤ Ts ≤ E − 45`, classified per point by section 5 and ruling R5",
        "(mutually exclusive; ambiguous regardless of the regime; regime off includes ER",
        "unavailable). `ineligible` = rule 5 (read set, TWV, ATR) or rule 6 (blackout) fails;",
        "`no side` = neither side's rules 3–4. Setups with `TWV(Ts) − X` (short: `Y − TWV(Ts)`)",
        "below 9.75 USD cannot meet section 13.1 unless the mean moves away from the extreme by T.",
        "",
    ]
    rows = []
    for k, e in POINTS:
        c = counts[(k, e)]
        al = {label: n for (s, label), n in c.items() if s == "all"}
        setups = al.get("setup_long", 0) + al.get("setup_short", 0)
        ge = al.get("setup_twv_gap_ge_975", 0)
        per = [c[(s, "setup_long")] + c[(s, "setup_short")] for s in SESSIONS]
        rows.append(
            [
                pt(k, e),
                sum(n for label, n in al.items() if not label.startswith("setup_twv")),
                al.get("ineligible", 0),
                al.get("no_side", 0),
                al.get("ambiguous", 0),
                al.get("regime_off_long", 0) + al.get("regime_off_short", 0),
                f"{al.get('setup_long', 0)} / {al.get('setup_short', 0)}",
                f"{per[0]} / {per[1]}",
                f"{ge} ({share(ge, setups)})",
            ]
        )
    out += [
        table(
            [
                "k_dev, er_max",
                "bars",
                "ineligible",
                "no side",
                "ambiguous",
                "regime off",
                "setups long / short",
                "setups london / overlap",
                "setups with gap ≥ 9.75",
            ],
            rows,
        ),
        "",
    ]
    cands = [c for d in ctx.days for c in d.candidates]
    out += [
        f"Ineligible bars: read set incomplete {sum(not c.read_ok for c in cands)}, touching a news window"
        f" {sum(c.blackout for c in cands)}, both {sum(c.blackout and not c.read_ok for c in cands)}.",
        "",
    ]
    return out


def render_points(ctx: Context) -> list[str]:
    out = [
        "## 5. Setup life cycle per point (spec 6.5 counters, while the session-date is unspent)",
        "",
        "Each counted setup has one terminal outcome (rulings R2–R4): triggered (= spent), broken",
        "(`setups_broken`), replaced (`setups_replaced`: an unbroken active setup displaced by a",
        "newer setup) or expired (`setups_expired`). At one instant breaking precedes replacement,",
        "and at `Ts + 30` the old setup's last trigger and break are evaluated first. The three",
        "*non-normative* columns are not section 6.5 counters: a newer setup arriving while the old",
        "one was already broken (`same T`: broken at that instant) or at exactly `Ts + 30` (the old",
        "one counts expired); adding them to `setups_replaced` gives the alternative “both” counts.",
        "",
    ]
    rows = []
    names = (
        *NONNORMATIVE,
        *(name for _, name in COUNTERS),
    )
    for (v, k, e), outs in ctx.results.items():
        setups, fates = Counter(), Counter()
        for o in outs:
            setups.update(o.setups)
            fates.update(o.fates)
        rows.append(
            [
                v,
                pt(k, e),
                f"{setups['long']} / {setups['short']}",
                *(fates[x] for x in FATES),
                *(sum(getattr(o, name) for o in outs) for name in names),
            ]
        )
    out += [
        table(
            [
                "variant",
                "k_dev, er_max",
                "setups long / short",
                *FATES,
                "non-normative: after break",
                "same T",
                "at expiry",
                *(f"`{metric}`" for metric, _ in COUNTERS),
            ],
            rows,
        ),
        "",
        "## 6. Spend, geometry and session-date statuses",
        "",
        "Statuses with the registered precedence (section 6.5). Ruling R1: at a spend the checks",
        "are evaluated in the order minimum, cap, reward:risk; the first failure is the sole status",
        "and increments only its official counter (`stop_below_minimum`, `stop_above_cap`,",
        "`reward_risk_below_min` = the status columns). The second table shows the `would_fail_*`",
        "combination flags (not section 6.5 counters), so other aggregations stay possible.",
        "",
    ]
    rows = []
    for (v, k, e), outs in ctx.results.items():
        st = Counter(o.status for o in outs)
        rows.append([v, pt(k, e), len(outs), *(st[s] for s in STATUSES)])
    out += [table(["variant", "k_dev, er_max", "session-dates", *STATUSES], rows), ""]
    rows = []
    for (v, k, e), outs in ctx.results.items():
        g = Counter(
            would_fail_combination(o).removeprefix("would_fail_combination_")
            for o in outs
            if o.spend
        )
        spends = sum(g.values())
        nec = sum(1 for o in outs if o.spend and o.twv_gap >= NECESSARY_POINTS)  # type: ignore[operator]
        rows.append(
            [
                v,
                pt(k, e),
                spends,
                f"{g['min1_cap0_rr0']} / {g['min1_cap0_rr1']}",
                f"{g['min0_cap1_rr0']} / {g['min0_cap1_rr1']}",
                g["min0_cap0_rr1"],
                g["min0_cap0_rr0"],
                f"{nec} ({share(nec, spends)})",
                share(g["min0_cap0_rr0"], spends),
            ]
        )
    out += [
        table(
            [
                "variant",
                "k_dev, er_max",
                "spends",
                "d_S < 500: rr ok / rr would fail",
                "d_S > 2,000: rr ok / rr would fail",
                "in bounds, rr fail",
                "signals",
                "spends with TWV(T) gap ≥ 9.75",
                "signals / spends",
            ],
            rows,
        ),
        "",
        "Funnel per session-date (R1 order): session-dates → at least one setup counted → spent →",
        "passes the minimum → passes the cap → passes reward:risk (= geometry-eligible signal).",
        "",
    ]
    rows = []
    for (v, k, e), outs in ctx.results.items():
        n = len(outs)
        with_setup = sum(1 for o in outs if o.counted)
        spent = sum(1 for o in outs if o.spend)
        p_min = sum(1 for o in outs if o.spend and not o.would_fail_min)
        p_cap = sum(1 for o in outs if o.spend and not o.would_fail_min and not o.would_fail_cap)
        sig = sum(1 for o in outs if o.status == "signal")
        rows.append([v, pt(k, e), n, with_setup, spent, p_min, p_cap, sig, share(sig, n)])
    out += [
        table(
            [
                "variant",
                "k_dev, er_max",
                "session-dates",
                "≥ 1 setup",
                "spent",
                "≥ minimum",
                "≤ cap",
                "signals",
                "signals / session-dates",
            ],
            rows,
        ),
        "",
    ]
    near = Counter()
    for outs in ctx.results.values():
        for o in outs:
            near.update(o.near)
    out += [
        f"Float comparisons within {NEAR:g} of their threshold (deviation, new extreme, ER, break, rejection, room to the mean), summed over all points and variants; reported only, never used to change a classification: "
        + (", ".join(f"{k} {n}" for k, n in sorted(near.items())) or "none")
        + ".",
        "",
    ]
    return out


def render_folds(ctx: Context) -> list[str]:
    out = [
        "## 7. Counts by fold, session and side",
        "",
        "Signals (geometry-eligible) by fold of the decision date (UTC; equal to the London date for",
        "every session instant), and over folds 1–4 by session and side. Gates 1, 8 and 9 need",
        "≥ 30 closed trades per OOS fold, per side and per session, and ≥ 200 in total; signals",
        "are an upper bound on trades.",
        "",
    ]
    rows = []
    for (v, k, e), outs in ctx.results.items():
        by = Counter(
            fold_of(c.day) for c, o in zip(ctx.cals, outs, strict=True) if o.status == "signal"
        )
        spends = Counter(fold_of(c.day) for c, o in zip(ctx.cals, outs, strict=True) if o.spend)
        oos = [
            (c, o)
            for c, o in zip(ctx.cals, outs, strict=True)
            if o.status == "signal" and fold_of(c.day) != "training"
        ]
        sides = Counter(o.setup[1] for _, o in oos)  # type: ignore[index]
        sess = Counter(c.session for c, _ in oos)
        rows.append(
            [
                v,
                pt(k, e),
                *(f"{by[f]} / {spends[f]}" for f, _, _ in FOLDS),
                len(oos),
                f"{sides['long']} / {sides['short']}",
                f"{sess['london_morning']} / {sess['ldn_ny_overlap']}",
            ]
        )
    out += [
        table(
            [
                "variant",
                "k_dev, er_max",
                *(f"{f}: signals / spends" for f, _, _ in FOLDS),
                "folds 1–4 signals",
                "long / short",
                "london / overlap",
            ],
            rows,
        ),
        "",
    ]
    years = sorted({c.day.year for c in ctx.cals})
    rows = []
    for (v, k, e), outs in ctx.results.items():
        by = Counter(
            c.day.year for c, o in zip(ctx.cals, outs, strict=True) if o.status == "signal"
        )
        rows.append([v, pt(k, e), *(by[y] for y in years)])
    out += [
        "Signals by calendar year (2020 and 2025 are partial):",
        "",
        table(["variant", "k_dev, er_max", *map(str, years)], rows),
        "",
    ]
    return out


def render_selection(ctx: Context) -> list[str]:
    out = [
        "## 8. Selection boundaries: ≥ 100 strictly prior geometry-eligible signals",
        "",
        "Section 16: fold k selects among points with ≥ 100 development trades entered before the",
        "fold starts (00:00 UTC); the frozen configuration needs ≥ 100 over the whole period.",
        "Signals strictly before each boundary are an upper bound on those trades: below 100 means",
        "the point cannot be selected there; 100 or more does not mean it can. ✓ = at least 100.",
        "",
    ]
    rows = []
    for (v, k, e), outs in ctx.results.items():
        b = boundary_counts(outs)
        rows.append([v, pt(k, e), *(f"{n} {'✓' if n >= 100 else '✗'}" for n in b)])
    out += [
        table(
            ["variant", "k_dev, er_max", *(f"before {a}" for _, a, _ in FOLDS[1:]), "whole period"],
            rows,
        ),
        "",
    ]
    for v in VARIANTS:
        ok = [
            pt(k, e)
            for k, e in POINTS
            if all(n >= 100 for n in boundary_counts(ctx.results[(v, k, e)]))
        ]
        any_fold1 = [
            pt(k, e) for k, e in POINTS if boundary_counts(ctx.results[(v, k, e)])[0] >= 100
        ]
        out.append(
            f"- {v}: points with ≥ 100 at every boundary: {len(ok)} of 6"
            + (f" ({'; '.join(ok)})" if ok else "")
            + f"; points with ≥ 100 before fold 1: {len(any_fold1)} of 6."
        )
    out.append("")
    return out


def render_kdev(ctx: Context) -> list[str]:
    out = [
        "## 9. k_dev: geometry-eligible counts versus which setup spends",
        "",
        "Per variant and `er_max`, each pair of `k_dev` values compared session-date by session-date.",
        "`same` / `diff` = both spent (or both signalled) on the same / a different setup (Ts, side).",
        "`pre-empted` = a signal only at the higher `k_dev` where the lower one spent earlier on a",
        "setup that failed the geometry.",
        "",
    ]
    rows = []
    for v in VARIANTS:
        for e in ER_MAX:
            for k1, k2 in ((1.0, 1.5), (1.5, 2.0), (1.0, 2.0)):
                c = kdev_pair(ctx, v, e, k1, k2)
                s1 = sum(o.status == "signal" for o in ctx.results[(v, k1, e)])
                s2 = sum(o.status == "signal" for o in ctx.results[(v, k2, e)])
                rows.append(
                    [
                        v,
                        f"{e:.2f}",
                        f"{k1} vs {k2}",
                        f"{s1} / {s2}",
                        f"{c['spent_both_same_setup']} / {c['spent_both_different_setup']}",
                        f"{c['spent_only_lower_k']} / {c['spent_only_higher_k']}",
                        f"{c['signal_both_same_setup']} / {c['signal_both_different_setup']}",
                        c["signal_only_lower_k"],
                        c["signal_only_higher_k"],
                        c["signal_only_higher_k_lower_k_spent_earlier_geometry_fail"],
                    ]
                )
    out += [
        table(
            [
                "variant",
                "er_max",
                "k_dev",
                "signals",
                "spent both: same / diff",
                "spent only lower / higher",
                "signal both: same / diff",
                "signal only lower",
                "signal only higher",
                "of which pre-empted",
            ],
            rows,
        ),
        "",
    ]
    return out


def render_overlap(ctx: Context) -> list[str]:
    out = [
        "## 10. m15 and m5: overlap and dependence (dates and setups, no returns)",
        "",
        "Per point, session-date by session-date. `same setup` = both variants spent the same setup",
        "(Ts, side); `m5 earlier` = of those, m5 spent at an earlier instant.",
        "",
    ]
    rows = []
    for k, e in POINTS:
        c = overlap(ctx, k, e)
        rows.append(
            [
                pt(k, e),
                c["spent_both"],
                c["spent_both_same_setup"],
                c["spent_both_same_setup_m5_earlier"],
                f"{c['spent_only_m15']} / {c['spent_only_m5']}",
                f"{c['signal_both']} ({c['signal_both_same_setup']})",
                f"{c['signal_only_m15']} / {c['signal_only_m5']}",
                f"{c['signal_dates_both']} / {c['signal_dates_either']} ({share(c['signal_dates_both'], c['signal_dates_either'])})",
            ]
        )
    out += [
        table(
            [
                "k_dev, er_max",
                "spent both",
                "same setup",
                "m5 earlier",
                "spent only m15 / m5",
                "signal both (same setup)",
                "signal only m15 / m5",
                "dates with a signal: both / either",
            ],
            rows,
        ),
        "",
    ]
    return out


def render_checks(ctx: Context) -> list[str]:
    out = [
        "## 11. Reconciliation and invariants (all asserted by the script; a failure stops it)",
        "",
        "- Statuses: exactly one per session-date; spent statuses = spends; per point, the fold,",
        "  session and year splits sum to the totals (checked below for every point and variant).",
        "- Terminal outcomes: per session-date, triggered + broken + replaced + expired = counted setups, and",
        "  triggered = spends.",
        "- No spend at a blackout instant; no spend at or after the first missing slot; every",
        "  signal has `TWV(T) − X ≥ 9.75 USD` (section 13.1, necessary).",
        "- Look-ahead guard: every bar read names its instant and is refused if it closes later.",
        "- No mutation: every input file re-hashed after the run, unchanged. Modules never imported:",
        "  " + ", ".join(f"`{m}`" for m in FORBIDDEN_MODULES) + ".",
        "",
    ]
    for name, n in sorted(ctx.checks.items()):
        out.append(f"- {name}: {n:,} (variant, point, session-date) cases, all equal.")
    out.append("")
    for outs in ctx.results.values():
        st = Counter(o.status for o in outs)
        assert sum(st.values()) == len(outs) == len(ctx.cals)
        assert sum(st[s] for s in SPENT) == sum(1 for o in outs if o.spend)
        assert sum(o.fates["triggered"] for o in outs) == sum(1 for o in outs if o.spend)
        for key in (lambda c: fold_of(c.day), lambda c: c.session, lambda c: c.day.year):
            split = Counter()
            for c, o in zip(ctx.cals, outs, strict=True):
                split[(key(c), o.status)] += 1
            for s in STATUSES:
                assert sum(n for (_, x), n in split.items() if x == s) == st[s]
        sides = Counter(o.setup[1] for o in outs if o.status == "signal")  # type: ignore[index]
        assert sum(sides.values()) == st["signal"]
    out += [
        f"Reconciled for all {len(ctx.results)} (variant, point) pairs over {len(ctx.cals):,} session-dates.",
        "",
    ]
    return out


if __name__ == "__main__":
    sys.exit(main())
