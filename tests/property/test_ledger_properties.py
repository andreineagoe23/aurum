"""Property tests: ledger roundtrips arbitrary JSON-like params/metrics exactly."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from aurum.core.clock import ManualClock
from aurum.ledger.trial_ledger import TrialLedger

_leaf = st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False)
_leaf = _leaf | st.text(max_size=10)
json_values = st.recursive(
    _leaf,
    lambda children: (
        st.lists(children, max_size=3) | st.dictionaries(st.text(max_size=8), children, max_size=3)
    ),
    max_leaves=10,
)
params_st = st.dictionaries(st.text(max_size=8), json_values, max_size=4)
metrics_st = st.dictionaries(
    st.text(max_size=8),
    st.none() | st.floats(allow_nan=False, allow_infinity=False),
    max_size=4,
)

START = datetime(2023, 1, 1, tzinfo=UTC)
END = datetime(2023, 6, 1, tzinfo=UTC)


@settings(max_examples=100, deadline=None)
@given(params=params_st, metrics=metrics_st)
def test_roundtrip_and_verify(params: dict[str, Any], metrics: dict[str, float | None]) -> None:
    clock = ManualClock(datetime(2024, 1, 1, tzinfo=UTC))
    with tempfile.TemporaryDirectory() as d, TrialLedger(Path(d) / "t.sqlite", clock=clock) as lg:
        rec = lg.record_trial(
            strategy="s",
            variant="v",
            params=params,
            data_start=START,
            data_end=END,
            metrics=metrics,
        )
        got = lg.get_trial(rec.trial_id)
        assert got.params == params
        assert got.metrics == metrics
        assert got == rec
        assert lg.verify() == []
