"""MetaTrader 5 adapter. The ONLY package allowed to import MetaTrader5, and only lazily."""

from aurum.adapters.mt5.broker import MT5Broker
from aurum.adapters.mt5.loader import is_available, load_mt5

__all__ = ["MT5Broker", "is_available", "load_mt5"]
