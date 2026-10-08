"""Live-trading guard (MASTER_PROMPT §4 rule 1). Pure logic so it is testable everywhere.

Rules, checked before **every** broker write (send/modify/close/cancel):

1. The run mode must be DEMO or LIVE. BACKTEST and SHADOW never touch a real broker.
2. A DEMO account is allowed.
3. Any other account (REAL, CONTEST, unknown) is refused unless *all* of:
   ``AURUM_MODE=LIVE``, ``AURUM_LIVE_ACK`` equals ``LIVE_ACK_PHRASE`` exactly, and
   ``configs/promotion.yaml`` has ``live_enabled: true`` with a sign-off date not in the future.
"""

from datetime import date
from typing import Final

from aurum.core.enums import AccountTradeMode, Mode
from aurum.core.errors import LiveTradingBlockedError

LIVE_ACK_PHRASE: Final = "I accept the risk of real money"
ORDER_SENDING_MODES: Final = frozenset({Mode.DEMO, Mode.LIVE})


def order_permission_violations(
    *,
    account_trade_mode: AccountTradeMode,
    mode: Mode,
    live_ack: str | None,
    live_enabled: bool,
    signed_off_date: date | None,
    today: date,
) -> list[str]:
    """Return the list of violated conditions; empty means the broker write may proceed."""
    if mode not in ORDER_SENDING_MODES:
        return [f"mode {mode} never sends orders (only DEMO or LIVE may)"]
    if account_trade_mode is AccountTradeMode.DEMO:
        return []

    violations = [f"account trade_mode is {account_trade_mode}, not DEMO"]
    if mode is not Mode.LIVE:
        violations.append("AURUM_MODE is not LIVE")
    if live_ack != LIVE_ACK_PHRASE:
        violations.append("AURUM_LIVE_ACK is missing or does not match the required phrase")
    if not live_enabled:
        violations.append("configs/promotion.yaml live_enabled is false")
    if signed_off_date is None:
        violations.append("configs/promotion.yaml has no signed_off_date")
    elif signed_off_date > today:
        violations.append(f"signed_off_date {signed_off_date} is in the future")
    # The account itself is not a violation when every live condition holds.
    return [] if len(violations) == 1 else violations


def assert_order_permitted(
    *,
    account_trade_mode: AccountTradeMode,
    mode: Mode,
    live_ack: str | None,
    live_enabled: bool,
    signed_off_date: date | None,
    today: date,
) -> None:
    """Raise ``LiveTradingBlockedError`` listing every violated condition, else return."""
    violations = order_permission_violations(
        account_trade_mode=account_trade_mode,
        mode=mode,
        live_ack=live_ack,
        live_enabled=live_enabled,
        signed_off_date=signed_off_date,
        today=today,
    )
    if violations:
        raise LiveTradingBlockedError("broker write refused: " + "; ".join(violations))
