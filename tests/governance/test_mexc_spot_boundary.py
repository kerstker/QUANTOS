"""治理:MEXC 现货适配器专属边界(2026-07-14 引入,2026-07-23 拆为独立文件——
console `mexc-readiness` 的 `adapter_governance_test` 检查按 `tests/governance/test_*mexc*.py`
识别本文件,与 test_live_execution_boundary.py 的全树检查互补)。

现货适配器没有 testnet,结构性约束必须比合约适配器更窄:绝不含合约/杠杆语义,
绝不含提币/划转字面量,网络 import 只允许 urllib 白名单。
"""
import ast
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MEXC_SPOT = os.path.join(_ROOT, "live", "exchange", "mexc_spot.py")

_MEXC_FUTURES_SEMANTICS = ["leverage", "marginType", "set_leverage", "positionSide"]
_MEXC_ALLOWED_NETWORK = {"urllib"}
_NETWORK_ROOTS = {"urllib", "socket", "requests", "httpx", "aiohttp", "websocket", "websockets",
                  "ftplib", "telnetlib", "http"}
_FORBIDDEN_SDK = {"ccxt", "binance", "coinbase", "cbpro", "krakenex", "kraken", "ib_insync",
                  "ibapi", "alpaca", "alpaca_trade_api", "oandapyV20", "fix", "quickfix", "ctp", "vnpy"}
# 真红线:把钱转出账户的调用——下单(place_order/submit_order)是本树正当职责,刻意不在此清单
_FORBIDDEN_TRANSFER_CALLS = [".withdraw(", "create_withdrawal", "transfer(", "sapiPost"]


def _import_roots(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                roots.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            roots.add(n.module.split(".")[0])
    return roots


def _mexc_src() -> str:
    if not os.path.exists(_MEXC_SPOT):
        pytest.skip("live/exchange/mexc_spot.py 尚未由并行任务创建——创建后本测试自动生效")
    return open(_MEXC_SPOT, encoding="utf-8").read()


def test_mexc_spot_has_no_futures_or_shorting_semantics():
    src = _mexc_src()
    hits = [w for w in _MEXC_FUTURES_SEMANTICS if w in src]
    assert not hits, \
        f"mexc_spot.py is a SPOT adapter — no leverage/margin/futures semantics allowed: {hits}"


def test_mexc_spot_has_no_withdraw_or_transfer_literals():
    src = _mexc_src()
    hits = [w for w in _FORBIDDEN_TRANSFER_CALLS if w in src]
    assert not hits, \
        f"mexc_spot.py must never move money OUT of the account (order placement is fine): {hits}"


def test_mexc_spot_network_imports_are_urllib_only():
    _mexc_src()  # skip guard
    roots = _import_roots(_MEXC_SPOT)
    network = roots & _NETWORK_ROOTS
    assert network, "mexc_spot.py must use urllib for its signed REST calls"
    assert network <= _MEXC_ALLOWED_NETWORK, \
        f"mexc_spot.py network imports must be urllib-only, got: {sorted(network)}"
    assert not (roots & _FORBIDDEN_SDK), \
        f"FORBIDDEN exchange SDK in mexc_spot.py: {sorted(roots & _FORBIDDEN_SDK)}"
