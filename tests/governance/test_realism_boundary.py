"""治理:realism/ 纯离线——无网络/券商 SDK import(与 services 全树无网络不变量一致)。"""
import ast
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DIR = os.path.join(_ROOT, "services", "research", "realism")
_FORBIDDEN = {"ccxt", "binance", "ib_insync", "urllib", "socket", "requests", "httpx",
              "aiohttp", "websocket", "websockets", "ftplib", "http"}


def _roots(path):
    t = ast.parse(open(path, encoding="utf-8").read())
    r = set()
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            for a in n.names:
                r.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            r.add(n.module.split(".")[0])
    return r


def test_realism_has_no_network_or_broker_imports():
    for fn in os.listdir(_DIR):
        if fn.endswith(".py"):
            bad = _roots(os.path.join(_DIR, fn)) & _FORBIDDEN
            assert not bad, f"FORBIDDEN import in realism/{fn}: {sorted(bad)}"
