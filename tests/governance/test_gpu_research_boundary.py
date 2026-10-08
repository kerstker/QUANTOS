"""Governance: CUDA is an offline research capability, never an execution path."""

import ast
import os


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SERVICES = os.path.join(_ROOT, "services")


def _roots(path: str) -> set[str]:
    tree = ast.parse(open(path, encoding="utf-8").read())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_xgboost_import_is_isolated_to_research_ml():
    offenders = []
    for dirpath, _dirs, files in os.walk(_SERVICES):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            if "xgboost" in _roots(path) and os.path.normpath(os.path.join(_SERVICES, "research", "ml")) not in os.path.normpath(path):
                offenders.append(os.path.relpath(path, _ROOT))
    assert offenders == []


def test_research_ml_gpu_modules_have_no_order_or_broker_imports():
    ml_root = os.path.join(_SERVICES, "research", "ml")
    forbidden = {"ccxt", "binance", "coinbase", "ib_insync", "alpaca", "quickfix", "ctp", "vnpy"}
    offenders = []
    for name in ("accelerator.py", "stacker.py", "walk_forward_stack.py"):
        roots = _roots(os.path.join(ml_root, name))
        if roots & forbidden:
            offenders.append((name, sorted(roots & forbidden)))
    assert offenders == []

