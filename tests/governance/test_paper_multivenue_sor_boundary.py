import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    ROOT / "tools" / "paper_multivenue_sor.py",
    ROOT / "services" / "data" / "cross_venue_order_book_store.py",
)
FORBIDDEN_IMPORTS = {
    "binance",
    "ccxt",
    "pybit",
    "requests",
    "websocket",
}
FORBIDDEN_TEXT = {
    "api_key",
    "api_secret",
    "private_key",
    "submit_order",
    "place_order",
}


def test_public_paper_sor_has_no_broker_sdk_or_credential_surface():
    for path in TARGETS:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            str(node.module).split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        assert imported.isdisjoint(FORBIDDEN_IMPORTS)
        lowered = source.lower()
        for token in FORBIDDEN_TEXT:
            assert token not in lowered
