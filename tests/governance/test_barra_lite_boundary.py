"""Governance checks for Barra-lite diagnostics safety boundaries."""
from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_SERVICE_TARGET = Path("services/portfolio/barra_lite.py")
_OPTIONAL_TOOL_TARGET = Path("tools/barra_lite_diagnostics.py")

_FORBIDDEN_IMPORT_ROOTS = {
    "aiohttp",
    "alpaca",
    "alpaca_trade_api",
    "binance",
    "ccxt",
    "coinbase",
    "ctp",
    "fix",
    "httpx",
    "ib_insync",
    "ibapi",
    "kraken",
    "krakenex",
    "oandapyv20",
    "pkcs11",
    "pypkcs11",
    "quickfix",
    "requests",
    "socket",
    "urllib",
    "vnpy",
    "websocket",
    "websockets",
}

_SENSITIVE_SOURCE_TERMS = {
    "api_key",
    "cancel_order",
    "create_order",
    "password",
    "place_order",
    "private_key",
    "secret_key",
    "submit_order",
    "token",
    "transfer",
    "withdraw",
}

_FORBIDDEN_SERVICE_IMPORT_PREFIXES = (
    "services.trading",
    "services.execution",
    "services.risk.pre_trade",
)


def _read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _existing_target_paths() -> list[Path]:
    service = _ROOT / _SERVICE_TARGET
    if not service.exists():
        pytest.fail(f"required Barra-lite service target is missing: {_SERVICE_TARGET}")

    targets = [service]
    optional_tool = _ROOT / _OPTIONAL_TOOL_TARGET
    if optional_tool.exists():
        targets.append(optional_tool)
    return targets


def _absolute_import_names(path: Path) -> set[str]:
    tree = ast.parse(_read_source(path), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imports.add(node.module)
            imports.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imports


def _import_roots(path: Path) -> set[str]:
    return {name.split(".", 1)[0].lower() for name in _absolute_import_names(path)}


def _source_term_hits(path: Path) -> list[str]:
    source = _read_source(path).lower()
    hits = []
    for term in sorted(_SENSITIVE_SOURCE_TERMS):
        pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
        if re.search(pattern, source):
            hits.append(term)
    return hits


def test_barra_lite_targets_import_no_broker_exchange_network_or_hsm_sdks():
    for path in _existing_target_paths():
        roots = _import_roots(path)
        forbidden = sorted(roots & _FORBIDDEN_IMPORT_ROOTS)
        assert not forbidden, f"{path.relative_to(_ROOT)} imports forbidden roots: {forbidden}"


def test_barra_lite_targets_expose_no_order_transfer_or_credential_surface():
    for path in _existing_target_paths():
        hits = _source_term_hits(path)
        assert not hits, f"{path.relative_to(_ROOT)} contains sensitive source terms: {hits}"


def test_barra_lite_service_does_not_import_execution_trading_or_pre_trade_gate():
    imports = _absolute_import_names(_ROOT / _SERVICE_TARGET)
    forbidden = sorted(
        name
        for name in imports
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in _FORBIDDEN_SERVICE_IMPORT_PREFIXES)
    )
    assert not forbidden, f"{_SERVICE_TARGET} imports forbidden runtime boundary modules: {forbidden}"


def test_barra_lite_tool_writes_only_gitignored_cache_snapshot():
    tool = importlib.import_module("tools.barra_lite_diagnostics")
    out_path = Path(tool._OUT)

    assert out_path.as_posix() == "data/cache/barra_lite_snapshot.json"
    gitignore = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/cache/" in gitignore
