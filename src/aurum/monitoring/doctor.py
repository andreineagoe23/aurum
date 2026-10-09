"""Environment checks behind ``aurum doctor``. Pure functions returning results; no printing."""

import platform
import sys
import sysconfig
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from aurum.adapters.mt5.loader import is_available as mt5_available
from aurum.core.errors import ConfigError
from aurum.settings import Settings, find_project_root, load_settings, readiness_issues


class Status(StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str


def check_python() -> CheckResult:
    v = sys.version_info
    version = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) == (3, 12):
        return CheckResult("python", Status.OK, version)
    if (v.major, v.minor) < (3, 12):
        return CheckResult("python", Status.FAIL, f"{version} (need 3.12)")
    return CheckResult("python", Status.WARN, f"{version} (project targets 3.12)")


def check_architecture(os_name: str = sys.platform, plat: str | None = None) -> CheckResult:
    """MetaTrader5 ships ``win_amd64`` wheels only, so Windows needs an x64 interpreter.

    Uses ``sysconfig.get_platform()`` (what the interpreter was built for), not
    ``platform.machine()``: on Windows ARM the latter reports ``ARM64`` even for emulated x64.
    """
    plat = plat or sysconfig.get_platform()
    detail = f"{plat}, {64 if sys.maxsize > 2**32 else 32}-bit interpreter"
    if os_name == "win32" and plat != "win-amd64":
        return CheckResult(
            "architecture", Status.FAIL, f"{detail}: MetaTrader5 needs x64 Python (win-amd64)"
        )
    return CheckResult("architecture", Status.OK, detail)


def check_os() -> CheckResult:
    return CheckResult("os", Status.OK, platform.platform())


def check_mt5_package(os_name: str = sys.platform) -> CheckResult:
    if os_name != "win32":
        return CheckResult("mt5 package", Status.SKIP, "Windows-only; expected absent on this OS")
    if mt5_available():
        return CheckResult("mt5 package", Status.OK, "MetaTrader5 importable")
    return CheckResult("mt5 package", Status.FAIL, "MetaTrader5 not installed in this venv")


def check_data_dir(data_dir: Path) -> CheckResult:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / f".doctor-{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult("data dir", Status.FAIL, f"{data_dir}: {exc}")
    return CheckResult("data dir", Status.OK, f"{data_dir} writable")


def check_settings(settings: Settings, os_name: str = sys.platform) -> list[CheckResult]:
    results = [
        CheckResult(
            "config",
            Status.OK,
            f"{settings.config_dir} ({len(settings.config.strategies)} strategy configs)",
        )
    ]
    promotion = settings.config.promotion
    results.append(
        CheckResult("live guard", Status.WARN, "promotion.live_enabled is TRUE")
        if promotion.live_enabled
        else CheckResult("live guard", Status.OK, "live trading disabled (promotion.yaml)")
    )
    results.append(CheckResult("mode", Status.OK, str(settings.mode)))
    if os_name == "win32":
        creds = settings.env.has_mt5_credentials
        results.append(
            CheckResult(
                "mt5 credentials",
                Status.OK if creds else Status.WARN,
                "set in environment/.env" if creds else "MT5_LOGIN/PASSWORD/SERVER missing",
            )
        )
    for issue in readiness_issues(settings):
        results.append(CheckResult("readiness", Status.FAIL, issue))
    if (settings.data_dir / "KILL").exists():
        results.append(CheckResult("kill switch", Status.WARN, "KILL file present: trading locked"))
    return results


def run_checks(project_root: Path | None = None) -> list[CheckResult]:
    results = [check_python(), check_architecture(), check_os(), check_mt5_package()]
    try:
        settings = load_settings(project_root or find_project_root())
    except ConfigError as exc:
        results.append(CheckResult("config", Status.FAIL, str(exc)))
        return results
    results.extend(check_settings(settings))
    results.append(check_data_dir(settings.data_dir))
    return results
