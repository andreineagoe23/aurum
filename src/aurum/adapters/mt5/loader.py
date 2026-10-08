"""Lazy access to the Windows-only ``MetaTrader5`` package.

Nothing in Aurum imports MetaTrader5 at module import time, so the whole package (including this
adapter) imports fine on macOS. ``tests/unit/test_architecture.py`` enforces that.
"""

import importlib
import importlib.util
import sys
from types import ModuleType

from aurum.core.errors import MT5UnavailableError


def _is_windows() -> bool:
    # A function call, so mypy does not mark Windows-only branches unreachable on macOS.
    return sys.platform == "win32"


def is_available() -> bool:
    """True only on Windows with the MetaTrader5 package installed (terminal not checked)."""
    return _is_windows() and importlib.util.find_spec("MetaTrader5") is not None


def load_mt5() -> ModuleType:
    """Import and return the MetaTrader5 module, or raise ``MT5UnavailableError``."""
    if not _is_windows():
        raise MT5UnavailableError(
            f"MetaTrader5 is Windows-only (this is {sys.platform}); run this in the Windows VM"
        )
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError as exc:
        raise MT5UnavailableError(
            "MetaTrader5 package not installed in this environment "
            "(needs x64 Python; see scripts/windows_setup.ps1)"
        ) from exc
