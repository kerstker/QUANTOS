from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "services" / "data" / "distribution_drift.py"

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


def test_distribution_drift_kernel_has_no_io_network_or_execution_import() -> None:
    tree = ast.parse(_source(), filename=str(TARGET))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    roots = {name.split(".", 1)[0] for name in imports}
    assert not roots & FORBIDDEN_IMPORT_ROOTS
    assert not any(
        name.startswith(("services.execution", "services.trading", "services.risk"))
        for name in imports
    )


def test_distribution_drift_kernel_exposes_no_secret_or_order_surface() -> None:
    source = _source().lower()
    hits = []
    for term in sorted(FORBIDDEN_TERMS):
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", source):
            hits.append(term)

    assert hits == []
