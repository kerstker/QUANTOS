"""Phase 6 step 13 — structural safety: services/alpha must import NOTHING from
the trading/portfolio/execution layers and no broker SDKs, so the Alpha Factory
carries no live-trading surface. The governance scanner does not catch intra-repo
imports, so this AST test does (critique P1-6)."""
import ast
import os

import services.alpha

ALPHA_DIR = os.path.dirname(services.alpha.__file__)

FORBIDDEN_PREFIXES = (
    "services.trading", "services.portfolio", "services.execution", "services.risk",
)
FORBIDDEN_BROKER = ("ctp", "ctpapi", "vnpy", "ib_insync", "ibapi", "easytrader", "quickfix")


def _imported_modules(path: str) -> list[str]:
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


def _alpha_py_files() -> list[str]:
    out = []
    for root, _dirs, files in os.walk(ALPHA_DIR):
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(root, f))
    return out


def test_alpha_imports_no_trading_portfolio_execution_or_broker():
    offenders = []
    for path in _alpha_py_files():
        for mod in _imported_modules(path):
            low = mod.lower()
            if mod.startswith(FORBIDDEN_PREFIXES) or any(b in low.split(".") for b in FORBIDDEN_BROKER):
                offenders.append((os.path.basename(path), mod))
    assert not offenders, f"services/alpha must not import live-trading layers/SDKs: {offenders}"


def test_generator_is_import_isolated_from_research_internals():
    # the proposal-only generator must not reach into evaluation/backtest/lifecycle
    mods = _imported_modules(os.path.join(ALPHA_DIR, "generator.py"))
    banned = ("services.alpha.evaluator", "services.alpha.lifecycle",
              "services.research.backtest_engine", "services.research.validation")
    assert not [m for m in mods if m.startswith(banned)]
