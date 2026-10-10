"""SimBroker mechanics on hand-built tapes. P&L: (exit - entry) x side x 100 oz x lots."""

from datetime import timedelta

import pytest

from aurum.backtest.broker import IntrabarRule, SimBroker
from aurum.backtest.market import DataMode
from aurum.core.enums import DealEntry, DealReason, ExecutionStatus, OrderType, Side
from aurum.core.errors import BrokerError, NoMarketDataError, SafetyViolationError
from aurum.core.models import OrderIntent
from aurum.core.timeframes import Timeframe
from aurum.data.market_hours import MarketHours
from aurum.ports.broker import BrokerPort
from aurum.settings import AppConfig
from tests.factories import T0, intent
from tests.simdata import DAY, at, market, sim, spec

TAPE = [
    ("07:59:59.900", 1999.90, 2000.00),
    ("08:00:00.000", 2000.00, 2000.10),
    ("08:00:00.100", 2000.05, 2000.15),
    ("08:00:00.300", 2000.20, 2000.30),
    ("08:00:01.000", 2001.00, 2001.10),
    ("08:00:02.000", 1999.00, 1999.10),
    ("08:00:03.000", 1999.50, 1999.60),
]
BARS = [
    ("08:00", 2000.00, 2000.50, 1999.80, 2000.20, 10),
    ("08:01", 2000.20, 2001.20, 1999.40, 2000.00, 10),
    ("08:02", 1998.00, 1998.50, 1997.00, 1998.20, 10),
    ("08:03", 1998.20, 1998.40, 1998.00, 1998.30, 10),
]


def ticks_sim(**kwargs: object) -> SimBroker:
    broker, clock = sim(market(ticks={DAY: TAPE}), **kwargs)  # type: ignore[arg-type]
    clock.set(at("08:00"))
    return broker


def bars_sim(**kwargs: object) -> SimBroker:
    broker, clock = sim(market(m1=BARS, extra_points=2), **kwargs)  # type: ignore[arg-type]
    clock.set(at("08:00"))
    return broker


def order(
    side: Side = Side.BUY, sl: float = 1999.50, tp: float | None = 2001.00, **kw: object
) -> OrderIntent:
    return intent(side, 0.20, stop_loss=sl, take_profit=tp, **kw)


# ------------------------------------------------------------------------------ TICKS mode


def test_market_buy_fills_at_next_ask_not_before_decision() -> None:
    broker = ticks_sim()
    report = broker.send_order(order())
    assert report.status is ExecutionStatus.FILLED
    # The 07:59:59.900 quote is before the decision; the first quote at/after 08:00 is used.
    assert report.fill_price == 2000.10
    assert report.time == at("08:00")
    assert report.commission == -0.45  # 2.25 x 0.20


def test_latency_and_slippage_move_the_fill() -> None:
    broker = ticks_sim(latency_ms=250, slippage=5)
    report = broker.send_order(order())
    assert report.time == at("08:00:00.300")  # first quote at/after 08:00:00.250
    assert report.fill_price == pytest.approx(2000.35)  # ask 2000.30 + 5 points


def test_take_profit_fills_at_target_price() -> None:
    broker = ticks_sim()
    broker.send_order(order())
    broker.advance_to(at("08:01"))
    (trade,) = broker.trades
    assert trade.exit_reason is DealReason.TAKE_PROFIT
    assert trade.exit_time == at("08:00:01")
    assert trade.exit_price == 2001.00
    assert trade.profit == pytest.approx(18.00)  # (2001.00 - 2000.10) x 100 x 0.2
    assert broker.account_info().balance == pytest.approx(10_000 - 0.45 + 18.00 - 0.45)
    assert broker.positions() == []


def test_stop_gap_fills_at_the_gapped_bid() -> None:
    broker = ticks_sim()
    broker.send_order(order(sl=1999.50, tp=2002.00))
    broker.advance_to(at("08:01"))
    (trade,) = broker.trades
    assert trade.exit_reason is DealReason.STOP_LOSS
    assert trade.exit_price == 1999.00  # bid jumped from 2001.00 to 1999.00, through 1999.50
    assert trade.profit == pytest.approx(-22.00)
    assert trade.intrabar is IntrabarRule.NONE
    assert trade.exit_mode is DataMode.TICKS
    # MFE/MAE on the bid: best 2001.00, worst 1999.00.
    assert (trade.best, trade.worst) == (2001.00, 1999.00)


def test_sell_stop_triggers_on_the_ask() -> None:
    broker = ticks_sim()
    report = broker.send_order(order(Side.SELL, sl=2000.50, tp=1999.20))
    assert report.fill_price == 2000.00  # sells hit the bid
    broker.advance_to(at("08:01"))
    (trade,) = broker.trades
    assert trade.exit_reason is DealReason.STOP_LOSS
    assert trade.exit_price == 2001.10  # first ask >= 2000.50
    assert trade.profit == pytest.approx(-22.00)


def test_close_position_fills_at_next_bid_with_expert_reason() -> None:
    broker = ticks_sim()
    broker.send_order(order(sl=1990.0, tp=2010.0))
    broker._clock.set(at("08:00:01"))  # type: ignore[attr-defined]
    (pos,) = broker.positions()
    report = broker.close_position(pos.position_id)
    assert report.status is ExecutionStatus.CLOSED
    assert report.fill_price == 2001.00
    deals = broker.history_deals(at("07:00"), at("09:00"))
    assert [(d.entry, d.reason) for d in deals] == [
        (DealEntry.IN, DealReason.EXPERT),
        (DealEntry.OUT, DealReason.EXPERT),
    ]
    assert deals[1].commission == -0.45


def test_close_after_stop_already_hit_is_rejected() -> None:
    broker = ticks_sim()
    broker.send_order(order(sl=1999.50, tp=2002.00))
    broker._clock.set(at("08:00:02.500"))  # type: ignore[attr-defined]
    report = broker.close_position(broker.positions()[0].position_id)
    # The stop at 08:00:02 came first: the close finds nothing to close.
    assert report.status is ExecutionStatus.REJECTED
    assert "already closed" in report.message
    assert broker.trades[0].exit_reason is DealReason.STOP_LOSS


def test_no_quote_or_market_closed_is_rejected(app_config: AppConfig) -> None:
    broker = ticks_sim()
    broker._clock.set(at("09:00"))  # type: ignore[attr-defined]
    report = broker.send_order(order())
    assert report.status is ExecutionStatus.REJECTED
    assert "no quote" in report.message
    broker = ticks_sim(market_hours=MarketHours(app_config.data.market))
    broker._clock.set(at("21:30"))  # type: ignore[attr-defined]  # daily break 21:00-22:00 UTC
    report = broker.send_order(order())
    assert report.status is ExecutionStatus.REJECTED
    assert report.message == "market closed"


def test_invalid_volume_and_stops_level_rejected() -> None:
    broker = ticks_sim(symbol_spec=spec(stops_level=50))
    bad_volume = broker.send_order(intent(Side.BUY, 0.005, stop_loss=1999.0, t=T0))
    assert bad_volume.status is ExecutionStatus.REJECTED
    assert "invalid volume" in bad_volume.message
    # Fill quote bid 2000.00: a stop 30 points away is inside the 50-point stops level.
    close_stop = broker.send_order(order(sl=1999.70, tp=None, t=T0 + timedelta(minutes=1)))
    assert close_stop.status is ExecutionStatus.REJECTED
    assert "invalid stops" in close_stop.message
    ok = broker.send_order(order(sl=1999.50, tp=None, t=T0 + timedelta(minutes=2)))
    assert ok.status is ExecutionStatus.FILLED


def test_duplicate_key_returns_the_first_report() -> None:
    broker = ticks_sim()
    first = broker.send_order(order())
    again = broker.send_order(order())
    assert again.duplicate
    assert again.position_id == first.position_id
    assert len(broker.positions()) == 1


def test_unapproved_intent_raises() -> None:
    broker = ticks_sim()
    bad = order().model_copy(
        update={"risk_decision": order().risk_decision.model_copy(update={"approved": False})}
    )
    with pytest.raises(SafetyViolationError):
        broker.send_order(bad)


def test_check_order_margin_and_rejection_hook() -> None:
    broker = ticks_sim()
    ok = broker.check_order(order())
    assert ok.ok
    assert ok.margin_required == pytest.approx(80.004)  # 0.20 x 100 oz x ask 2000.10 / 500
    bad = broker.check_order(intent(Side.BUY, 0.005, stop_loss=1999.0, t=T0))
    assert not bad.ok
    assert any("invalid volume" in r for r in bad.reasons)
    early, clock = sim(market(ticks={DAY: TAPE}))
    clock.set(at("07:00"))
    assert "no quote" in early.check_order(order()).reasons
    poor = ticks_sim(balance=50.0, leverage=100)  # 0.20 lot x 100 oz x 2000 / 100 = 400
    assert "insufficient margin" in poor.send_order(order()).message
    picky = ticks_sim(rejection_hooks=[lambda i, q: "spread" if q.spread > 0.05 else None])
    assert picky.send_order(order()).message == "spread"


def test_buy_stop_entry_fills_at_triggering_ask() -> None:
    broker = ticks_sim()
    placed = broker.send_order(
        order(sl=2000.00, tp=2002.00, entry_type=OrderType.STOP, entry_price=2000.80)
    )
    assert placed.status is ExecutionStatus.PLACED
    assert [o.order_type for o in broker.orders()] == [OrderType.STOP]
    broker.advance_to(at("08:01"))
    (trade,) = broker.trades
    assert trade.entry_type is OrderType.STOP
    assert trade.entry_price == 2001.10  # ask jumped through 2000.80
    assert trade.exit_price == 1999.00  # then the stop gapped too
    assert trade.profit == pytest.approx(-42.00)
    assert broker.orders() == []
    deals = broker.history_deals(at("07:00"), at("09:00"))
    assert [d.magic for d in deals] == [trade.magic, trade.magic]


def test_pending_price_must_respect_side_and_cancel_works() -> None:
    broker = ticks_sim()
    wrong = broker.send_order(
        order(sl=1998.0, tp=None, entry_type=OrderType.STOP, entry_price=1999.0)
    )
    assert "invalid price" in wrong.message  # a buy stop below the ask
    placed = broker.send_order(
        order(
            sl=1998.0,
            tp=None,
            entry_type=OrderType.LIMIT,
            entry_price=1999.0,
            t=T0 + timedelta(minutes=1),
        )
    )
    assert placed.status is ExecutionStatus.PLACED
    assert placed.order_id is not None
    assert broker.cancel_order(placed.order_id).status is ExecutionStatus.CANCELLED
    assert broker.orders() == []
    assert broker.cancel_order(placed.order_id).status is ExecutionStatus.REJECTED


def test_tick_mode_cancel_keeps_the_exact_order() -> None:
    # BUY STOP 2000.25; the 08:00:00.300 tick (ask 2000.30) triggers it.
    for cancel_at, filled in (("08:00:00.250", False), ("08:00:00.500", True)):
        broker, clock = sim(market(ticks={DAY: TAPE}), start="08:00")
        stop = order(sl=1999.00, tp=None, entry_type=OrderType.STOP, entry_price=2000.25)
        placed = broker.send_order(stop)
        assert placed.order_id is not None
        clock.set(at(cancel_at))
        report = broker.cancel_order(placed.order_id)
        # .250 executes on the .300 tick itself: the cancel wins. .500: the trigger came first.
        assert (report.status is ExecutionStatus.REJECTED) is filled, (cancel_at, report)
        assert len(broker.positions()) == filled, cancel_at
        assert ("filled before the cancel arrived" in (report.message or "")) is filled


def test_modify_position_validates_stops_and_freeze_level() -> None:
    broker = ticks_sim()
    broker.send_order(order(sl=1999.00, tp=None))
    pos = broker.positions()[0]
    assert broker.modify_position(pos.position_id, 2000.50, None).status is ExecutionStatus.REJECTED
    ok = broker.modify_position(pos.position_id, 1999.80, 2003.00)
    assert ok.status is ExecutionStatus.MODIFIED
    assert broker.positions()[0].stop_loss == 1999.80
    frozen = ticks_sim(symbol_spec=spec(freeze_level=20))
    frozen.send_order(order(sl=1999.85, tp=None))
    report = frozen.modify_position(frozen.positions()[0].position_id, 1999.00, None)
    assert report.status is ExecutionStatus.REJECTED  # bid 2000.00 is 15 points from SL 1999.85
    assert "freeze" in report.message


# ------------------------------------------------------------------------------ BARS mode


def test_bar_mode_fill_uses_next_open_and_spread_min_plus_extra() -> None:
    broker = bars_sim()
    report = broker.send_order(order())
    assert report.fill_price == pytest.approx(2000.12)  # open 2000.00 + (10 + 2) points


def test_bar_mode_stop_and_target_in_one_bar_is_stop_first_and_flagged() -> None:
    broker = bars_sim()
    broker.send_order(order(sl=1999.50, tp=2001.00))
    broker.advance_to(at("08:05"))
    (trade,) = broker.trades
    assert trade.exit_reason is DealReason.STOP_LOSS
    assert trade.intrabar is IntrabarRule.SL_TP_SAME_BAR
    assert trade.exit_price == 1999.50
    assert trade.exit_time == at("08:01:59.999")
    assert trade.profit == pytest.approx(-12.40)  # (1999.50 - 2000.12) x 20


def test_bar_mode_gap_through_stop_fills_at_open() -> None:
    broker = bars_sim(slippage=5)
    broker.send_order(order(sl=1999.00, tp=2002.00))
    broker.advance_to(at("08:05"))
    (trade,) = broker.trades
    assert trade.entry_price == pytest.approx(2000.17)  # 2000.12 + 5 points
    assert trade.exit_price == pytest.approx(1997.95)  # open 1998.00 - 5 points
    assert trade.exit_time == at("08:02")
    assert trade.intrabar is IntrabarRule.NONE


def test_bar_mode_sell_stop_uses_ask() -> None:
    broker = bars_sim()
    broker.send_order(order(Side.SELL, sl=2000.60, tp=1999.00))
    broker.advance_to(at("08:05"))
    (trade,) = broker.trades
    assert trade.entry_price == 2000.00
    assert trade.exit_price == 2000.60  # ask high 2000.50 + 0.12 >= 2000.60
    assert trade.profit == pytest.approx(-12.00)


def test_bar_mode_stop_entry_and_stop_in_one_bar_is_flagged() -> None:
    broker = bars_sim()
    broker.send_order(order(sl=1999.85, tp=2001.00, entry_type=OrderType.STOP, entry_price=2000.40))
    broker.advance_to(at("08:05"))
    (trade,) = broker.trades
    assert trade.entry_price == 2000.40
    assert trade.exit_price == 1999.85
    assert trade.intrabar is IntrabarRule.ENTRY_BAR
    assert trade.profit == pytest.approx(-11.00)


def test_bar_mode_limit_target_not_credited_in_entry_bar() -> None:
    broker = bars_sim()
    broker.send_order(
        order(sl=1999.00, tp=2000.40, entry_type=OrderType.LIMIT, entry_price=1999.60)
    )
    broker.advance_to(at("08:05"))
    (trade,) = broker.trades
    # Filled in the 08:01 bar; its high reached the target, but possibly before the fill.
    assert trade.entry_time == at("08:01")
    assert trade.exit_reason is DealReason.STOP_LOSS
    assert trade.exit_price == 1998.00  # 08:02 opened below the stop
    assert trade.intrabar is IntrabarRule.ENTRY_BAR
    assert trade.profit == pytest.approx(-32.00)


def test_bar_mode_cancel_inside_a_bar_resolves_the_entry_first() -> None:
    """Cancel arriving 200 ms into a bar (freeze level 2.00, applied only if still pending)."""
    cases = [
        # side, entry, stop, cancel at, report message (None = cancelled), trade's rule
        (Side.BUY, 2002.50, 1999.00, "08:01", None, None),  # 08:01 ask high 2001.32: no touch
        (Side.BUY, 2001.50, 1999.00, "08:01", "frozen", None),  # 1.18 from the ask open
        (Side.SELL, 1999.00, 2000.00, "08:02", "at the bar open", IntrabarRule.NONE),
        (Side.BUY, 2001.00, 1999.50, "08:01", "cancel_bar assumption", IntrabarRule.CANCEL_BAR),
    ]
    for side, entry, sl, cancel_at, message, rule in cases:
        broker, clock = sim(
            market(m1=BARS, extra_points=2),
            start="08:00",
            latency_ms=200,
            symbol_spec=spec(freeze_level=200),
        )
        placed = broker.send_order(
            order(side, sl=sl, tp=None, entry_type=OrderType.STOP, entry_price=entry)
        )
        assert placed.order_id is not None
        clock.set(at(cancel_at))
        report = broker.cancel_order(placed.order_id)
        assert broker.orders() == [] or message == "frozen", entry
        if message is None:
            assert report.status is ExecutionStatus.CANCELLED, (entry, report)
            continue
        assert report.status is ExecutionStatus.REJECTED, entry
        assert message in (report.message or ""), (entry, report.message)
        if rule is None:
            continue
        clock.set(at("08:03"))
        for p in broker.positions():
            broker.close_position(p.position_id)
        (trade,) = broker.trades
        assert trade.intrabar is rule, entry
        if rule is IntrabarRule.CANCEL_BAR:
            # Filled at 2001.00 inside 08:01, whose low 1999.40 also takes the stop: the
            # same-bar stop doesn't replace the cancel_bar flag.
            assert (trade.entry_price, trade.exit_reason) == (2001.00, DealReason.STOP_LOSS)
            assert trade.profit == pytest.approx(-30.00)
        else:
            assert trade.entry_price == 1998.00  # SELL STOP at the 08:02 open bid (gap)


def test_bar_mode_close_and_excursions_from_bar_ranges() -> None:
    broker = bars_sim()
    broker.send_order(order(sl=1990.00, tp=2010.00))
    broker._clock.set(at("08:03"))  # type: ignore[attr-defined]
    (pos,) = broker.positions()
    report = broker.close_position(pos.position_id)
    assert report.fill_price == 1998.20  # open of the 08:03 bar
    (trade,) = broker.trades
    assert trade.exit_reason is DealReason.EXPERT
    # Bid range while open: highs 2000.50, 2001.20, 1998.50; lows down to 1997.00.
    assert (trade.best, trade.worst) == (2001.20, 1997.00)
    assert trade.profit == pytest.approx(-38.40)  # (1998.20 - 2000.12) x 20


def test_bar_mode_close_without_a_bar_waits_and_fails() -> None:
    broker = bars_sim()
    broker.send_order(order(sl=1990.00, tp=2010.00))
    broker._clock.set(at("08:04"))  # type: ignore[attr-defined]  # no 08:04 bar or later
    report = broker.close_position(broker.positions()[0].position_id)
    assert report.status is ExecutionStatus.REJECTED
    assert report.message == "no quote"
    assert len(broker.positions()) == 1


# ------------------------------------------------------------------------------ BrokerPort reads


def test_data_reads_never_reach_past_now() -> None:
    broker, clock = sim(market(ticks={DAY: TAPE}))
    assert isinstance(broker, BrokerPort)
    clock.set(at("07:00"))
    with pytest.raises(NoMarketDataError):  # no quote yet
        broker.latest_tick("XAUUSD")
    clock.set(at("08:00:01"))
    ticks = broker.ticks("XAUUSD", at("07:00"), at("09:00"))
    assert [t.time for t in ticks][-1] == at("08:00:01")  # inclusive of now, nothing later
    assert len(ticks) == 5
    assert broker.latest_tick("XAUUSD").bid == 2001.00
    m1 = broker.bars("XAUUSD", Timeframe.M1, at("07:00"), at("09:00"))
    assert [b.open_time for b in m1] == [at("07:59")]  # the 08:00 bar has not closed
    clock.set(at("08:01"))
    m1 = broker.bars("XAUUSD", Timeframe.M1, at("07:00"), at("09:00"))
    assert [b.open_time for b in m1] == [at("07:59"), at("08:00")]
    assert (m1[1].close, m1[1].spread_points) == (1999.50, 10)
    with pytest.raises(BrokerError):
        broker.latest_tick("EURUSD")


def test_sell_limit_entry_fills_at_its_price_then_target() -> None:
    broker = ticks_sim()
    placed = broker.send_order(
        order(Side.SELL, sl=2002.00, tp=1999.50, entry_type=OrderType.LIMIT, entry_price=2000.90)
    )
    assert placed.status is ExecutionStatus.PLACED
    broker.advance_to(at("08:01"))
    (trade,) = broker.trades
    assert (trade.entry_time, trade.entry_price) == (at("08:00:01"), 2000.90)  # bid 2001.00
    assert (trade.entry_bid, trade.entry_ask) == (2000.90, pytest.approx(2001.00))
    assert (trade.exit_reason, trade.exit_price) == (DealReason.TAKE_PROFIT, 1999.50)
    assert trade.profit == pytest.approx(28.00)  # 1.40 x 100 oz x 0.20


def test_sell_stop_entry_fills_at_triggering_bid_with_slippage() -> None:
    broker = ticks_sim(slippage=5)
    broker.send_order(
        order(Side.SELL, sl=2001.50, tp=1998.00, entry_type=OrderType.STOP, entry_price=1999.50)
    )
    broker.advance_to(at("08:01"))
    (pos,) = broker.positions()
    assert pos.open_price == pytest.approx(1998.95)  # bid 1999.00 at 08:00:02 - 5 points
    assert broker.orders() == []
