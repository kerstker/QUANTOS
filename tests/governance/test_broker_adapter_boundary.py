"""治理:机器钉死 broker adapter 接缝的 L2 安全边界(轨道3 子项目1)。
1) broker_adapter.py 不 import 任何券商/交易所 SDK、不 import 网络库;
2) LiveBrokerAdapter 在 RESEARCH_SAFE_DEFAULTS(L2 默认)下无法实例化(构造即 raise)。
(services/ 全树无网络 import 已由 test_crypto_data_boundary.py::test_services_tree_has_no_network_imports 覆盖;
这里针对接缝文件再显式钉一道,并把"L2 下 live 适配器不可实例化"变成机器强制的不变量。)"""
import ast
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BROKER = os.path.join(_ROOT, "services", "trading", "broker_adapter.py")

# exchange / broker / trading SDKs that must NEVER appear (dual-use -> live trading)
_FORBIDDEN_SDK = {"ccxt", "binance", "coinbase", "cbpro", "krakenex", "kraken", "ib_insync",
                  "ibapi", "alpaca", "alpaca_trade_api", "oandapyV20", "fix", "quickfix", "ctp", "vnpy"}
_NETWORK_ROOTS = {"urllib", "socket", "requests", "httpx", "aiohttp", "websocket", "websockets",
                  "ftplib", "telnetlib", "http"}


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


def test_broker_adapter_imports_no_sdk_or_network():
    roots = _import_roots(_BROKER)
    assert not (roots & _FORBIDDEN_SDK), f"FORBIDDEN broker/exchange SDK in broker_adapter.py: {sorted(roots & _FORBIDDEN_SDK)}"
    assert not (roots & _NETWORK_ROOTS), f"network import in broker_adapter.py: {sorted(roots & _NETWORK_ROOTS)}"


def test_live_broker_adapter_uninstantiable_at_l2():
    from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS, SafetyViolation
    from services.trading.broker_adapter import LiveBrokerAdapter
    assert RESEARCH_SAFE_DEFAULTS.live_trading_armed is False
    with pytest.raises(SafetyViolation):
        LiveBrokerAdapter(RESEARCH_SAFE_DEFAULTS)
