"""Unit tests for doctor checks."""

import sysconfig
from datetime import date
from pathlib import Path

import pytest

from aurum.monitoring.doctor import (
    CheckResult,
    Status,
    check_architecture,
    check_data_dir,
    check_mt5_package,
    check_python,
    check_settings,
)
from aurum.settings import load_settings


def test_architecture() -> None:
    assert check_architecture(os_name="win32", plat="win-arm64").status is Status.FAIL
    assert check_architecture(os_name="win32", plat="win32").status is Status.FAIL
    assert check_architecture(os_name="win32", plat="win-amd64").status is Status.OK
    assert check_architecture(os_name="darwin", plat="macosx-11.0-arm64").status is Status.OK
    assert check_architecture().detail.startswith(sysconfig.get_platform())


def test_environment_checks(tmp_path: Path) -> None:
    assert check_python().status is Status.OK
    assert check_mt5_package(os_name="darwin").status is Status.SKIP
    assert check_data_dir(tmp_path).status is Status.OK
    f = tmp_path / "afile"
    f.write_text("x", encoding="utf-8")
    assert check_data_dir(f / "sub").status is Status.FAIL


def test_news_calendar_and_kill_switch_checks(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AURUM_DATA_DIR", str(tmp_path))
    settings = load_settings(repo_root, env_file=tmp_path / "none.env")

    def by_name(today: date) -> dict[str, CheckResult]:
        return {r.name: r for r in check_settings(settings, os_name="darwin", today=today)}

    assert by_name(date(2026, 10, 9))["news calendar"].status is Status.OK  # Dec 9 listed
    late = by_name(date(2026, 10, 11))["news calendar"]  # 60 days later is past Dec 9
    assert late.status is Status.WARN
    assert "add the next ones" in late.detail
    assert "kill switch" not in by_name(date(2026, 10, 9))
    (tmp_path / "KILL").write_text("test", encoding="utf-8")
    assert by_name(date(2026, 10, 9))["kill switch"].status is Status.WARN
