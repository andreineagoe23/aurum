"""Process-level platform fixes that must run before third-party imports.

x64 Python on Windows-on-ARM (the MT5 VM) runs under the Prism emulator, which provides
SSE4.2/POPCNT but not AVX2/FMA. polars' default runtime uses AVX2 and dies with an illegal
instruction there. Its CPU-feature check cannot tell, because ``platform.machine()`` reports
``ARM64`` and the check then knows no flags at all and refuses to load.

Fix: on exactly that combination, load polars' "compat" runtime (needs only SSE3..SSE4.2,
POPCNT, CMPXCHG16B, all emulated) and skip the broken check. ``setdefault`` keeps any explicit
user setting. Elsewhere this does nothing.
"""

import os
import platform
import sysconfig


def is_emulated_x64_on_arm() -> bool:
    return (
        os.name == "nt"
        and platform.machine().upper() == "ARM64"
        and sysconfig.get_platform() == "win-amd64"
    )


def configure_polars_runtime() -> None:
    if is_emulated_x64_on_arm():
        os.environ.setdefault("POLARS_FORCE_PKG", "compat")
        os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
