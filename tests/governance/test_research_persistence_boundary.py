import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "services/research"

CORE_MODULES = (
    RESEARCH / "experiment_registry/registry.py",
    RESEARCH / "run_registry/registry.py",
    RESEARCH / "paper_trading/advance.py",
    RESEARCH / "paper_trading/health.py",
    RESEARCH / "paper_trading/forward.py",
)

SQLITE_ADAPTERS = {
    "services/research/experiment_registry/store.py",
    "services/research/run_registry/store.py",
    "services/research/paper_trading/store.py",
    "services/research/paper_trading/forward_store.py",
    "services/research/paper_trading/attested_store.py",
    "services/research/alpha_family_registry.py",
}


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]


def test_registry_and_paper_core_do_not_import_concrete_stores():
    offenders = []
    for path in CORE_MODULES:
        for node in _imports(path):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                modules = [node.module or ""]
            if any(
                module == "sqlite3"
                or module.endswith(".store")
                or module == "store"
                or module.endswith(".forward_store")
                or module == "forward_store"
                for module in modules
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{modules}")
    assert not offenders, offenders


def test_sqlite_is_confined_to_declared_persistence_adapters():
    actual = set()
    for path in sorted(RESEARCH.rglob("*.py")):
        if "tests" in path.parts:
            continue
        for node in _imports(path):
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if "sqlite3" in modules:
                actual.add(path.relative_to(ROOT).as_posix())
    assert actual == SQLITE_ADAPTERS


def test_persistence_ports_are_leaf_interfaces():
    path = RESEARCH / "ports/persistence.py"
    modules = []
    for node in _imports(path):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        else:
            modules.append(node.module or "")
    assert not any(module == "sqlite3" or ".store" in module or module.endswith("forward_store") for module in modules)
