"""FaultyBroker: wraps any BrokerPort and injects connection failures on demand."""

from collections.abc import Callable
from typing import Any

from aurum.core.errors import BrokerDisconnectedError
from aurum.core.models import ExecutionReport, OrderIntent
from aurum.ports.broker import BrokerPort


class FaultyBroker:
    """Delegates to ``inner``. Faults:

    * ``offline = True``: every call except ``is_connected`` raises ``BrokerDisconnectedError``;
    * ``lose_next_reply``: the next ``send_order`` executes at the broker, then the reply is lost;
    * ``fail_next_send``: the next ``send_order`` fails before reaching the broker.
    """

    def __init__(self, inner: BrokerPort) -> None:
        self.inner = inner
        self.offline = False
        self.lose_next_reply = False
        self.fail_next_send = False
        self.sends = 0
        """send_order calls that reached the inner broker."""

    def is_connected(self) -> bool:
        return not self.offline and self.inner.is_connected()

    def send_order(self, intent: OrderIntent) -> ExecutionReport:
        self._check()
        if self.fail_next_send:
            self.fail_next_send = False
            raise BrokerDisconnectedError("connection dropped before the request was sent")
        self.sends += 1
        report = self.inner.send_order(intent)
        if self.lose_next_reply:
            self.lose_next_reply = False
            raise BrokerDisconnectedError("connection dropped before the reply arrived")
        return report

    def _check(self) -> None:
        if self.offline:
            raise BrokerDisconnectedError("FaultyBroker is offline")

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.inner, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            self._check()
            return attr(*args, **kwargs)

        return call


def as_port(broker: FaultyBroker) -> BrokerPort:
    port: BrokerPort = broker  # type: ignore[assignment]
    return port


Faults = Callable[[FaultyBroker], None]
