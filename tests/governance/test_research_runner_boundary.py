"""治理:research_runner 是唯一可变面,机器钉死它"只研究、绝不碰交易"。

AST 扫描 research_runner 包:无券商/交易所 SDK、不 import services.trading、无下单/转账调用;
且 RUN_KINDS 白名单每个入口都指向 services/tools 的研究函数(不含 trading/order),参数 schema 受类型约束。
镜像 tests/governance/test_crypto_data_boundary.py 的写法。"""
import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Make `services` importable from ANY cwd/rootdir so the whitelist tests below truly run their boundary
# assertions instead of silently failing/skipping with ModuleNotFoundError (was a portability P2).
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_PKG = os.path.join(_ROOT, "services", "research", "research_runner")
_FORBIDDEN_SDK = {"ccxt", "binance", "coinbase", "cbpro", "krakenex", "kraken", "ib_insync",
                  "ibapi", "alpaca", "alpaca_trade_api", "oandapyV20", "fix", "quickfix", "ctp", "vnpy"}
_FORBIDDEN_CALLS = ["create_order", "place_order", "submit_order", "cancel_order", "create_market_order",
                    "create_limit_order", "fapiPrivate", "sapiPost", ".withdraw(", "create_withdrawal",
                    "transfer("]


def _import_roots(path: str) -> set:
    tree = ast.parse(open(path, encoding="utf-8").read())
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            roots |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            roots.add(n.module.split(".")[0])
    return roots


def _pyfiles() -> list:
    return [os.path.join(_PKG, f) for f in os.listdir(_PKG) if f.endswith(".py")]


def test_runner_no_broker_sdk_or_trading_import_or_order_call():
    for p in _pyfiles():
        roots = _import_roots(p)
        assert not (roots & _FORBIDDEN_SDK), f"broker/exchange SDK in {os.path.basename(p)}: {sorted(roots & _FORBIDDEN_SDK)}"
        src = open(p, encoding="utf-8").read()
        assert "services.trading" not in src, f"trading-path import in {os.path.basename(p)}"
        hits = [c for c in _FORBIDDEN_CALLS if c in src]
        assert not hits, f"order/transfer call in {os.path.basename(p)}: {hits}"


def test_whitelist_entrypoints_are_research_only():
    from services.research.research_runner.run_kinds import RUN_KINDS
    assert RUN_KINDS, "whitelist must be non-empty"
    for kind, spec in RUN_KINDS.items():
        root = spec.entrypoint.split(":")[0].split(".")[0]
        assert root in {"services", "tools"}, f"{kind}: entrypoint root {root!r} not research"
        assert "trading" not in spec.entrypoint and "order" not in spec.entrypoint, f"{kind}: non-research entrypoint"
        assert isinstance(spec.param_schema, dict), f"{kind}: param schema must be a typed dict"


def test_whitelist_is_subset_of_registry_allowed_kinds():
    # the runner can only produce kinds the registry will accept (defence in depth, kept in sync)
    from services.research.research_runner.run_kinds import RUN_KINDS
    from services.research.run_registry.contract import ALLOWED_KINDS
    assert set(RUN_KINDS) <= ALLOWED_KINDS, f"runner kinds not in registry whitelist: {set(RUN_KINDS) - ALLOWED_KINDS}"


# --- 2026-07-05 安全审查 MEDIUM 修复:/cancel 与 /run 同等问责要求 ---
# --- 2026-07-06 续:/cancel 与 /run 同等本机共享密钥认证要求 ---

def test_cancel_requires_operator_header():
    from fastapi.testclient import TestClient
    from services.research.research_runner import main as m
    client = TestClient(m.app)
    r = client.post("/cancel", json={"run_id": "run_nonexistent"},
                    headers={"X-Runner-Token": m._RUNNER_TOKEN})
    assert r.status_code == 400


def test_cancel_accepts_operator_header_and_records_it():
    from fastapi.testclient import TestClient
    from services.research.research_runner import main as m
    client = TestClient(m.app)
    r = client.post("/cancel", json={"run_id": "run_nonexistent"},
                    headers={"X-Operator": "alice", "X-Runner-Token": m._RUNNER_TOKEN})
    assert r.status_code == 200
    assert r.json()["operator"] == "alice"


def test_cancel_requires_runner_token():
    from fastapi.testclient import TestClient
    from services.research.research_runner import main as m
    client = TestClient(m.app)
    r = client.post("/cancel", json={"run_id": "run_nonexistent"},
                    headers={"X-Operator": "alice"})
    assert r.status_code == 401
