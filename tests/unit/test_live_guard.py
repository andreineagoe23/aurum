"""MASTER_PROMPT §4 rule 1: live trading is physically blocked by default."""

from datetime import date
from itertools import product

import pytest

from aurum.core.enums import AccountTradeMode, Mode
from aurum.core.errors import LiveTradingBlockedError
from aurum.core.safety import LIVE_ACK_PHRASE, assert_order_permitted, order_permission_violations

TODAY = date(2026, 10, 8)
ALL_LIVE = {
    "mode": Mode.LIVE,
    "live_ack": LIVE_ACK_PHRASE,
    "live_enabled": True,
    "signed_off_date": date(2026, 10, 1),
    "today": TODAY,
}


def permitted(account: AccountTradeMode, **overrides: object) -> bool:
    kwargs = ALL_LIVE | overrides
    return not order_permission_violations(account_trade_mode=account, **kwargs)  # type: ignore[arg-type]


def test_demo_account_in_demo_mode_is_allowed() -> None:
    assert permitted(
        AccountTradeMode.DEMO,
        mode=Mode.DEMO,
        live_ack=None,
        live_enabled=False,
        signed_off_date=None,
    )


def test_backtest_and_shadow_never_send_orders() -> None:
    for mode, account in product([Mode.BACKTEST, Mode.SHADOW], list(AccountTradeMode)):
        assert not permitted(account, mode=mode), (mode, account)


def test_exhaustive_combinations_only_one_unlocks_a_non_demo_account() -> None:
    acks = [
        None,
        "wrong",
        LIVE_ACK_PHRASE.lower(),  # case matters
        LIVE_ACK_PHRASE + " ",  # exact phrase only
        LIVE_ACK_PHRASE,
    ]
    signed = [None, date(2026, 1, 1), date(2026, 10, 9)]  # the last is in the future
    for account in (AccountTradeMode.REAL, AccountTradeMode.CONTEST):
        unlocked = [
            (mode, ack, enabled, signed_off)
            for mode, ack, enabled, signed_off in product(list(Mode), acks, [False, True], signed)
            if permitted(
                account,
                mode=mode,
                live_ack=ack,
                live_enabled=enabled,
                signed_off_date=signed_off,
            )
        ]
        assert unlocked == [(Mode.LIVE, LIVE_ACK_PHRASE, True, date(2026, 1, 1))], account


def test_error_lists_every_violation() -> None:
    with pytest.raises(LiveTradingBlockedError) as err:
        assert_order_permitted(
            account_trade_mode=AccountTradeMode.REAL,
            mode=Mode.DEMO,
            live_ack=None,
            live_enabled=False,
            signed_off_date=None,
            today=TODAY,
        )
    message = str(err.value)
    for fragment in ("not DEMO", "AURUM_MODE", "AURUM_LIVE_ACK", "live_enabled", "signed_off_date"):
        assert fragment in message
