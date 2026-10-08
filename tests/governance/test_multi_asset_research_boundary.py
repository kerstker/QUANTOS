import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FILES = (
    ROOT / "quantos/contracts/multi_asset.py",
    ROOT / "tools/fx_pit_bundle.py",
    ROOT / "scripts/generate_multi_asset_research_evidence.py",
    ROOT / "services/governance/multi_asset_research_evidence.py",
)


def test_multi_asset_evidence_path_has_no_network_broker_or_order_surface():
    forbidden_roots = {
        "aiohttp",
        "http",
        "httpx",
        "requests",
        "socket",
        "urllib",
        "websocket",
        "websockets",
    }
    forbidden_calls = {
        "place_order",
        "submit_order",
        "withdraw",
        "transfer",
        "connect_broker",
    }

    for path in FILES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        roots: set[str] = set()
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
        assert not roots & forbidden_roots, path
        assert not calls & forbidden_calls, path


def test_governance_verifier_does_not_import_research_or_tools():
    path = ROOT / "services/governance/multi_asset_research_evidence.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not any(name.startswith("services.research") for name in imported)
    assert not any(name.startswith("services.alpha") for name in imported)
    assert not any(name.startswith("tools") for name in imported)
