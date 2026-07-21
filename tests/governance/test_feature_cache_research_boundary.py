from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "tools" / "feature_cache_research.py"
FORBIDDEN_IMPORT_ROOTS = {
    "aiohttp",
    "ccxt",
    "httpx",
    "requests",
    "socket",
    "urllib",
    "websocket",
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


def test_feature_cache_driver_is_offline_and_not_an_execution_surface():
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".")[0])
    assert not (set(imported) & FORBIDDEN_IMPORT_ROOTS)
    lowered = source.lower()
    hits = [
        term
        for term in sorted(FORBIDDEN_TERMS)
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered)
    ]
    assert hits == []
    assert "services.execution" not in source
    assert "services.trading" not in source
    assert "services.risk" not in source
