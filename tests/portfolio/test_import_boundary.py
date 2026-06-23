"""Phase 7 — structural safety: services/portfolio imports nothing from the
trading/execution layers and no broker SDKs (no live surface)."""
import ast
import os

import services.portfolio

PORTFOLIO_DIR = os.path.dirname(services.portfolio.__file__)
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


def test_portfolio_has_no_live_trading_surface():
    offenders = []
    for root, _d, files in os.walk(PORTFOLIO_DIR):
        for f in files:
            if f.endswith(".py"):
                for m in _imports(os.path.join(root, f)):
                    if m.startswith(FORBIDDEN_PREFIXES) or any(b in m.lower().split(".") for b in FORBIDDEN_BROKER):
                        offenders.append((f, m))
    assert not offenders, f"services/portfolio must not import live layers/SDKs: {offenders}"
