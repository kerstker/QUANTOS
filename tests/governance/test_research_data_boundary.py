import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _absolute_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def test_research_domain_does_not_import_data_implementations():
    offenders = []
    for path in sorted((ROOT / "services/research").rglob("*.py")):
        if "tests" in path.parts:
            continue
        bad = sorted(
            module
            for module in _absolute_imports(path)
            if module == "services.data" or module.startswith("services.data.")
        )
        if bad:
            offenders.append(f"{path.relative_to(ROOT)}: {bad}")
    assert not offenders, offenders


def test_data_catalog_runtime_fails_closed_and_cannot_be_replaced():
    from services.console.api.data_port import (
        DataCatalogRuntime,
        DataCatalogUnavailable,
    )

    class Port:
        data_backend = "FAKE"

        def enumerate_real_datasets(self):
            return []

        def dataset_content_hash(self, dataset_id, version):
            return f"{dataset_id}:{version}"

        def verify_point_in_time_safe(self, _dataset_id):
            return True

    runtime = DataCatalogRuntime()
    with pytest.raises(DataCatalogUnavailable, match="services.console.main"):
        runtime.require()

    port = Port()
    runtime.configure(port)
    runtime.configure(port)
    assert runtime.require() is port
    with pytest.raises(RuntimeError, match="already configured"):
        runtime.configure(Port())


def test_intelligence_composition_owns_the_data_adapter():
    source = (ROOT / "services/intelligence/research_session.py").read_text(encoding="utf-8")
    assert "adapt_research_market_data" in source
    assert "services.data.research_adapter" in source


def test_data_adapter_does_not_import_research():
    path = ROOT / "services/data/research_adapter.py"
    assert not any(
        module == "services.research" or module.startswith("services.research.") for module in _absolute_imports(path)
    )
