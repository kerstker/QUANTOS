"""Keep exact-v3 activation outside every runtime research surface."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ACTIVATION_MODULE = (
    ROOT
    / "services"
    / "research"
    / "experiment_registry"
    / "family_schema_v3_activation.py"
)
OFFLINE_ENTRYPOINT = ROOT / "tools" / "family_schema_v3_activate.py"
ACTIVATION_IMPORT = (
    "services.research.experiment_registry.family_schema_v3_activation"
)
ACTIVATION_SYMBOL = "activate_family_schema_v3"
ACTIVATION_MODULE_NAME = "family_schema_v3_activation"
PRODUCTION_ROOTS = tuple(
    ROOT / name for name in ("apps", "live", "quantos", "services", "tools")
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _activation_references(path: Path) -> tuple[str, ...]:
    references: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == ACTIVATION_IMPORT:
                    references.append(f"import:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == ACTIVATION_IMPORT or module.endswith(
                "family_schema_v3_activation"
            ):
                references.append(f"from:{module}")
            for alias in node.names:
                if alias.name in (ACTIVATION_MODULE_NAME, ACTIVATION_SYMBOL):
                    references.append(f"symbol:{alias.name}")
        elif isinstance(node, ast.Constant) and type(node.value) is str:
            if node.value in (ACTIVATION_IMPORT, ACTIVATION_SYMBOL):
                references.append(f"dynamic:{node.value}")
    return tuple(references)


@pytest.mark.parametrize(
    "source",
    (
        "from services.research.experiment_registry import family_schema_v3_activation\n",
        "from . import family_schema_v3_activation\n",
    ),
)
def test_activation_scanner_rejects_parent_package_import_forms(
    tmp_path: Path,
    source: str,
) -> None:
    fixture = tmp_path / "forbidden_import.py"
    fixture.write_text(source, encoding="utf-8")

    assert _activation_references(fixture)


def test_only_the_designated_offline_surface_can_reach_activation() -> None:
    allowed = {ACTIVATION_MODULE.resolve(), OFFLINE_ENTRYPOINT.resolve()}
    violations: dict[str, tuple[str, ...]] = {}

    for root in PRODUCTION_ROOTS:
        for path in root.rglob("*.py"):
            if path.resolve() in allowed:
                continue
            references = _activation_references(path)
            if references:
                violations[path.relative_to(ROOT).as_posix()] = references

    assert not violations


def test_runtime_store_port_exposes_no_activation_or_family_migration() -> None:
    port = ROOT / "services" / "research" / "ports" / "persistence.py"
    store = (
        ROOT
        / "services"
        / "research"
        / "experiment_registry"
        / "store.py"
    )

    port_tree = _tree(port)
    experiment_store_port = next(
        node
        for node in port_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ExperimentStorePort"
    )
    method_names = {
        node.name
        for node in experiment_store_port.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert not any("activate" in name or "migrate" in name for name in method_names)
    assert not _activation_references(port)
    assert not _activation_references(store)


def test_activation_module_is_offline_and_has_no_execution_or_agent_dependency() -> None:
    imported_roots: set[str] = set()
    for node in ast.walk(_tree(ACTIVATION_MODULE)):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module)

    forbidden = (
        "services.console",
        "services.execution",
        "services.intelligence",
        "services.research_runner",
        "services.trading",
        "live",
        "tools",
    )
    assert not {
        module
        for module in imported_roots
        if any(module == root or module.startswith(f"{root}.") for root in forbidden)
    }
