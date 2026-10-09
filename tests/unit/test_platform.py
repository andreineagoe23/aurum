import pytest

from aurum import _platform


@pytest.mark.parametrize(
    ("os_name", "machine", "plat", "expected"),
    [
        ("nt", "ARM64", "win-amd64", True),  # the MT5 VM: x64 Python emulated on ARM
        ("nt", "AMD64", "win-amd64", False),  # native x64 Windows
        ("nt", "ARM64", "win-arm64", False),  # native ARM Python
        ("posix", "arm64", "macosx-14.0-arm64", False),  # the Mac
    ],
)
def test_detects_emulated_x64(
    monkeypatch: pytest.MonkeyPatch, os_name: str, machine: str, plat: str, expected: bool
) -> None:
    monkeypatch.setattr(_platform.os, "name", os_name)
    monkeypatch.setattr(_platform.platform, "machine", lambda: machine)
    monkeypatch.setattr(_platform.sysconfig, "get_platform", lambda: plat)
    assert _platform.is_emulated_x64_on_arm() is expected


def test_selects_compat_runtime_only_when_emulated(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("POLARS_FORCE_PKG", "POLARS_SKIP_CPU_CHECK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_platform, "is_emulated_x64_on_arm", lambda: False)
    _platform.configure_polars_runtime()
    assert "POLARS_FORCE_PKG" not in _platform.os.environ
    monkeypatch.setattr(_platform, "is_emulated_x64_on_arm", lambda: True)
    _platform.configure_polars_runtime()
    assert _platform.os.environ["POLARS_FORCE_PKG"] == "compat"
    assert _platform.os.environ["POLARS_SKIP_CPU_CHECK"] == "1"


def test_polars_imports_and_computes() -> None:
    import polars as pl

    assert pl.DataFrame({"a": [1, 2, 3]})["a"].sum() == 6
