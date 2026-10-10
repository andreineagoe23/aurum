"""Import-boundary rules, checked statically over src/aurum (see docs/ARCHITECTURE.md)."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "aurum"


def modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def package_of(path: Path) -> str:
    return ".".join(path.relative_to(SRC.parent).with_suffix("").parts)


def imports(tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_metatrader5_only_imported_in_mt5_adapter() -> None:
    assert len(modules()) > 20  # the scan is not vacuous
    offenders = [
        package_of(p)
        for p in modules()
        if any(m.split(".")[0] == "MetaTrader5" for m in imports(parse(p)))
        and not package_of(p).startswith("aurum.adapters.mt5")
    ]
    assert offenders == []


def test_metatrader5_never_imported_at_module_level() -> None:
    for p in modules():
        tree = parse(p)
        module_level = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
        names = imports(ast.Module(body=module_level, type_ignores=[]))
        assert "MetaTrader5" not in names, package_of(p)


NO_BROKER_ACCESS = ("aurum.adapters", "aurum.execution", "aurum.apps", "aurum.ports.broker")


RULES: list[tuple[str, tuple[str, ...]]] = [
    ("aurum.core", ("aurum.",)),  # core depends on nothing else in aurum
    ("aurum.strategies", NO_BROKER_ACCESS),
    ("aurum.features", NO_BROKER_ACCESS),
    ("aurum.risk", ("aurum.adapters", "aurum.apps", "aurum.strategies")),
    ("aurum.backtest", ("aurum.adapters.mt5", "aurum.apps")),
    # The engine is shared by BACKTEST/SHADOW/DEMO: it may not know about the simulator.
    ("aurum.execution", ("aurum.adapters", "aurum.apps", "aurum.backtest")),
    ("aurum.sessions", ("aurum.adapters", "aurum.execution", "aurum.apps")),
]


def test_dependency_rules() -> None:
    for p in modules():
        name = package_of(p)
        for package, forbidden in RULES:
            if not name.startswith(package):
                continue
            for imported in imports(parse(p)):
                if package == "aurum.core" and imported.startswith("aurum.core"):
                    continue
                assert not imported.startswith(forbidden), f"{name} imports {imported}"


def test_whole_package_imports_without_metatrader5() -> None:
    import importlib
    import sys

    for p in modules():
        name = package_of(p).removesuffix(".__init__")
        importlib.import_module(name)
    if sys.platform != "win32":
        assert "MetaTrader5" not in sys.modules
