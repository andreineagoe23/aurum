"""Unit tests for the aurum CLI."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aurum.adapters.mt5 import MT5Broker
from aurum.cli import app
from aurum.core.clock import ManualClock
from aurum.ledger.trial_ledger import TrialLedger
from aurum.settings import Settings
from tests.fake_mt5 import FakeMT5

REPO_ROOT = Path(__file__).parents[2]
runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AURUM_MODE", "BACKTEST")
    monkeypatch.delenv("AURUM_LIVE_ACK", raising=False)
    monkeypatch.setenv("AURUM_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(REPO_ROOT)


def test_doctor_ok() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "python" in result.output
    assert "config" in result.output


def test_doctor_fails_on_empty_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty = tmp_path / "empty_cfg"
    empty.mkdir()
    monkeypatch.setenv("AURUM_CONFIG_DIR", str(empty))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1, result.output
    assert "FAIL" in result.output


@pytest.mark.parametrize(
    "args",
    [["data", "collect"], ["kill"], ["trade", "demo"], ["shadow", "run"]],
)
def test_placeholders_exit_2(args: list[str]) -> None:
    assert runner.invoke(app, args).exit_code == 2


# --------------------------------------------------------------------------- mt5 (fake terminal)


@pytest.fixture
def fake_terminal(monkeypatch: pytest.MonkeyPatch) -> FakeMT5:
    """Route the CLI's MT5Broker to the fake module: unit tests never touch a real terminal."""
    import aurum.adapters.mt5 as mt5_pkg

    clock = ManualClock(datetime(2026, 10, 9, 0, 35, tzinfo=UTC))
    fake = FakeMT5(now=clock.now)
    real = mt5_pkg.MT5Broker

    def factory(settings: Settings) -> MT5Broker:
        return real(settings, mt5_module=fake, clock=clock)

    monkeypatch.setattr(mt5_pkg, "MT5Broker", factory)
    return fake


def test_mt5_ping(fake_terminal: FakeMT5) -> None:
    result = runner.invoke(app, ["mt5", "ping"])
    assert result.exit_code == 0, result.output
    assert "DEMO" in result.output
    assert "GBP" in result.output
    assert "VERIFIED" in result.output
    assert "UTC+3.00h" in result.output


def test_mt5_ping_connect_failure_exits_1(fake_terminal: FakeMT5) -> None:
    fake_terminal.initialize_results = [False, False, False]
    result = runner.invoke(app, ["mt5", "ping"])
    assert result.exit_code == 1
    assert "IPC timeout" in result.output


def test_mt5_symbols_writes_snapshot(fake_terminal: FakeMT5, tmp_path: Path) -> None:
    result = runner.invoke(app, ["mt5", "symbols", "--find", "gold"])
    assert result.exit_code == 0, result.output
    assert "XAUUSD" in result.output
    assert len(list((tmp_path / "specs" / "XAUUSD").glob("*.json"))) == 1


def test_ledger_list_no_ledger() -> None:
    result = runner.invoke(app, ["ledger", "list"])
    assert result.exit_code == 0, result.output
    assert "No ledger yet" in result.output


def test_ledger_list_with_trial(tmp_path: Path) -> None:
    with TrialLedger(tmp_path / "ledger" / "trials.sqlite") as lg:
        lg.record_trial(
            strategy="zebra_strategy",
            variant="m15",
            params={},
            data_start=datetime(2023, 1, 1, tzinfo=UTC),
            data_end=datetime(2023, 2, 1, tzinfo=UTC),
            metrics={},
        )
    result = runner.invoke(app, ["ledger", "list"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "zebra_strategy" in result.output
