"""Windows-only MT5 integration tests. Auto-skipped elsewhere (see tests/conftest.py).

TODO(Phase 1): ping, symbol discovery, bars/ticks in UTC, demo open -> modify -> close ->
reconcile, 20 reconnect cycles without duplicate orders.
"""

import pytest

from aurum.adapters.mt5 import is_available, load_mt5


@pytest.mark.mt5
def test_metatrader5_package_loads() -> None:
    assert is_available()
    mt5 = load_mt5()
    assert hasattr(mt5, "initialize")
