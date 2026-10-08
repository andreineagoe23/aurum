"""Unit tests for the aurum CLI."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aurum.cli import app
from aurum.ledger.trial_ledger import TrialLedger

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
    [["mt5", "ping"], ["data", "collect"], ["kill"], ["trade", "demo"], ["shadow", "run"]],
)
def test_placeholders_exit_2(args: list[str]) -> None:
    assert runner.invoke(app, args).exit_code == 2


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
