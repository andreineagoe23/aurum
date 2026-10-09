import os
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

from aurum.adapters.mt5.loader import is_available
from aurum.settings import AppConfig, load_config

settings.register_profile("default", max_examples=200, deadline=None)
settings.register_profile(
    "ci", max_examples=1000, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.load_profile("default")

REPO_ROOT = Path(__file__).resolve().parents[1]


ORDER_TESTS_ENV = "AURUM_MT5_ORDER_TESTS"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """MT5 integration tests only run on Windows with the MetaTrader5 package installed.
    Tests that send (demo) orders additionally need ``AURUM_MT5_ORDER_TESTS=1``."""
    mt5_ok = sys.platform == "win32" and is_available()
    orders_ok = mt5_ok and os.environ.get(ORDER_TESTS_ENV) == "1"
    no_mt5 = pytest.mark.skip(reason="needs Windows + MetaTrader5 package + running terminal")
    no_orders = pytest.mark.skip(reason=f"sends demo orders; set {ORDER_TESTS_ENV}=1 to run")
    for item in items:
        if "mt5_orders" in item.keywords and not orders_ok:
            item.add_marker(no_orders)
        elif "mt5" in item.keywords and not mt5_ok:
            item.add_marker(no_mt5)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def app_config() -> AppConfig:
    """The real configs/ from the repo (so the tests also validate what is committed)."""
    return load_config(REPO_ROOT / "configs")
