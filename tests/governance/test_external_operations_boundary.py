from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    return roots


def test_domain_event_seal_is_read_only_and_has_no_network_or_broker_boundary():
    path = ROOT / "tools" / "domain_event_seal.py"
    source = path.read_text(encoding="utf-8")
    lowered = source.lower()
    assert not _import_roots(path) & {"httpx", "requests", "socket", "urllib", "ccxt"}
    for forbidden in (
        "submit_order",
        "cancel_order",
        "delete from",
        "drop table",
        "vacuum",
        "--submit",
        "--act",
    ):
        assert forbidden not in lowered
    assert "?mode=ro" in source
    assert 'open("xb")' in source
    assert "PARTIAL_EXTERNAL_WORM_UNVERIFIED" in source


def test_external_heartbeat_has_no_trading_or_credential_persistence_path():
    path = ROOT / "tools" / "external_heartbeat.py"
    source = path.read_text(encoding="utf-8")
    lowered = source.lower()
    assert not _import_roots(path) & {"ccxt", "web3", "binance", "pybit"}
    for forbidden in (
        "submit_order",
        "cancel_order",
        "api_secret",
        "broker",
        "--submit",
        "--act",
    ):
        assert forbidden not in lowered
    assert 'parsed.scheme != "https"' in source
    assert "_NoRedirectHandler" in source
    assert "endpoint_fingerprint" in source
    assert '"endpoint":' not in source


def test_event_authority_commitment_is_a_read_only_service_contract():
    path = ROOT / "services" / "monitoring" / "event_bus.py"
    source = path.read_text(encoding="utf-8")
    method = source[source.index("    def authority_commitment(") :]
    method = method[: method.index("    def subscribe(")]
    lowered = method.lower()
    assert "insert " not in lowered
    assert "update " not in lowered
    assert "delete " not in lowered
    assert "commit(" not in lowered
