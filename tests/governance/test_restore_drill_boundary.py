import ast
from pathlib import Path


def test_restore_drill_has_no_network_broker_or_active_state_write_boundary():
    path = Path("tools/restore_drill.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not imports & {"requests", "httpx", "urllib", "socket", "ccxt"}
    lowered = source.lower()
    assert "submit_order" not in lowered
    assert "cancel_order" not in lowered
    assert "data/cache" not in lowered
    assert "live_trading_enabled" not in lowered

