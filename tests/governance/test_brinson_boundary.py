"""Governance checks for the Brinson attribution primitive boundary."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_TARGET = Path("services/portfolio/brinson.py")

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

_FORBIDDEN_IMPORT_PREFIXES = (
    "services.trading",
    "services.execution",
    "services.risk.pre_trade",
)


def _target_path() -> Path:
    path = _ROOT / _TARGET
    if not path.exists():
        pytest.fail(f"required Brinson attribution target is missing: {_TARGET}")
    return path


def _read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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


def test_brinson_imports_no_broker_exchange_network_or_hsm_sdks():
    roots = {name.split(".", 1)[0].lower() for name in _absolute_import_names(_target_path())}
    forbidden = sorted(roots & _FORBIDDEN_IMPORT_ROOTS)

    assert not forbidden, f"{_TARGET} imports forbidden roots: {forbidden}"


def test_brinson_exposes_no_order_transfer_or_credential_surface():
    source = _read_source(_target_path()).lower()
    hits = []
    for term in sorted(_SENSITIVE_SOURCE_TERMS):
        pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
        if re.search(pattern, source):
            hits.append(term)

    assert not hits, f"{_TARGET} contains sensitive source terms: {hits}"


def test_brinson_does_not_import_execution_trading_or_pre_trade_gate():
    imports = _absolute_import_names(_target_path())
    forbidden = sorted(
        name
        for name in imports
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in _FORBIDDEN_IMPORT_PREFIXES)
    )

    assert not forbidden, f"{_TARGET} imports forbidden runtime boundary modules: {forbidden}"
