"""Intent store: every order Aurum decides to send, with the signal behind it and its outcome.

Persisted (SQLite under the data dir) so that after a restart the engine still knows each
position's signal (``max_hold`` deadlines, invalidation) and which sends had an unknown outcome.
Backtests use an in-memory store. The broker remains the source of truth for positions; the
store is reconciled against it (``aurum.execution.reconcile``).

Statuses: SENT (handed to the broker) -> FILLED / PLACED / REJECTED / UNKNOWN (connection lost
mid-request) -> CLOSED / CANCELLED / EXPIRED (never reached the broker, too old to send).
ADOPTED marks an Aurum-owned position found at the broker without a stored intent.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Self

from aurum.core.models import OrderIntent, PositionState, Signal
from aurum.core.timeutil import to_utc


class IntentStatus(StrEnum):
    SENT = "SENT"
    FILLED = "FILLED"
    PLACED = "PLACED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    EXPIRED = "EXPIRED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    ADOPTED = "ADOPTED"


UNRESOLVED = frozenset({IntentStatus.SENT, IntentStatus.UNKNOWN})
"""The broker may or may not hold an order for these: no new entry until resolved."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intents (
    key         TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    status      TEXT NOT NULL,
    signal      TEXT,
    intent      TEXT,
    position_id INTEGER,
    order_id    INTEGER,
    note        TEXT NOT NULL DEFAULT ''
)
"""


@dataclass(frozen=True, slots=True)
class IntentRecord:
    key: str
    created_at: datetime
    updated_at: datetime
    status: IntentStatus
    signal: Signal | None
    intent: OrderIntent | None
    position_id: int | None
    order_id: int | None
    note: str


class IntentStore:
    def __init__(self, path: Path | None = None) -> None:
        """``path=None``: in memory (backtests). Otherwise a SQLite file, created if missing."""
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(":memory:" if path is None else str(path))
        self._db.execute(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------ writes
    def add(self, intent: OrderIntent, signal: Signal, at: datetime) -> None:
        """Record an intent before it is sent (no-op if the key is already known)."""
        ts = to_utc(at).isoformat()
        self._db.execute(
            "INSERT OR IGNORE INTO intents (key, created_at, updated_at, status, signal, intent)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                intent.idempotency_key,
                to_utc(intent.created_at).isoformat(),
                ts,
                IntentStatus.SENT,
                signal.model_dump_json(),
                intent.model_dump_json(),
            ),
        )
        self._db.commit()

    def adopt(self, position: PositionState, at: datetime) -> None:
        """Record an owned position the store did not know (no signal: force-flat/kill only)."""
        assert position.idempotency_key is not None
        ts = to_utc(at).isoformat()
        self._db.execute(
            "INSERT OR IGNORE INTO intents (key, created_at, updated_at, status, position_id, note)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                position.idempotency_key,
                to_utc(position.open_time).isoformat(),
                ts,
                IntentStatus.ADOPTED,
                position.position_id,
                "found at the broker without a stored intent",
            ),
        )
        self._db.commit()

    def set_status(
        self,
        key: str,
        status: IntentStatus,
        at: datetime,
        *,
        position_id: int | None = None,
        order_id: int | None = None,
        note: str = "",
    ) -> None:
        self._db.execute(
            "UPDATE intents SET status = ?, updated_at = ?,"
            " position_id = COALESCE(?, position_id), order_id = COALESCE(?, order_id),"
            " note = ? WHERE key = ?",
            (status, to_utc(at).isoformat(), position_id, order_id, note, key),
        )
        self._db.commit()

    # ------------------------------------------------------------------ reads
    def get(self, key: str) -> IntentRecord | None:
        rows = self._select("WHERE key = ?", (key,))
        return rows[0] if rows else None

    def records(self, statuses: frozenset[IntentStatus] | None = None) -> list[IntentRecord]:
        """Records (all, or those in ``statuses``), oldest first."""
        if statuses is None:
            return self._select("", ())
        marks = ", ".join("?" for _ in statuses)
        return self._select(f"WHERE status IN ({marks})", tuple(sorted(statuses)))

    def signals(self) -> dict[str, Signal]:
        """Idempotency key -> signal, for every stored intent that has one."""
        return {r.key: r.signal for r in self.records() if r.signal is not None}

    def _select(self, where: str, args: tuple[object, ...]) -> list[IntentRecord]:
        cur = self._db.execute(
            "SELECT key, created_at, updated_at, status, signal, intent, position_id, order_id,"
            f" note FROM intents {where} ORDER BY created_at, key",
            args,
        )
        return [
            IntentRecord(
                key=k,
                created_at=datetime.fromisoformat(c),
                updated_at=datetime.fromisoformat(u),
                status=IntentStatus(s),
                signal=Signal.model_validate_json(sig) if sig else None,
                intent=OrderIntent.model_validate_json(it) if it else None,
                position_id=pos,
                order_id=oid,
                note=note,
            )
            for k, c, u, s, sig, it, pos, oid, note in cur.fetchall()
        ]
