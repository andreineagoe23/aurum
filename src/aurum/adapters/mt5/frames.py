"""MetaTrader5 numpy structured arrays -> polars frames in the stored raw schemas, with every
server timestamp converted to UTC (ADR-0004). Pure: no MetaTrader5 import, no I/O."""

from typing import Any

import numpy as np
import polars as pl

from aurum.adapters.mt5.servertime import ServerTimeZone
from aurum.data.schemas import RAW_BARS, RAW_TICKS, polars_schema

TICK_DTYPE = np.dtype(
    [
        ("time", "<i8"),
        ("bid", "<f8"),
        ("ask", "<f8"),
        ("last", "<f8"),
        ("volume", "<u8"),
        ("time_msc", "<i8"),
        ("flags", "<u4"),
        ("volume_real", "<f8"),
    ]
)
"""``copy_ticks_*`` row layout (MetaTrader5 5.0.6231, measured)."""

RATE_DTYPE = np.dtype(
    [
        ("time", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("tick_volume", "<u8"),
        ("spread", "<i4"),
        ("real_volume", "<u8"),
    ]
)
"""``copy_rates_*`` row layout (MetaTrader5 5.0.6231, measured)."""


def _utc_ms(values: np.ndarray[Any, Any]) -> pl.Series:
    return pl.Series(values.astype(np.int64)).cast(pl.Datetime("ms")).dt.replace_time_zone("UTC")


def ticks_to_frame(raw: np.ndarray[Any, Any], tz: ServerTimeZone) -> pl.DataFrame:
    """Rows in received order; ``time`` = UTC of ``time_msc``. Nothing is filtered."""
    arr = np.asarray(raw, dtype=TICK_DTYPE)
    utc = tz.to_utc_ms(arr["time_msc"])
    return pl.DataFrame(
        {
            "time": _utc_ms(utc),
            "bid": arr["bid"],
            "ask": arr["ask"],
            "last": arr["last"],
            "volume": arr["volume"],
            "flags": arr["flags"],
            "volume_real": arr["volume_real"],
        },
        schema=polars_schema(RAW_TICKS),
    )


def rates_to_frame(raw: np.ndarray[Any, Any], tz: ServerTimeZone) -> pl.DataFrame:
    """Rows in received order; ``open_time`` = UTC of the server bar time."""
    arr = np.asarray(raw, dtype=RATE_DTYPE)
    utc = tz.to_utc_ms(arr["time"] * 1000)
    return pl.DataFrame(
        {
            "open_time": _utc_ms(utc),
            "open": arr["open"],
            "high": arr["high"],
            "low": arr["low"],
            "close": arr["close"],
            "tick_volume": arr["tick_volume"],
            "spread": arr["spread"],
            "real_volume": arr["real_volume"],
        },
        schema=polars_schema(RAW_BARS),
    )
