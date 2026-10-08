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


@pytest.mark.parametrize("mode", [Mode.BACKTEST, Mode.SHADOW])
@pytest.mark.parametrize("account", list(AccountTradeMode))
def test_backtest_and_shadow_never_send_orders(mode: Mode, account: AccountTradeMode) -> None:
    assert not permitted(account, mode=mode)


@pytest.mark.parametrize("account", [AccountTradeMode.REAL, AccountTradeMode.CONTEST])
def test_non_demo_account_blocked_with_default_settings(account: AccountTradeMode) -> None:
    with pytest.raises(LiveTradingBlockedError, match="not DEMO"):
        assert_order_permitted(
            account_trade_mode=account,
            mode=Mode.DEMO,
            live_ack=None,
            live_enabled=False,
            signed_off_date=None,
            today=TODAY,
        )


def test_real_account_allowed_only_when_every_condition_holds() -> None:
    assert permitted(AccountTradeMode.REAL)


@pytest.mark.parametrize(
    "override",
    [
        {"mode": Mode.DEMO},
        {"live_ack": None},
        {"live_ack": "i accept the risk of real money"},  # case matters
        {"live_ack": LIVE_ACK_PHRASE + " "},
        {"live_enabled": False},
        {"signed_off_date": None},
        {"signed_off_date": date(2026, 10, 9)},  # sign-off in the future
    ],
)
def test_any_single_missing_live_condition_blocks_real_account(override: dict[str, object]) -> None:
    assert not permitted(AccountTradeMode.REAL, **override)


def test_exhaustive_combinations_only_one_unlocks_real_account() -> None:
    unlocked = []
    for mode, ack, enabled, signed in product(
        list(Mode), [None, "wrong", LIVE_ACK_PHRASE], [False, True], [None, date(2026, 1, 1)]
    ):
        if permitted(
            AccountTradeMode.REAL,
            mode=mode,
            live_ack=ack,
            live_enabled=enabled,
            signed_off_date=signed,
        ):
            unlocked.append((mode, ack, enabled, signed))
    assert unlocked == [(Mode.LIVE, LIVE_ACK_PHRASE, True, date(2026, 1, 1))]


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
