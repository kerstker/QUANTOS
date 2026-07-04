"""治理:机器钉死 live/ 执行栈(Stage 1,实盘路线)的 L2 安全边界。镜像
test_broker_adapter_boundary.py / test_signing_boundary.py 的 AST 检查风格,应用到整棵 live/ 树。
设计:docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §4/§10。

1) 仅 live/exchange/{binance,bybit}_futures.py 可含网络 import;live/ 其余文件一律不许;
2) live/ 全树无交易所/券商 SDK import;
3) live/ 全树源码不含提幣/划转调用字面量(place_order/submit_order 是本树正当职责,不在禁止清单);
4) 每个 venue adapter(base_url=MAINNET_BASE)在 L2 默认下必须 raise SafetyViolation;
5) 源码无硬编码 API key/secret 字面量;
6) services/ 树任何文件不得 import live/(单向依赖:live/ 依赖 services/,反向禁止);
7) evaluate_mandate 在 mandate 缺失/过期/venue 不匹配时,对非空 orders 恒 NEEDS_HUMAN_REVIEW
   (fail-closed 冒烟——完整分支覆盖在 tests/live/test_mandate.py)。
"""
import ast
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LIVE = os.path.join(_ROOT, "live")
_SERVICES = os.path.join(_ROOT, "services")
_VENUE_ADAPTERS = (os.path.join(_LIVE, "exchange", "binance_futures.py"),
                   os.path.join(_LIVE, "exchange", "bybit_futures.py"))

_FORBIDDEN_SDK = {"ccxt", "binance", "coinbase", "cbpro", "krakenex", "kraken", "ib_insync",
                  "ibapi", "alpaca", "alpaca_trade_api", "oandapyV20", "fix", "quickfix", "ctp", "vnpy"}
_NETWORK_ROOTS = {"urllib", "socket", "requests", "httpx", "aiohttp", "websocket", "websockets",
                  "ftplib", "telnetlib", "http"}
# 真红线:把钱转出账户的调用——下单(place_order/submit_order)是本树正当职责,刻意不在此清单
_FORBIDDEN_TRANSFER_CALLS = [".withdraw(", "create_withdrawal", "transfer(", "sapiPost"]


def _all_py_files(root):
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return out


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


_LIVE_FILES = _all_py_files(_LIVE)


_VENUE_ADAPTER_PATHS = {os.path.normpath(p) for p in _VENUE_ADAPTERS}


def test_only_venue_adapters_have_network_imports():
    offenders = []
    for path in _LIVE_FILES:
        if os.path.normpath(path) in _VENUE_ADAPTER_PATHS:
            continue
        bad = _import_roots(path) & _NETWORK_ROOTS
        if bad:
            offenders.append(f"{os.path.relpath(path, _ROOT)}: {sorted(bad)}")
    assert not offenders, "network import outside the venue-adapter seam:\n" + "\n".join(offenders)


def test_venue_adapters_only_stdlib_network_no_sdk():
    for adapter in _VENUE_ADAPTERS:
        roots = _import_roots(adapter)
        assert not (roots & _FORBIDDEN_SDK), \
            f"FORBIDDEN exchange SDK in {os.path.relpath(adapter, _ROOT)}: {sorted(roots & _FORBIDDEN_SDK)}"
        assert roots & _NETWORK_ROOTS, \
            f"expected {os.path.relpath(adapter, _ROOT)} to use urllib for its signed REST calls"


def test_live_tree_has_no_exchange_sdk_anywhere():
    offenders = []
    for path in _LIVE_FILES:
        bad = _import_roots(path) & _FORBIDDEN_SDK
        if bad:
            offenders.append(f"{os.path.relpath(path, _ROOT)}: {sorted(bad)}")
    assert not offenders, "FORBIDDEN exchange/broker SDK in live/:\n" + "\n".join(offenders)


def test_live_tree_has_no_withdraw_or_transfer_calls():
    offenders = []
    for path in _LIVE_FILES:
        src = open(path, encoding="utf-8").read()
        hits = [w for w in _FORBIDDEN_TRANSFER_CALLS if w in src]
        if hits:
            offenders.append(f"{os.path.relpath(path, _ROOT)}: {hits}")
    assert not offenders, "FORBIDDEN withdraw/transfer call in live/ (order submission is fine; " \
                          "moving money OUT of the account is not):\n" + "\n".join(offenders)


def test_mainnet_adapters_uninstantiable_at_l2():
    from live.exchange.binance_futures import BinanceFuturesAdapter
    from live.exchange.bybit_futures import BybitFuturesAdapter
    from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS, SafetyViolation
    assert RESEARCH_SAFE_DEFAULTS.live_trading_armed is False
    for cls in (BinanceFuturesAdapter, BybitFuturesAdapter):
        with pytest.raises(SafetyViolation):
            cls(api_key="k", api_secret="s", base_url=cls.MAINNET_BASE,
               safety_config=RESEARCH_SAFE_DEFAULTS)


def test_no_hardcoded_api_key_material_in_live_tree():
    pattern = re.compile(r'(API_KEY|API_SECRET|SECRET_KEY|PRIVATE_KEY)\s*=\s*["\'][A-Za-z0-9]{8,}')
    offenders = []
    for path in _LIVE_FILES:
        src = open(path, encoding="utf-8").read()
        if pattern.search(src) or "-----BEGIN" in src:
            offenders.append(os.path.relpath(path, _ROOT))
    assert not offenders, f"hardcoded key-like literal found in: {offenders}"


def test_services_tree_does_not_import_live():
    offenders = []
    for dirpath, _dirs, files in os.walk(_SERVICES):
        for fn in files:
            if fn.endswith(".py"):
                p = os.path.join(dirpath, fn)
                if "live" in _import_roots(p):
                    offenders.append(os.path.relpath(p, _ROOT))
    assert not offenders, "services/ must never depend on live/ (one-way: live/ -> services/ only):\n" \
                          + "\n".join(offenders)


def test_emergency_channel_is_structurally_independent_of_the_shared_gate():
    """紧急减仓通道(live/execution/emergency.py)的存在意义 = 不被 kill-switch 挡住,
    它的安全性因此**必须**来自结构约束而非共用 gate——机器钉死两点:
    ① 不 import 共用风控门/kill switch(否则"独立"名存实亡);
    ② 它构造的每一笔订单都强制 reduce_only=True(源码级检查 + 行为级性质测试在
       tests/live/test_emergency.py 的网格测试)。"""
    path = os.path.join(_LIVE, "execution", "emergency.py")
    src = open(path, encoding="utf-8").read()
    roots_and_modules = set()
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            roots_and_modules.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            roots_and_modules.add(n.module)
            roots_and_modules.update(f"{n.module}.{a.name}" for a in n.names)
    forbidden = [m for m in roots_and_modules
                 if "pre_trade.gate" in m or "PreTradeRiskGate" in m
                 or "kill_switch" in m or "KillSwitch" in m]
    assert not forbidden, \
        f"emergency channel must not couple to the shared gate/kill-switch: {forbidden}"
    assert "reduce_only=True" in src, \
        "emergency channel must force reduce_only=True on every order it constructs"
    assert "reduce_only=False" not in src


def test_emergency_validation_grid_only_reducing_orders_pass():
    """行为级冒烟(完整网格在 tests/live/test_emergency.py):加仓方向/超量/无持仓全被拒。"""
    from live.execution.emergency import validate_flatten_order
    from quantos.contracts.trading import OrderSide
    assert validate_flatten_order(OrderSide.SELL, 0.5, position=1.0) is None
    assert validate_flatten_order(OrderSide.BUY, 0.5, position=1.0) is not None
    assert validate_flatten_order(OrderSide.SELL, 1.5, position=1.0) is not None
    assert validate_flatten_order(OrderSide.SELL, 0.5, position=0.0) is not None


def test_evaluate_mandate_fails_closed_without_valid_mandate():
    from live.contracts import LiveOrderRequest
    from live.governance.mandate import NEEDS_HUMAN_REVIEW, Mandate, evaluate_mandate
    from quantos.contracts.trading import OrderSide

    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                             client_order_id="c1")
    prices = {"BTC": 60000.0}

    assert evaluate_mandate([order], prices, None, venue="binance_testnet", now=1).tier \
        == NEEDS_HUMAN_REVIEW

    expired = Mandate(mandate_id="m", venue="binance_testnet", allowed_instruments=frozenset({"BTC"}),
                      max_single_order_notional=1e9, max_gross_notional=1e9, max_net_notional=1e9,
                      expires_at=0)
    assert evaluate_mandate([order], prices, expired, venue="binance_testnet", now=1).tier \
        == NEEDS_HUMAN_REVIEW

    valid = Mandate(mandate_id="m", venue="binance_testnet", allowed_instruments=frozenset({"BTC"}),
                    max_single_order_notional=1e9, max_gross_notional=1e9, max_net_notional=1e9)
    assert evaluate_mandate([order], prices, valid, venue="bybit_testnet", now=1).tier \
        == NEEDS_HUMAN_REVIEW
