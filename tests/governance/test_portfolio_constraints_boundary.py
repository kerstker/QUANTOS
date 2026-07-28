from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "services" / "portfolio" / "portfolio_constraints.py"

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


def test_portfolio_constraint_kernel_is_offline_and_execution_agnostic() -> None:
    tree = ast.parse(TARGET.read_text(encoding="utf-8"), filename=str(TARGET))
    imports: set[str] = set()
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


def test_portfolio_constraint_kernel_has_no_network_or_order_surface() -> None:
    source = TARGET.read_text(encoding="utf-8").lower()
    assert "submit_order" not in source
    assert "place_order" not in source
    assert "api_key" not in source
    assert "credential" not in source
