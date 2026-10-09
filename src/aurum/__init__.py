"""Aurum: research and automated-trading platform for intraday XAUUSD on MetaTrader 5.

Demo only. Live trading is blocked by default (see ``aurum.core.safety``).
"""

from aurum._platform import configure_polars_runtime

configure_polars_runtime()  # before anything imports polars (see aurum/_platform.py)

__version__ = "0.0.1"
