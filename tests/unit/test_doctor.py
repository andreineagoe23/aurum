"""Unit tests for doctor checks."""

import sysconfig
from pathlib import Path

from aurum.monitoring.doctor import (
    Status,
    check_architecture,
    check_data_dir,
    check_mt5_package,
    check_python,
)


def test_architecture() -> None:
    assert check_architecture(os_name="win32", plat="win-arm64").status is Status.FAIL
    assert check_architecture(os_name="win32", plat="win32").status is Status.FAIL
    assert check_architecture(os_name="win32", plat="win-amd64").status is Status.OK
    assert check_architecture(os_name="darwin", plat="macosx-11.0-arm64").status is Status.OK


def test_architecture_defaults_to_interpreter_build_platform() -> None:
    assert check_architecture().detail.startswith(sysconfig.get_platform())


def test_mt5_package_skipped_off_windows() -> None:
    assert check_mt5_package(os_name="darwin").status is Status.SKIP


def test_data_dir_ok(tmp_path: Path) -> None:
    assert check_data_dir(tmp_path).status is Status.OK


def test_data_dir_under_file_fails(tmp_path: Path) -> None:
    f = tmp_path / "afile"
    f.write_text("x", encoding="utf-8")
    assert check_data_dir(f / "sub").status is Status.FAIL


def test_python_ok() -> None:
    assert check_python().status is Status.OK
