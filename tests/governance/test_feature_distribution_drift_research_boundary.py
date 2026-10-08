from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "tools" / "feature_distribution_drift_research.py"

FORBIDDEN_IMPORT_ROOTS = {
    "aiohttp",
    "ccxt",
    "httpx",
    "requests",
    "socket",
    "urllib",
    "websocket",
    "websockets",
}
FORBIDDEN_TERMS = {
    "api_key",
    "cancel_order",
    "credential",
    "password",
    "place_order",
    "private_key",
    "secret",
    "submit_order",
    "token",
    "transfer",
    "withdraw",
}


def _source() -> str:
    return TARGET.read_text(encoding="utf-8")


def test_feature_drift_driver_is_offline_and_read_only():
    tree = ast.parse(_source(), filename=str(TARGET))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    roots = {name.split(".", 1)[0] for name in imports}
    assert not roots & FORBIDDEN_IMPORT_ROOTS
    assert not any(name.startswith(("services.execution", "services.trading", "services.risk")) for name in imports)


def test_feature_drift_driver_has_no_secret_or_order_surface():
    source = _source().lower()
    hits = [
        term for term in sorted(FORBIDDEN_TERMS)
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", source)
    ]
    assert hits == []
