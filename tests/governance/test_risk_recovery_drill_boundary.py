import ast
from pathlib import Path


def test_risk_recovery_drill_has_no_network_broker_or_submission_boundary():
    path = Path("tools/risk_recovery_drill.py")
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
    assert not imports & {
        "aiohttp",
        "ccxt",
        "httpx",
        "requests",
        "socket",
        "urllib",
        "websockets",
    }
    lowered = source.lower()
    assert "submit_order" not in lowered
    assert "cancel_order" not in lowered
    assert "live_trading_enabled" not in lowered
    assert "real_broker_enabled" not in lowered
    assert "allow_order_submission" not in lowered
    assert "arm_and_record" in source
    assert "save_kill_switch_state(" not in source
