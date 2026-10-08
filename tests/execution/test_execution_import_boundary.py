"""Phase 10 — structural safety: services/execution imports no broker SDK
(no live execution surface)."""
import ast
import os

import services.execution

PKG_DIR = os.path.dirname(services.execution.__file__)
FORBIDDEN_BROKER = ("ctp", "ctpapi", "vnpy", "ib_insync", "ibapi", "easytrader", "quickfix", "fix")


def _imports(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


def test_execution_has_no_broker_sdk():
    offenders = []
    for root, _d, files in os.walk(PKG_DIR):
        for f in files:
            if f.endswith(".py"):
                for m in _imports(os.path.join(root, f)):
                    if any(b in m.lower().split(".") for b in FORBIDDEN_BROKER):
                        offenders.append((f, m))
    assert not offenders, f"services/execution must not import a broker SDK: {offenders}"
