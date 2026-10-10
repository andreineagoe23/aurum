"""Reference strategy and the Phase 3 risk stand-in."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from aurum.backtest.market import HistoricalMarket
from aurum.core.enums import AccountTradeMode, SessionPhase, Side
from aurum.core.models import AccountSnapshot, SessionState
from aurum.core.timeframes import Timeframe
from aurum.risk.base import RiskContext
from aurum.risk.fixed import FixedVolumeRisk
from aurum.settings import AppConfig
from aurum.strategies.reference import FixedTimeReference, ReferenceParams
from aurum.strategies.registry import build_strategy
from tests.factories import signal, tick
from tests.simdata import at, flat_m1, market, spec

PARAMS = ReferenceParams.model_validate(
    {
        "entries": [
            {
                "time_utc": "08:00",
                "side": "BUY",
                "stop_points": 1000,
                "target_points": 1500,
                "max_hold_minutes": 120,
            },
            {"time_utc": "14:30", "side": "SELL", "stop_points": 3000},
        ]
    }
)


def strategy(tf: Timeframe = Timeframe.M5) -> FixedTimeReference:
    return FixedTimeReference(PARAMS, symbol="XAUUSD", point=0.01, decision_timeframe=tf)


def m5_market(last: str = "15:59") -> HistoricalMarket:
    m1 = flat_m1("07:00", last, price=2000.0)
    m5 = [r for r in flat_m1("07:00", last, price=2000.0) if int(r[0][3:]) % 5 == 0]
    m15 = [r for r in m5 if int(r[0][3:]) % 15 == 0]
    return market(m1=m1, higher={Timeframe.M5: m5, Timeframe.M15: m15})


def test_signals_only_at_entry_times_with_fixed_distances() -> None:
    mkt = m5_market()
    s = strategy()
    assert s.on_bar(mkt.store.view_at(at("08:05"))) is None
    sig = s.on_bar(mkt.store.view_at(at("08:00")))
    assert sig is not None
    assert (sig.side, sig.stop_loss, sig.take_profit) == (Side.BUY, 1990.0, 2015.0)
    assert sig.max_hold == timedelta(minutes=120)
    assert sig.data_as_of == at("08:00")
    sell = s.on_bar(mkt.store.view_at(at("14:30")))
    assert sell is not None
    assert (sell.side, sell.stop_loss, sell.take_profit, sell.max_hold) == (
        Side.SELL,
        2030.0,
        None,
        None,
    )


def test_no_signal_when_decision_bar_missing() -> None:
    mkt = m5_market(last="07:54")  # the bar closing at 08:00 does not exist
    assert strategy().on_bar(mkt.store.view_at(at("08:00"))) is None


def test_m15_variant_and_params_validation() -> None:
    mkt = m5_market()
    assert strategy(Timeframe.M15).on_bar(mkt.store.view_at(at("08:00"))) is not None
    with pytest.raises(ValueError, match="M15 bar close"):
        FixedTimeReference(
            ReferenceParams.model_validate(
                {"entries": [{"time_utc": "08:05", "side": "BUY", "stop_points": 10}]}
            ),
            symbol="XAUUSD",
            point=0.01,
            decision_timeframe=Timeframe.M15,
        )
    with pytest.raises(ValidationError, match="unique"):
        ReferenceParams.model_validate(
            {"entries": [{"time_utc": "08:00", "side": "BUY", "stop_points": 1}] * 2}
        )


def test_registry_builds_reference_from_config(app_config: AppConfig) -> None:
    cfg = app_config.strategies["reference_fixed_time"]
    built = build_strategy(cfg, variant="m5", symbol="XAUUSD", point=0.01)
    assert built.strategy_id == "reference_fixed_time"
    assert built.decision_timeframe is Timeframe.M5
    with pytest.raises(ValueError, match="variant"):
        build_strategy(cfg, variant="h1", symbol="XAUUSD", point=0.01)


def ctx(phase: SessionPhase = SessionPhase.OPEN, positions: tuple = ()) -> RiskContext:  # type: ignore[type-arg]
    now = at("08:00")
    return RiskContext(
        now=now,
        session=SessionState(time=now, local_time=now, phase=phase),
        account=AccountSnapshot(
            login=0,
            server="x",
            currency="USD",
            trade_mode=AccountTradeMode.DEMO,
            balance=10_000,
            equity=10_000,
            margin_free=10_000,
            leverage=500,
            time=now,
        ),
        spec=spec(),
        positions=positions,
        orders=(),
        quote=tick(2000.00, 2000.10, now),
    )


def test_fixed_volume_risk_approves_and_prices_risk() -> None:
    d = FixedVolumeRisk(0.10, 2.25).evaluate(signal(Side.BUY, stop_loss=1995.0), ctx())
    assert d.approved
    assert d.volume == 0.10
    assert d.stop_distance_points == pytest.approx(510)  # ask 2000.10 - 1995.00
    assert d.risk_amount == pytest.approx(51.0 + 0.45)


def test_fixed_volume_risk_rejects() -> None:
    for risk, context, stop, reason in [
        (FixedVolumeRisk(0.10), ctx(SessionPhase.NO_NEW_ENTRIES), 1995.0, "no new entries"),
        (FixedVolumeRisk(0.005), ctx(), 1995.0, "not tradable"),
        (FixedVolumeRisk(0.10), ctx(), 2001.0, "not beyond"),  # stop through the market
    ]:
        d = risk.evaluate(signal(Side.BUY, stop_loss=stop), context)
        assert not d.approved
        assert reason in "; ".join(d.reasons), reason
