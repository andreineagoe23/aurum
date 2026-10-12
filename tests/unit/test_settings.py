"""Config validation: the committed configs load, and bad ones fail loudly."""

import shutil
from datetime import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from aurum.core.enums import Mode
from aurum.core.errors import ConfigError
from aurum.settings import (
    AppConfig,
    EnvSettings,
    Settings,
    load_config,
    load_settings,
    readiness_issues,
)


@pytest.fixture
def config_dir(tmp_path: Path, repo_root: Path) -> Path:
    target = tmp_path / "configs"
    shutil.copytree(repo_root / "configs", target)
    return target


def edit(config_dir: Path, file: str, **changes: Any) -> None:
    path = config_dir / f"{file}.yaml"
    data = yaml.safe_load(path.read_text())
    data.update(changes)
    path.write_text(yaml.safe_dump(data))


# --------------------------------------------------------------------------- committed configs


def test_repo_configs_are_valid(app_config: AppConfig) -> None:
    assert app_config.sessions.timezone == "Europe/London"
    assert app_config.sessions.sessions["london_morning"].start == time(7, 0)
    assert app_config.sessions.force_flat_at == time(20, 0)
    assert app_config.broker.commission.per_lot_round_trip == pytest.approx(4.50)
    assert app_config.risk.max_open_positions == 1
    assert app_config.risk.max_spread_points == 20
    assert app_config.promotion.live_enabled is False
    assert set(app_config.strategies) == {
        "trend_pullback",
        "opening_range_breakout",
        "vwap_reversion",
        "reference_fixed_time",
    }
    assert app_config.strategies["reference_fixed_time"].family == "reference"
    assert app_config.strategies["reference_fixed_time"].status == "draft"
    assert all(
        s.status == "rejected" for s in app_config.strategies.values() if s.family != "reference"
    )
    assert app_config.backtest.bar_spread_extra_points == 2
    assert app_config.backtest.latency_ms == 200
    sealed = app_config.backtest.sealed_period
    assert (sealed.first.isoformat(), sealed.last.isoformat()) == ("2025-10-01", "2026-10-08")
    parity = sealed.parity_exemption
    assert parity is not None
    assert (parity.strategy, parity.variant, parity.first.isoformat(), parity.last.isoformat()) == (
        "reference_fixed_time",
        "m5",
        "2026-09-14",
        "2026-10-08",
    )
    assert not any(s.enabled for s in app_config.strategies.values())


# --------------------------------------------------------------------------- invalid configs


@pytest.mark.parametrize(
    ("name", "changes", "match"),
    [
        # Safety caps (MASTER_PROMPT §2 risk limits, §4 rule 1)
        ("risk", {"risk_per_trade_pct": 5}, "risk_per_trade_pct"),
        ("risk", {"max_open_positions": 2}, "max_open_positions"),
        ("risk", {"weekly_loss_limit_pct": 0.5}, "weekly_loss_limit_pct"),
        ("promotion", {"live_enabled": True}, "signed_off_date"),
        # Unknown keys and malformed values
        ("risk", {"unknown_limit": 1}, "unknown_limit"),
        ("sessions", {"timezone": "Mars/Olympus"}, "unknown timezone"),
        ("sessions", {"force_flat_at": "19:00"}, "before force_flat_at"),
        ("sessions", {"trading_days": ["MON", "MON"]}, "duplicates"),
        ("broker", {"server_time": {"shift_hours": 30}}, "shift_hours"),
        ("broker", {"server_time": {"timezone": "Mars/Olympus"}}, "unknown timezone"),
        ("broker", {"commission": {"per_lot_per_side": -1}}, "per_lot_per_side"),
        # Sealed final-test period (D1): ordered; the parity exemption is one reference strategy
        # and variant inside it, and nothing else
        ("backtest", {"sealed_period": {"first": "2026-10-08", "last": "2025-10-01"}}, "before"),
        (
            "backtest",
            {
                "sealed_period": {
                    "first": "2025-10-01",
                    "last": "2026-10-08",
                    "exempt_families": ["reference"],
                }
            },
            "exempt_families",
        ),
        (
            "backtest",
            {
                "sealed_period": {
                    "first": "2025-10-01",
                    "last": "2026-09-30",
                    "parity_exemption": {
                        "strategy": "reference_fixed_time",
                        "variant": "m5",
                        "first": "2026-09-14",
                        "last": "2026-10-08",
                    },
                }
            },
            "inside",
        ),
        (
            "backtest",
            {
                "sealed_period": {
                    "first": "2025-10-01",
                    "last": "2026-10-08",
                    "parity_exemption": {
                        "strategy": "trend_pullback",
                        "variant": "m5",
                        "first": "2026-09-14",
                        "last": "2026-10-08",
                    },
                }
            },
            "reference-family",
        ),
    ],
)
def test_invalid_values_fail_loudly(
    config_dir: Path, name: str, changes: dict[str, Any], match: str
) -> None:
    edit(config_dir, name, **changes)
    with pytest.raises(ConfigError, match=match):
        load_config(config_dir)


def test_invalid_session_windows(config_dir: Path) -> None:
    for strat in (config_dir / "strategies").glob("*.yaml"):
        strat.unlink()  # they reference the real session names
    cases: list[tuple[dict[str, Any], str]] = [
        ({"a": {"start": "11:00", "end": "07:00"}}, "must be before end"),
        (
            {"a": {"start": "07:00", "end": "12:00"}, "b": {"start": "11:00", "end": "13:00"}},
            "overlap",
        ),
        ({"a": {"start": "19:45", "end": "19:50"}}, "starts at/after no_new_entries_after"),
        ({"a": {"start": "07:00", "end": "21:00"}}, "ends after force_flat_at"),
        ({"a": {"start": "00:10", "end": "05:00"}}, "before local midnight"),
    ]
    for sessions, match in cases:
        edit(config_dir, "sessions", sessions=sessions)
        with pytest.raises(ConfigError, match=match):
            load_config(config_dir)


def test_unquoted_yaml_time_is_rejected_not_misread(config_dir: Path) -> None:
    # Unquoted 13:00 is YAML 1.1 sexagesimal (= 780); it must not become 00:13.
    path = config_dir / "sessions.yaml"
    path.write_text(path.read_text().replace('start: "13:00"', "start: 13:00"))
    with pytest.raises(ConfigError, match="HH:MM"):
        load_config(config_dir)


def test_strategy_config_errors(config_dir: Path) -> None:
    path = config_dir / "strategies" / "vwap_reversion.yaml"  # no spec yet
    original = path.read_text()
    for changes, match in [
        ({"enabled": True}, "without a spec"),
        ({"sessions": ["tokyo"]}, "unknown sessions"),
        ({"name": "other_name"}, "declares name"),
        ({"status": "approved"}, "status"),
        ({"status": None}, "status"),
    ]:
        path.write_text(original)
        edit(config_dir, "strategies/vwap_reversion", **changes)
        with pytest.raises(ConfigError, match=match):
            load_config(config_dir)
    for status in ("draft", "frozen", "rejected"):
        path.write_text(original)
        edit(config_dir, "strategies/vwap_reversion", status=status)
        assert load_config(config_dir).strategies["vwap_reversion"].status == status
    raw = yaml.safe_load(original)
    raw.pop("status")
    path.write_text(yaml.safe_dump(raw))
    assert load_config(config_dir).strategies["vwap_reversion"].status == "draft"


def test_missing_file_and_bad_yaml(config_dir: Path) -> None:
    (config_dir / "risk.yaml").write_text("risk: [unclosed")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(config_dir)
    (config_dir / "risk.yaml").unlink()
    with pytest.raises(ConfigError, match="missing config file"):
        load_config(config_dir)


# --------------------------------------------------------------------------- environment


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for var in (
        "AURUM_MODE",
        "AURUM_DATA_DIR",
        "AURUM_CONFIG_DIR",
        "AURUM_LIVE_ACK",
        "MT5_LOGIN",
        "MT5_PASSWORD",
        "MT5_SERVER",
        "MT5_PATH",
    ):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_env_defaults_are_safe(
    clean_env: pytest.MonkeyPatch, repo_root: Path, tmp_path: Path
) -> None:
    s = load_settings(repo_root, env_file=tmp_path / "absent.env")
    assert s.mode is Mode.BACKTEST
    assert s.env.aurum_live_ack is None
    assert not s.env.has_mt5_credentials


def test_env_file_and_overrides(
    clean_env: pytest.MonkeyPatch, repo_root: Path, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AURUM_MODE=demo\nMT5_LOGIN=123\nMT5_PASSWORD=pw\nMT5_SERVER=Demo\n"
        f"AURUM_DATA_DIR={tmp_path / 'd'}\n"
    )
    s = load_settings(repo_root, env_file=env_file)
    assert s.mode is Mode.DEMO
    assert s.env.has_mt5_credentials
    assert s.data_dir == tmp_path / "d"
    assert "pw" not in repr(s.env)  # SecretStr masks the password
    clean_env.setenv("AURUM_MODE", "SHADOW")  # process env beats .env
    assert load_settings(repo_root, env_file=env_file).mode is Mode.SHADOW
    blank = EnvSettings(_env_file=None, mt5_login="", mt5_password="", mt5_server=" ")
    assert (blank.mt5_login, blank.mt5_password, blank.mt5_server) == (None, None, None)
    clean_env.setenv("AURUM_MODE", "YOLO")
    with pytest.raises(ConfigError, match="aurum_mode"):
        load_settings(repo_root, env_file=env_file)


def test_readiness_blocks_demo_until_spread_calibrated(app_config: AppConfig) -> None:
    uncalibrated = app_config.model_copy(
        update={"risk": app_config.risk.model_copy(update={"max_spread_points": None})}
    )

    def settings(mode: Mode, config: AppConfig = uncalibrated) -> Settings:
        env = EnvSettings(_env_file=None, aurum_mode=mode)
        return Settings(
            env=env,
            config=config,
            project_root=Path("."),
            config_dir=Path("configs"),
            data_dir=Path("data"),
        )

    assert readiness_issues(settings(Mode.BACKTEST)) == []
    demo = readiness_issues(settings(Mode.DEMO))
    assert any("max_spread_points" in i for i in demo)
    assert any("MT5_LOGIN" in i for i in demo)
    assert any("live_enabled" in i for i in readiness_issues(settings(Mode.LIVE)))
    # Calibrated cap (the committed config): DEMO is still refused until Phase 8.
    calibrated = readiness_issues(settings(Mode.DEMO, app_config))
    assert not any("max_spread_points" in i for i in calibrated)
    assert any("Phase 8" in i for i in calibrated)
