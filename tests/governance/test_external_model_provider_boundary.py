import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PURE_PATHS = (
    ROOT / "quantos/contracts/external_model_provider.py",
    ROOT / "services/intelligence/provider_selection.py",
    ROOT / "services/intelligence/external_provider_runtime.py",
    ROOT / "services/governance/external_model_provider_evidence.py",
)
NETWORK_ROOTS = {
    "aiohttp",
    "http",
    "httpx",
    "requests",
    "socket",
    "urllib",
    "websocket",
    "websockets",
}
FORBIDDEN_EXTERNAL_IMPORTS = {
    "services.trading",
    "services.execution",
    "services.deployment",
    "services.risk.pre_trade",
    "live",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_network_is_confined_to_tools_adapter():
    for path in PURE_PATHS:
        roots = {name.split(".", 1)[0] for name in _imports(path)}
        assert not roots & NETWORK_ROOTS, path

    adapter = ROOT / "tools/external_model_provider.py"
    roots = {name.split(".", 1)[0] for name in _imports(adapter)}
    assert "http" in roots
    assert not any(
        imported == forbidden or imported.startswith(forbidden + ".")
        for imported in _imports(adapter)
        for forbidden in FORBIDDEN_EXTERNAL_IMPORTS
    )


def test_governance_verifier_does_not_import_intelligence_runtime():
    verifier = ROOT / "services/governance/external_model_provider_evidence.py"
    assert not any(
        imported == "services.intelligence" or imported.startswith("services.intelligence.")
        for imported in _imports(verifier)
    )


def test_provider_registry_is_offline_default_and_contains_no_secret():
    path = ROOT / "config/model_provider_registry.json"
    raw = path.read_text(encoding="utf-8")
    document = json.loads(raw)

    assert document["default_provider_id"] == "offline_registered"
    assert document["selection_env"] == "QUANTOS_MODEL_PROVIDER"
    external = next(item for item in document["providers"] if item["provider_id"] == "sushuo_gpt_5_6_sol")
    assert external["endpoint"] == "https://vip.sushuo.biz/v1/chat/completions"
    assert external["model"] == "gpt-5.6-sol"
    assert external["api_key_env"] == "NEW_API_KEY"
    assert "Bearer " not in raw
    assert "api_key" not in {key.lower() for key in external}
    assert "key.txt" not in raw


def test_external_provider_sources_have_no_order_or_credential_authority():
    paths = (*PURE_PATHS, ROOT / "tools/external_model_provider.py")
    forbidden_calls = {
        "place_order",
        "submit_order",
        "send_order",
        "approve",
        "reset_kill_switch",
    }
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        called = {
            node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        called.update(
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        )
        assert not called & forbidden_calls, path
