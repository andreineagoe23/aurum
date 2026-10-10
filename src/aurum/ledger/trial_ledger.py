"""Append-only SQLite trial ledger (MASTER_PROMPT §4 rule 9: record every trial, not the best).

* Trial IDs are generated here, never supplied by callers, and never reused.
* UPDATE and DELETE are blocked by SQLite triggers; ``verify()`` re-hashes every row to detect
  tampering that bypassed the triggers (e.g. editing the file directly).
"""

import hashlib
import json
import secrets
import sqlite3
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from aurum.core.clock import SystemClock
from aurum.core.errors import LedgerError
from aurum.core.models import TrialRecord
from aurum.core.timeutil import to_utc
from aurum.ports.clock import ClockPort

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
    seq                INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_id           TEXT NOT NULL UNIQUE,
    created_at         TEXT NOT NULL,
    strategy           TEXT NOT NULL,
    variant            TEXT NOT NULL,
    params_json        TEXT NOT NULL,
    data_start         TEXT NOT NULL,
    data_end           TEXT NOT NULL,
    metrics_json       TEXT NOT NULL,
    notes              TEXT NOT NULL,
    code_version       TEXT,
    config_hash        TEXT,
    sealed_test_opened INTEGER NOT NULL,
    record_hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trials_strategy ON trials(strategy, variant);
CREATE TRIGGER IF NOT EXISTS trials_no_update BEFORE UPDATE ON trials
BEGIN SELECT RAISE(ABORT, 'trial ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trials_no_delete BEFORE DELETE ON trials
BEGIN SELECT RAISE(ABORT, 'trial ledger is append-only'); END;
CREATE TABLE IF NOT EXISTS sealed_openings (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at    TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    variant      TEXT NOT NULL,
    opened_by    TEXT NOT NULL,
    reason       TEXT NOT NULL,
    first_day    TEXT NOT NULL,
    last_day     TEXT NOT NULL,
    config_hash  TEXT,
    code_version TEXT,
    UNIQUE (strategy, variant)
);
CREATE TRIGGER IF NOT EXISTS sealed_no_update BEFORE UPDATE ON sealed_openings
BEGIN SELECT RAISE(ABORT, 'sealed openings are append-only'); END;
CREATE TRIGGER IF NOT EXISTS sealed_no_delete BEFORE DELETE ON sealed_openings
BEGIN SELECT RAISE(ABORT, 'sealed openings are append-only'); END;
CREATE TABLE IF NOT EXISTS parity_accesses (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    accessed_at  TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    variant      TEXT NOT NULL,
    first_day    TEXT NOT NULL,
    last_day     TEXT NOT NULL,
    config_hash  TEXT,
    code_version TEXT
);
CREATE TRIGGER IF NOT EXISTS parity_no_update BEFORE UPDATE ON parity_accesses
BEGIN SELECT RAISE(ABORT, 'parity accesses are append-only'); END;
CREATE TRIGGER IF NOT EXISTS parity_no_delete BEFORE DELETE ON parity_accesses
BEGIN SELECT RAISE(ABORT, 'parity accesses are append-only'); END;
CREATE TABLE IF NOT EXISTS attempts (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id   TEXT NOT NULL UNIQUE,
    started_at   TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    variant      TEXT NOT NULL,
    params_json  TEXT NOT NULL,
    first_day    TEXT NOT NULL,
    last_day     TEXT NOT NULL,
    purpose      TEXT NOT NULL,
    grid_id      TEXT,
    retry_of     TEXT UNIQUE,
    code_version TEXT,
    config_hash  TEXT
);
CREATE TABLE IF NOT EXISTS attempt_outcomes (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id   TEXT NOT NULL UNIQUE,
    finished_at  TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
    stage        TEXT,
    error        TEXT,
    trial_id     TEXT
);
CREATE TRIGGER IF NOT EXISTS attempts_no_update BEFORE UPDATE ON attempts
BEGIN SELECT RAISE(ABORT, 'attempts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS attempts_no_delete BEFORE DELETE ON attempts
BEGIN SELECT RAISE(ABORT, 'attempts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON attempt_outcomes
BEGIN SELECT RAISE(ABORT, 'attempt outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON attempt_outcomes
BEGIN SELECT RAISE(ABORT, 'attempt outcomes are append-only'); END;
"""


@dataclass(frozen=True)
class Attempt:
    """One evaluation attempt: recorded before it runs, with its outcome once it ends.
    ``status`` is ``succeeded``, ``failed`` or ``unfinished`` (no outcome: it crashed or is
    still running)."""

    attempt_id: str
    started_at: datetime
    strategy: str
    variant: str
    params: dict[str, Any]
    first_day: date
    last_day: date
    purpose: str
    grid_id: str | None
    retry_of: str | None
    status: str
    stage: str | None
    error: str | None
    trial_id: str | None


@dataclass(frozen=True)
class SealedOpening:
    """One journalled opening of the sealed final-test period (at most one per strategy and
    variant, enforced by a UNIQUE constraint)."""

    opened_at: datetime
    strategy: str
    variant: str
    opened_by: str
    reason: str
    first_day: date
    last_day: date
    config_hash: str | None
    code_version: str | None


_COLUMNS = (
    "trial_id, created_at, strategy, variant, params_json, data_start, data_end, metrics_json, "
    "notes, code_version, config_hash, sealed_test_opened, record_hash"
)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise LedgerError(f"value is not canonical-JSON serialisable: {exc}") from exc


def current_code_version(cwd: Path | None = None) -> str | None:
    """``git`` HEAD sha, suffixed ``-dirty`` if the tree has changes; None outside git."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{sha}-dirty" if dirty else sha


class TrialLedger:
    def __init__(self, path: Path | str, *, clock: ClockPort | None = None) -> None:
        self._path = Path(path)
        if str(path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or SystemClock()
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _new_trial_id(self, created_at: datetime) -> str:
        return f"tr_{created_at:%Y%m%dT%H%M%S%f}_{secrets.token_hex(4)}"

    @staticmethod
    def _hash(row: Mapping[str, Any]) -> str:
        return hashlib.sha256(_canonical_json(dict(row)).encode()).hexdigest()

    def record_trial(
        self,
        *,
        strategy: str,
        variant: str,
        params: Mapping[str, Any],
        data_start: datetime,
        data_end: datetime,
        metrics: Mapping[str, float | None],
        notes: str = "",
        code_version: str | None = None,
        config_hash: str | None = None,
        sealed_test_opened: bool = False,
    ) -> TrialRecord:
        """Append one trial and return it. ``params``/``metrics`` must be JSON-serialisable;
        NaN/inf are rejected (use None for undefined metrics)."""
        data_start, data_end = to_utc(data_start), to_utc(data_end)
        if data_end <= data_start:
            raise LedgerError("data_end must be after data_start")
        created_at = to_utc(self._clock.now())
        row: dict[str, Any] = {
            "trial_id": self._new_trial_id(created_at),
            "created_at": created_at.isoformat(),
            "strategy": strategy,
            "variant": variant,
            "params_json": _canonical_json(dict(params)),
            "data_start": data_start.isoformat(),
            "data_end": data_end.isoformat(),
            "metrics_json": _canonical_json(dict(metrics)),
            "notes": notes,
            "code_version": code_version,
            "config_hash": config_hash,
            "sealed_test_opened": int(sealed_test_opened),
        }
        row["record_hash"] = self._hash(row)
        with self._conn:
            self._conn.execute(
                f"INSERT INTO trials ({_COLUMNS}) VALUES ({', '.join('?' * 13)})",
                tuple(row.values()),
            )
        return self._to_record(row)

    def get_trial(self, trial_id: str) -> TrialRecord:
        cur = self._conn.execute(f"SELECT {_COLUMNS} FROM trials WHERE trial_id = ?", (trial_id,))
        found = cur.fetchone()
        if found is None:
            raise LedgerError(f"unknown trial {trial_id}")
        return self._to_record(self._row(cur, found))

    def list_trials(
        self, strategy: str | None = None, variant: str | None = None
    ) -> list[TrialRecord]:
        """All matching trials in insertion order (never filtered by performance)."""
        sql = f"SELECT {_COLUMNS} FROM trials WHERE 1=1"
        args: list[str] = []
        if strategy is not None:
            sql += " AND strategy = ?"
            args.append(strategy)
        if variant is not None:
            sql += " AND variant = ?"
            args.append(variant)
        cur = self._conn.execute(sql + " ORDER BY seq", args)
        return [self._to_record(self._row(cur, r)) for r in cur.fetchall()]

    def count_trials(self, strategy: str | None = None) -> int:
        """Number of trials (input to the Deflated Sharpe Ratio in Phase 6)."""
        if strategy is None:
            (n,) = self._conn.execute("SELECT COUNT(*) FROM trials").fetchone()
        else:
            (n,) = self._conn.execute(
                "SELECT COUNT(*) FROM trials WHERE strategy = ?", (strategy,)
            ).fetchone()
        return int(n)

    def record_sealed_opening(
        self,
        *,
        strategy: str,
        variant: str,
        opened_by: str,
        reason: str,
        first_day: date,
        last_day: date,
        config_hash: str | None,
        code_version: str | None,
    ) -> SealedOpening:
        """Journal the one-time opening of the sealed period, before any of it is read."""
        if not opened_by.strip() or not reason.strip():
            raise LedgerError("opening the sealed period needs who (--by) and why (--reason)")
        opening = SealedOpening(
            opened_at=to_utc(self._clock.now()),
            strategy=strategy,
            variant=variant,
            opened_by=opened_by.strip(),
            reason=reason.strip(),
            first_day=first_day,
            last_day=last_day,
            config_hash=config_hash,
            code_version=code_version,
        )
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO sealed_openings (opened_at, strategy, variant, opened_by, reason,"
                    " first_day, last_day, config_hash, code_version)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        opening.opened_at.isoformat(),
                        strategy,
                        variant,
                        opening.opened_by,
                        opening.reason,
                        first_day.isoformat(),
                        last_day.isoformat(),
                        config_hash,
                        code_version,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise LedgerError(
                f"the sealed period was already opened for {strategy} {variant}; "
                "it can be opened only once"
            ) from exc
        return opening

    def sealed_openings(self) -> list[SealedOpening]:
        cur = self._conn.execute(
            "SELECT opened_at, strategy, variant, opened_by, reason, first_day, last_day,"
            " config_hash, code_version FROM sealed_openings ORDER BY seq"
        )
        return [
            SealedOpening(
                opened_at=datetime.fromisoformat(r[0]),
                strategy=r[1],
                variant=r[2],
                opened_by=r[3],
                reason=r[4],
                first_day=date.fromisoformat(r[5]),
                last_day=date.fromisoformat(r[6]),
                config_hash=r[7],
                code_version=r[8],
            )
            for r in cur.fetchall()
        ]

    def start_attempt(
        self,
        *,
        strategy: str,
        variant: str,
        params: Mapping[str, Any],
        first_day: date,
        last_day: date,
        purpose: str,
        grid_id: str | None = None,
        retry_of: str | None = None,
        code_version: str | None = None,
        config_hash: str | None = None,
    ) -> str:
        """Record an evaluation attempt before it runs; returns its id. A retry names the
        failed or unfinished attempt it repeats: same grid point, at most one retry each."""
        params_json = _canonical_json(dict(params))
        if retry_of is not None:
            self.check_retry(
                retry_of,
                strategy=strategy,
                variant=variant,
                params=params,
                first_day=first_day,
                last_day=last_day,
                purpose=purpose,
                grid_id=grid_id,
            )
        started = to_utc(self._clock.now())
        attempt_id = f"at_{started:%Y%m%dT%H%M%S%f}_{secrets.token_hex(4)}"
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO attempts (attempt_id, started_at, strategy, variant, params_json,"
                    " first_day, last_day, purpose, grid_id, retry_of, code_version, config_hash)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        attempt_id,
                        started.isoformat(),
                        strategy,
                        variant,
                        params_json,
                        first_day.isoformat(),
                        last_day.isoformat(),
                        purpose,
                        grid_id,
                        retry_of,
                        code_version,
                        config_hash,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise LedgerError(f"attempt {retry_of} was already retried") from exc
        return attempt_id

    def check_retry(
        self,
        retry_of: str,
        *,
        strategy: str,
        variant: str,
        params: Mapping[str, Any],
        first_day: date,
        last_day: date,
        purpose: str,
        grid_id: str | None,
    ) -> None:
        """Raise unless ``retry_of`` is a failed or unfinished attempt of exactly this grid point
        that has not been retried yet. Read-only: callers check before any side effect."""
        prior = self.get_attempt(retry_of)
        if prior.status == "succeeded":
            raise LedgerError(f"attempt {retry_of} succeeded; there is nothing to retry")
        same = (
            prior.strategy,
            prior.variant,
            _canonical_json(prior.params),
            prior.first_day,
            prior.last_day,
            prior.purpose,
            prior.grid_id,
        ) == (
            strategy,
            variant,
            _canonical_json(dict(params)),
            first_day,
            last_day,
            purpose,
            grid_id,
        )
        if not same:
            raise LedgerError(f"a retry must repeat attempt {retry_of}'s exact grid point")
        taken = self._conn.execute(
            "SELECT attempt_id FROM attempts WHERE retry_of = ?", (retry_of,)
        ).fetchone()
        if taken is not None:
            raise LedgerError(f"attempt {retry_of} was already retried by {taken[0]}")

    def finish_attempt(
        self,
        attempt_id: str,
        *,
        succeeded: bool,
        stage: str | None = None,
        error: str | None = None,
        trial_id: str | None = None,
    ) -> None:
        """The attempt's one terminal outcome (success with its trial, or the failing stage
        and error)."""
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO attempt_outcomes (attempt_id, finished_at, status, stage, error,"
                    " trial_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        attempt_id,
                        to_utc(self._clock.now()).isoformat(),
                        "succeeded" if succeeded else "failed",
                        stage,
                        error,
                        trial_id,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise LedgerError(f"attempt {attempt_id} already has an outcome") from exc

    _ATTEMPT_SQL = (
        "SELECT a.attempt_id, a.started_at, a.strategy, a.variant, a.params_json, a.first_day,"
        " a.last_day, a.purpose, a.grid_id, a.retry_of, o.status, o.stage, o.error, o.trial_id"
        " FROM attempts a LEFT JOIN attempt_outcomes o ON o.attempt_id = a.attempt_id"
    )

    @staticmethod
    def _attempt(r: tuple[Any, ...]) -> Attempt:
        return Attempt(
            attempt_id=r[0],
            started_at=datetime.fromisoformat(r[1]),
            strategy=r[2],
            variant=r[3],
            params=json.loads(r[4]),
            first_day=date.fromisoformat(r[5]),
            last_day=date.fromisoformat(r[6]),
            purpose=r[7],
            grid_id=r[8],
            retry_of=r[9],
            status=r[10] or "unfinished",
            stage=r[11],
            error=r[12],
            trial_id=r[13],
        )

    def get_attempt(self, attempt_id: str) -> Attempt:
        row = self._conn.execute(
            self._ATTEMPT_SQL + " WHERE a.attempt_id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise LedgerError(f"unknown attempt {attempt_id}")
        return self._attempt(row)

    def attempts(self, *, grid_id: str | None = None) -> list[Attempt]:
        """Every attempt (or a grid's), oldest first, failures and unfinished ones included."""
        sql, args = self._ATTEMPT_SQL, tuple[str, ...]()
        if grid_id is not None:
            sql, args = sql + " WHERE a.grid_id = ?", (grid_id,)
        return [self._attempt(r) for r in self._conn.execute(sql + " ORDER BY a.seq", args)]

    def record_parity_access(
        self,
        *,
        strategy: str,
        variant: str,
        first_day: date,
        last_day: date,
        config_hash: str | None,
        code_version: str | None,
    ) -> None:
        """Journal one parity-exempt read inside the sealed period, before it happens. Kept
        apart from research trials and from sealed openings."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO parity_accesses (accessed_at, strategy, variant, first_day, last_day,"
                " config_hash, code_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    to_utc(self._clock.now()).isoformat(),
                    strategy,
                    variant,
                    first_day.isoformat(),
                    last_day.isoformat(),
                    config_hash,
                    code_version,
                ),
            )

    def parity_accesses(self) -> list[tuple[str, str, date, date]]:
        """(strategy, variant, first day, last day) of every parity access, oldest first."""
        cur = self._conn.execute(
            "SELECT strategy, variant, first_day, last_day FROM parity_accesses ORDER BY seq"
        )
        return [
            (r[0], r[1], date.fromisoformat(r[2]), date.fromisoformat(r[3])) for r in cur.fetchall()
        ]

    def verify(self) -> list[str]:
        """Trial IDs whose stored content no longer matches its hash (empty = intact)."""
        cur = self._conn.execute(f"SELECT {_COLUMNS} FROM trials ORDER BY seq")
        bad = []
        for raw in cur.fetchall():
            row = self._row(cur, raw)
            stored = row.pop("record_hash")
            if self._hash(row) != stored:
                bad.append(str(row["trial_id"]))
        return bad

    @staticmethod
    def _row(cur: sqlite3.Cursor, values: tuple[Any, ...]) -> dict[str, Any]:
        return {d[0]: v for d, v in zip(cur.description, values, strict=True)}

    @staticmethod
    def _to_record(row: Mapping[str, Any]) -> TrialRecord:
        return TrialRecord(
            trial_id=row["trial_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            strategy=row["strategy"],
            variant=row["variant"],
            params=json.loads(row["params_json"]),
            data_start=datetime.fromisoformat(row["data_start"]),
            data_end=datetime.fromisoformat(row["data_end"]),
            metrics=json.loads(row["metrics_json"]),
            notes=row["notes"],
            code_version=row["code_version"],
            config_hash=row["config_hash"],
            sealed_test_opened=bool(row["sealed_test_opened"]),
            record_hash=row["record_hash"],
        )
