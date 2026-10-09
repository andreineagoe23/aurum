"""Kill switch (MASTER_PROMPT §4 rule 6): a ``KILL`` file in the data directory.

While the file exists the engine closes every Aurum position, cancels every Aurum pending order
and opens nothing. It is engaged by ``aurum kill`` (which also flattens at once in DEMO mode)
or by creating the file by hand, and cleared only by ``aurum unkill`` (deleting the file).
"""

from datetime import datetime
from pathlib import Path

from aurum.core.timeutil import to_utc


class KillSwitch:
    def __init__(self, path: Path) -> None:
        self.path = path

    def reason(self) -> str | None:
        """Why trading is locked (the file's text), or None when the switch is off."""
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        return text or "KILL file present"

    def engage(self, reason: str, now: datetime) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{to_utc(now).isoformat()} {reason}\n", encoding="utf-8")

    def clear(self) -> bool:
        """Remove the file; False if it was not there."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True
