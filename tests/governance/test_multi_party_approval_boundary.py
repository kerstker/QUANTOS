"""治理:机器钉死多方批准状态机的 L2 边界。
1) approval.py 不 import 任何券商/交易所 SDK、不 import 网络库;
2) 非人 actor 结构上到不了 APPROVED(机器不变量)。"""
import ast
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MOD = os.path.join(_ROOT, "services", "governance", "approval.py")

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


def test_approval_imports_no_sdk_or_network():
    roots = _import_roots(_MOD)
    assert not (roots & _FORBIDDEN_SDK), f"FORBIDDEN SDK in approval.py: {sorted(roots & _FORBIDDEN_SDK)}"
    assert not (roots & _NETWORK_ROOTS), f"network import in approval.py: {sorted(roots & _NETWORK_ROOTS)}"


def test_non_human_can_never_reach_approved():
    from quantos.contracts.alpha import Actor, ActorKind
    from services.governance.approval import (ApprovalViolation, approve, open_request, review)
    human_a = Actor(actor_id="a", kind=ActorKind.HUMAN)
    human_b = Actor(actor_id="b", kind=ActorKind.HUMAN)
    bot = Actor(actor_id="bot", kind=ActorKind.AGENT)
    r = review(open_request("r", "s", human_a, at=1), human_b, at=2)
    with pytest.raises(ApprovalViolation):        # 非人批准被结构性拒绝
        approve(r, bot, at=3)
