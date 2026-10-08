"""UTC helpers. Rule: every timestamp inside Aurum is timezone-aware and normalised to UTC."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime

from aurum.core.errors import NaiveDatetimeError

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def require_aware(dt: datetime, name: str = "timestamp") -> datetime:
    """Return ``dt`` unchanged, or raise if it has no usable tzinfo."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise NaiveDatetimeError(f"{name} must be timezone-aware, got naive {dt!r}")
    return dt


def to_utc(dt: datetime) -> datetime:
    """Convert an aware datetime to UTC. Naive datetimes are rejected, never assumed UTC."""
    return require_aware(dt).astimezone(UTC)


def utc_from_epoch_seconds(seconds: float) -> datetime:
    """UTC datetime from seconds since the Unix epoch (the epoch itself is UTC by definition)."""
    return datetime.fromtimestamp(seconds, tz=UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(to_utc)]
"""Pydantic field type: rejects naive input, stores the value converted to UTC."""
