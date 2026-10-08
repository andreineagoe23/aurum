"""ClockPort: injectable source of "now"."""

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClockPort(Protocol):
    def now(self) -> datetime:
        """Current instant, timezone-aware UTC."""
        ...
