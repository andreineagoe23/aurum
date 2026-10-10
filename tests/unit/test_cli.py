"""Unit tests for the aurum CLI."""

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aurum.adapters.mt5 import MT5Broker
from aurum.cli import app
from aurum.core.clock import ManualClock
from aurum.core.models import OrderIntent
from aurum.ledger.trial_ledger import TrialLedger
from aurum.settings import AppConfig, Settings
from tests.fake_mt5 import FakeMT5

REPO_ROOT = Path(__file__).parents[2]
runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AURUM_MODE", "BACKTEST")
    monkeypatch.delenv("AURUM_LIVE_ACK", raising=False)
    monkeypatch.setenv("AURUM_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(REPO_ROOT)


def test_doctor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "python" in result.output
    assert "config" in result.output
    empty = tmp_path / "empty_cfg"
    empty.mkdir()
    monkeypatch.setenv("AURUM_CONFIG_DIR", str(empty))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1, result.output
    assert "FAIL" in result.output


def test_placeholders_exit_2() -> None:
    for args in (["trade", "demo"], ["shadow", "run"]):
        assert runner.invoke(app, args).exit_code == 2, args


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
    assert "USD" in result.output
    assert "VERIFIED" in result.output
    assert "UTC+3.00h" in result.output
    fake_terminal.initialize_results = [False, False, False]
    result = runner.invoke(app, ["mt5", "ping"])
    assert result.exit_code == 1
    assert "IPC timeout" in result.output


def test_mt5_symbols_writes_snapshot(fake_terminal: FakeMT5, tmp_path: Path) -> None:
    result = runner.invoke(app, ["mt5", "symbols", "--find", "gold"])
    assert result.exit_code == 0, result.output
    assert "XAUUSD" in result.output
    assert len(list((tmp_path / "specs" / "XAUUSD").glob("*.json"))) == 1


def test_ledger_list(tmp_path: Path) -> None:
    result = runner.invoke(app, ["ledger", "list"])
    assert result.exit_code == 0, result.output
    assert "No ledger yet" in result.output
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


class _MovingClock:
    """Starts at the fake terminal's instant and advances with wall time."""

    def __init__(self, start: datetime) -> None:
        self._start, self._t0 = start, time.monotonic()

    def now(self) -> datetime:
        return self._start + timedelta(seconds=time.monotonic() - self._t0)


def test_data_collect_runs_and_stops(
    fake_terminal: FakeMT5, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The collector reads the system clock; pin it (and the fake terminal) to one moving clock
    # so the test doesn't depend on today's date.
    clock = _MovingClock(fake_terminal.now())
    fake_terminal.now = clock.now  # type: ignore[method-assign]
    monkeypatch.setattr("aurum.apps.collector.SystemClock", lambda: clock)
    ms = fake_terminal._server_ms()
    fake_terminal.tick_rows = [
        {"time_msc": ms - 2000 + i * 100, "bid": 4142.7, "ask": 4142.8} for i in range(10)
    ]
    log = tmp_path / "collector.jsonl"
    result = runner.invoke(app, ["data", "collect", "--max-seconds", "0.2", "--log-file", str(log)])
    assert result.exit_code == 0, result.output
    assert "stopped: 10 ticks" in result.output
    assert list((tmp_path / "raw_ticks" / "mt5_live" / "XAUUSD").rglob("*.parquet"))
    assert list((tmp_path / "specs" / "XAUUSD").glob("*.json"))  # spec snapshot on connect
    assert "collector.exit" in log.read_text(encoding="utf-8")
    assert not (tmp_path / "collector" / "STOP").exists()


def test_data_backfill_writes_days_and_report(
    fake_terminal: FakeMT5, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)  # empty-day retries
    ms = fake_terminal.server_s(datetime(2026, 10, 7, 12, tzinfo=UTC)) * 1000
    fake_terminal.tick_rows = [{"time_msc": ms + i, "bid": 4142.7, "ask": 4142.8} for i in range(5)]
    args = ["data", "backfill", "--tf", "H1", "--from", "2026-10-06", "--to", "2026-10-07"]
    result = runner.invoke(app, args, env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    ticks = sorted(p.name for p in (tmp_path / "raw_ticks/mt5_history/XAUUSD").iterdir())
    assert ticks == ["2026-10-06.parquet", "2026-10-07.parquet"]
    assert len(list((tmp_path / "raw_broker_bars/mt5/XAUUSD/H1").iterdir())) == 2
    assert list((tmp_path / "quality_reports" / "backfill").glob("*.json"))
    assert "2026-10-06" in result.output  # a weekday with no data is listed as empty


# --------------------------------------------------------------------------- kill switch


def test_kill_and_unkill_in_backtest_mode_touch_no_broker(tmp_path: Path) -> None:
    result = runner.invoke(app, ["kill", "--reason", "drill"])
    assert result.exit_code == 0, result.output
    assert "sends nothing to the broker" in result.output
    assert "drill" in (tmp_path / "KILL").read_text(encoding="utf-8")
    cleared = runner.invoke(app, ["unkill"])
    assert "kill switch cleared" in cleared.output
    assert not (tmp_path / "KILL").exists()
    assert "was not engaged" in runner.invoke(app, ["unkill"]).output


def test_kill_in_demo_flattens_only_aurum_positions(
    fake_terminal: FakeMT5, monkeypatch: pytest.MonkeyPatch, app_config: AppConfig
) -> None:
    from tests.factories import approve, signal
    from tests.unit.test_mt5_adapter import NOW, make_settings

    settings = make_settings(app_config)
    broker = MT5Broker(settings, mt5_module=fake_terminal, clock=ManualClock(NOW))
    broker.connect()
    for offset in (0, 1):
        sig = signal(t=NOW + timedelta(seconds=offset), stop_loss=4127.0)
        broker.send_order(OrderIntent.from_signal(sig, approve(sig, 0.01), created_at=sig.time))
    # MT5Broker stamps Aurum's magic on everything it sends: make the second one a trade
    # placed by hand in the terminal (magic 0, no AU comment).
    manual = list(fake_terminal.positions.values())[1]
    manual.magic, manual.comment = 0, "by hand"
    assert len(broker.positions()) == 2
    monkeypatch.setenv("AURUM_MODE", "DEMO")
    result = runner.invoke(app, ["kill"])
    assert result.exit_code == 0, result.output
    assert "1 Aurum orders/positions flattened" in result.output
    (left,) = broker.positions()
    assert left.magic == 0


def test_watchdog_alarm_engages_the_kill_switch(tmp_path: Path) -> None:
    # No heartbeat at all: an alarm only inside the trading window, idle otherwise.
    result = runner.invoke(app, ["watchdog"])
    if "MISSING" in result.output:
        assert result.exit_code == 1
        assert (tmp_path / "KILL").exists()
    else:
        assert "IDLE" in result.output
        assert result.exit_code == 0
        assert not (tmp_path / "KILL").exists()
