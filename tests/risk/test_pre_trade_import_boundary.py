"""Phase 8 — structural safety: services/risk/pre_trade imports nothing from the
trading/execution layers and no broker SDK (the gate has no live surface)."""
import ast
import os

import services.risk.pre_trade

PKG_DIR = os.path.dirname(services.risk.pre_trade.__file__)
FORBIDDEN_PREFIXES = ("services.trading", "services.execution")
FORBIDDEN_BROKER = ("ctp", "ctpapi", "vnpy", "ib_insync", "ibapi", "easytrader", "quickfix")


def _imports(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


def test_pre_trade_gate_has_no_live_surface():
    offenders = []
    for root, _d, files in os.walk(PKG_DIR):
        for f in files:
            if f.endswith(".py"):
                for m in _imports(os.path.join(root, f)):
                    if m.startswith(FORBIDDEN_PREFIXES) or any(b in m.lower().split(".") for b in FORBIDDEN_BROKER):
                        offenders.append((f, m))
    assert not offenders, f"pre_trade must not import live layers/SDKs: {offenders}"
