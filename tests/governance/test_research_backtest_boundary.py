import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "services/research"


def _resolved_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = list(path.parent.relative_to(ROOT).parts)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            if node.module:
                found.append((node.lineno, node.module))
            continue
        parents = node.level - 1
        if parents > len(package):
            continue
        parts = package[: len(package) - parents]
        if node.module:
            parts.extend(node.module.split("."))
        if parts:
            found.append((node.lineno, ".".join(parts)))
    return found


def test_validation_orchestration_does_not_import_concrete_backtest_engine():
    offenders = []
    for path in sorted((RESEARCH / "validation").rglob("*.py")):
        for line, module in _resolved_imports(path):
            if module == "services.research.backtest_engine" or module.startswith(
                "services.research.backtest_engine."
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{line}:{module}")
    assert not offenders, offenders


def test_backtest_execution_port_is_a_leaf_interface():
    path = RESEARCH / "ports/backtest.py"
    forbidden = []
    for line, module in _resolved_imports(path):
        if module.startswith(("services.research.backtest_engine", "services.data", "tools", "live")):
            forbidden.append(f"{path.relative_to(ROOT)}:{line}:{module}")
    assert not forbidden, forbidden


def test_concrete_backtest_engine_is_reached_only_inside_its_adapter_package():
    offenders = []
    for path in sorted(RESEARCH.rglob("*.py")):
        if "tests" in path.parts or "backtest_engine" in path.parts:
            continue
        for line, module in _resolved_imports(path):
            if module == "services.research.backtest_engine.engine" or module.endswith(
                ".backtest_engine.engine"
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{line}:{module}")
    assert not offenders, offenders
