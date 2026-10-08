"""Phase 11 — structural safety: the AI agent layer has NO live surface, checked
TRANSITIVELY (review: a direct-only scan could miss services.alpha importing a
forbidden layer). Walks the full intra-repo import closure from services/intelligence."""
import ast
import os

import services.intelligence

PKG_DIR = os.path.dirname(services.intelligence.__file__)
ROOT = os.path.dirname(os.path.dirname(PKG_DIR))      # .../QUANTOS
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


def _resolve(mod):
    parts = mod.split(".")
    cand = os.path.join(ROOT, *parts) + ".py"
    pkg = os.path.join(ROOT, *parts, "__init__.py")
    return cand if os.path.isfile(cand) else (pkg if os.path.isfile(pkg) else None)


def test_no_live_surface_in_transitive_closure():
    queue = [os.path.join(r, f) for r, _d, fs in os.walk(PKG_DIR) for f in fs if f.endswith(".py")]
    seen, offenders = set(), []
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        for m in _imports(path):
            if m.startswith(FORBIDDEN_PREFIXES) or any(b in m.lower().split(".") for b in FORBIDDEN_BROKER):
                offenders.append((os.path.relpath(path, ROOT), m))
            if m.startswith(("services.", "quantos.")):
                p = _resolve(m)
                if p and p not in seen:
                    queue.append(p)
    assert not offenders, f"agent layer reaches a live/broker surface transitively: {offenders}"
