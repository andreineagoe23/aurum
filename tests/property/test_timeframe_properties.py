"""Causality properties of completed-bar logic (MASTER_PROMPT §4 rule 7)."""

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from aurum.core.timeframes import Timeframe

instants = st.datetimes(
    min_value=datetime(2000, 1, 1),  # noqa: DTZ001 - hypothesis bounds are naive by API
    max_value=datetime(2100, 1, 1),  # noqa: DTZ001
    timezones=st.just(UTC),
)
timeframes = st.sampled_from(list(Timeframe))


@given(now=instants, tf=timeframes)
def test_last_completed_bar_is_never_in_the_future(now: datetime, tf: Timeframe) -> None:
    span = tf.last_completed_bar(now)
    assert span.close_time <= now


@given(now=instants, tf=timeframes)
def test_last_completed_bar_is_the_latest_one(now: datetime, tf: Timeframe) -> None:
    span = tf.last_completed_bar(now)
    # The next bar is still forming: it closes strictly after now.
    assert span.close_time + tf.duration > now
    assert now - span.close_time < tf.duration


@given(now=instants, tf=timeframes)
def test_spans_are_aligned_and_one_duration_long(now: datetime, tf: Timeframe) -> None:
    span = tf.last_completed_bar(now)
    assert tf.is_aligned(span.open_time)
    assert tf.bar_close_time(span.open_time) == span.close_time
    assert span.close_time - span.open_time == tf.duration


@given(now=instants, tf=timeframes)
def test_forming_bar_contains_now(now: datetime, tf: Timeframe) -> None:
    open_time = tf.bar_open_time(now)
    assert open_time <= now < open_time + tf.duration


@given(now=instants)
def test_higher_timeframe_boundaries_are_lower_timeframe_boundaries(now: datetime) -> None:
    # As-of joins rely on every H1 close also being an M15/M5/M1 close.
    h1_close = Timeframe.H1.last_completed_bar(now).close_time
    for tf in (Timeframe.M1, Timeframe.M5, Timeframe.M15):
        assert tf.is_aligned(h1_close)
        assert tf.last_completed_bar(now).close_time >= h1_close


@given(now=instants, tf=timeframes, delta=st.timedeltas(timedelta(0), timedelta(days=3)))
def test_monotonic(now: datetime, tf: Timeframe, delta: timedelta) -> None:
    assert tf.last_completed_bar(now + delta).close_time >= tf.last_completed_bar(now).close_time
