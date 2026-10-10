"""Unit tests for the append-only TrialLedger."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from aurum.core.clock import ManualClock
from aurum.core.errors import LedgerError, NaiveDatetimeError
from aurum.core.models import TrialRecord
from aurum.ledger.trial_ledger import TrialLedger, current_code_version

T0 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
START = datetime(2023, 1, 1, tzinfo=UTC)
END = datetime(2023, 6, 1, tzinfo=UTC)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "ledger" / "trials.sqlite"


@pytest.fixture
def ledger(db_path: Path, clock: ManualClock) -> Iterator[TrialLedger]:
    with TrialLedger(db_path, clock=clock) as lg:
        yield lg


def _rec(lg: TrialLedger, strategy: str = "orb", variant: str = "m15", **kw: Any) -> TrialRecord:
    kw.setdefault("params", {"n": 1})
    kw.setdefault("data_start", START)
    kw.setdefault("data_end", END)
    kw.setdefault("metrics", {"sharpe": 1.5})
    return lg.record_trial(strategy=strategy, variant=variant, **kw)


def test_record_and_get_roundtrip(ledger: TrialLedger, tmp_path: Path) -> None:
    rec = _rec(ledger, notes="hello", code_version="abc", config_hash="h")
    assert rec.trial_id.startswith("tr_")
    assert rec.created_at == T0
    assert rec.created_at.utcoffset() == timedelta(0)
    assert (rec.strategy, rec.params) == ("orb", {"n": 1})
    assert ledger.get_trial(rec.trial_id) == rec
    with pytest.raises(LedgerError):
        ledger.get_trial("tr_nope")
    sealed = _rec(ledger, metrics={"sharpe": None, "n": 3.0}, sealed_test_opened=True)
    got = ledger.get_trial(sealed.trial_id)
    assert got.metrics == {"sharpe": None, "n": 3.0}
    assert got.sealed_test_opened is True
    assert rec.sealed_test_opened is False
    assert current_code_version(tmp_path) is None  # outside git


def test_list_insertion_order_and_filters(ledger: TrialLedger, clock: ManualClock) -> None:
    ids = []
    for strat, var in [("a", "m15"), ("b", "m5"), ("a", "m5"), ("b", "m15")]:
        ids.append(_rec(ledger, strat, var).trial_id)
        clock.advance(timedelta(seconds=1))
    assert [t.trial_id for t in ledger.list_trials()] == ids
    assert [t.trial_id for t in ledger.list_trials(strategy="a")] == [ids[0], ids[2]]
    assert [t.trial_id for t in ledger.list_trials(variant="m5")] == [ids[1], ids[2]]
    assert [t.trial_id for t in ledger.list_trials(strategy="b", variant="m15")] == [ids[3]]
    assert ledger.list_trials(strategy="zzz") == []


def test_counts_and_ids_unique_with_frozen_clock(ledger: TrialLedger) -> None:
    assert ledger.count_trials() == 0
    ids = {_rec(ledger, "a").trial_id for _ in range(200)}
    _rec(ledger, "b")
    assert len(ids) == 200
    assert ledger.count_trials() == 201
    assert (ledger.count_trials("a"), ledger.count_trials("b"), ledger.count_trials("c")) == (
        200,
        1,
        0,
    )


def test_update_and_delete_blocked(ledger: TrialLedger, db_path: Path) -> None:
    rec = _rec(ledger, notes="orig")
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE trials SET notes = 'x'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM trials")
    finally:
        conn.close()
    assert ledger.get_trial(rec.trial_id).notes == "orig"
    assert ledger.count_trials() == 1
    assert ledger.verify() == []


def test_verify_detects_tampering(ledger: TrialLedger, db_path: Path) -> None:
    good = _rec(ledger)
    bad = _rec(ledger, notes="orig")
    assert ledger.verify() == []
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TRIGGER trials_no_update")
        conn.execute("UPDATE trials SET notes = 'tampered' WHERE trial_id = ?", (bad.trial_id,))
        conn.commit()
    finally:
        conn.close()
    assert ledger.verify() == [bad.trial_id]
    assert good.trial_id not in ledger.verify()


def test_persistence_across_reopen(db_path: Path, clock: ManualClock) -> None:
    lg = TrialLedger(db_path, clock=clock)
    rec = _rec(lg)
    lg.close()
    with TrialLedger(db_path, clock=clock) as again:
        assert again.list_trials() == [rec]
        assert again.verify() == []


def test_invalid_records_rejected(ledger: TrialLedger) -> None:
    for bad in [
        {"metrics": {"sharpe": float("nan")}},
        {"params": {"x": object()}},
        {"data_start": END, "data_end": START},
        {"data_start": START, "data_end": START},
    ]:
        with pytest.raises(LedgerError):
            _rec(ledger, **bad)
    with pytest.raises(NaiveDatetimeError):
        _rec(ledger, data_start=datetime(2023, 1, 1))  # noqa: DTZ001
    assert ledger.count_trials() == 0
