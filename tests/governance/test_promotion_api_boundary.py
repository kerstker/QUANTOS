"""治理:机器钉死晋级闭环(approval_store / promotion / promotion_api)的 L2 边界。

1) 三个新模块不 import 券商/交易所 SDK、不 import 网络库(fastapi/uvicorn 例外,镜像
   research_runner 的既有口径)、不 import live/、不 import tools/、无下单/转账调用字面量;
2) 通用治理扫描器对 services/governance + services/alpha 全绿(FORBIDDEN_APIS/CREDENTIALS/FLAGS);
3) 非人 actor 结构上到不了 APPROVED;单人/两人凑不齐三方;
4) 篡改持久化步骤被哈希链拒;
5) 无 token → 401(GET 与 POST 都要);
6) LIVE 五旗帜不受影响 —— 晋级 ≠ 真钱,SafetyConfig 默认全安全、grant 不解锁 LIVE。
镜像 tests/governance/test_multi_party_approval_boundary.py 与 test_research_runner_boundary.py 的写法。"""

import ast
import importlib.util
import os
import sqlite3

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FILES = [
    os.path.join(_ROOT, "services", "governance", "approval_store.py"),
    os.path.join(_ROOT, "services", "governance", "strong_identity.py"),
    os.path.join(_ROOT, "services", "governance", "identity_authorization.py"),
    os.path.join(
        _ROOT,
        "services",
        "governance",
        "identity_authorization_store.py",
    ),
    os.path.join(_ROOT, "services", "alpha", "promotion.py"),
] + [
    os.path.join(_ROOT, "services", "promotion_api", f)
    for f in os.listdir(os.path.join(_ROOT, "services", "promotion_api"))
    if f.endswith(".py")
]

_FORBIDDEN_SDK = {
    "ccxt",
    "binance",
    "coinbase",
    "cbpro",
    "krakenex",
    "kraken",
    "ib_insync",
    "ibapi",
    "alpaca",
    "alpaca_trade_api",
    "oandapyV20",
    "fix",
    "quickfix",
    "ctp",
    "vnpy",
}
_NETWORK_ROOTS = {
    "urllib",
    "socket",
    "requests",
    "httpx",
    "aiohttp",
    "websocket",
    "websockets",
    "ftplib",
    "telnetlib",
    "http",
}
_FORBIDDEN_CALLS = [
    "create_order",
    "place_order",
    "submit_order",
    "cancel_order",
    "create_market_order",
    "create_limit_order",
    "fapiPrivate",
    "sapiPost",
    ".withdraw(",
    "create_withdrawal",
    "transfer(",
]


def _import_roots(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            roots |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            roots.add(n.module.split(".")[0])
    return roots


def test_promotion_modules_no_sdk_network_live_or_tools_import():
    assert _FILES, "promotion modules must exist"
    for p in _FILES:
        roots = _import_roots(p)
        name = os.path.basename(p)
        assert not (roots & _FORBIDDEN_SDK), f"broker/exchange SDK in {name}: {sorted(roots & _FORBIDDEN_SDK)}"
        assert not (roots & _NETWORK_ROOTS), f"network import in {name}: {sorted(roots & _NETWORK_ROOTS)}"
        assert "live" not in roots, f"{name} imports live/ (services→live 单向边界)"
        assert "tools" not in roots, f"{name} imports tools/ (services 不得依赖 tools)"
        src = open(p, encoding="utf-8").read()
        assert "services.trading" not in src, f"trading-path import in {name}"
        hits = [c for c in _FORBIDDEN_CALLS if c in src]
        assert not hits, f"order/transfer call in {name}: {hits}"


def test_promotion_api_has_no_shared_token_or_self_asserted_identity():
    path = os.path.join(_ROOT, "services", "promotion_api", "main.py")
    source = open(path, encoding="utf-8").read()
    for forbidden in (
        "X-Gov-Token",
        "X-Operator",
        "governance_token.txt",
        "_load_or_create_token",
        "token_urlsafe",
    ):
        assert forbidden not in source
    assert "Authorization" in source
    assert "PROMOTION_APPROVER" in source
    assert "PROMOTION_EXECUTOR" in source
    assert "evaluate_authorization" in source
    assert "IdentityAuthorizationStore" in source
    tree = ast.parse(source)
    authorization_store_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "IdentityAuthorizationStore"
    ]
    assert authorization_store_calls
    assert all(
        any(
            keyword.arg == "create" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            for keyword in call.keywords
        )
        for call in authorization_store_calls
    )


def test_promotion_command_surface_is_research_only_and_fail_closed():
    path = os.path.join(_ROOT, "services", "promotion_api", "main.py")
    source = open(path, encoding="utf-8").read()
    assert "SqliteCommandLog" in source
    assert "CommandEnvelope" in source
    assert "idempotency_key" in source
    assert "target_version" in source
    assert "business_reason" in source
    assert "expected_impact" in source
    assert "preview_hash" in source
    for forbidden in (
        "SUBMIT_ORDER",
        "SCALE_CAPITAL",
        "MODIFY_RISK_LIMIT",
        "RESET_KILL_SWITCH",
    ):
        assert forbidden not in source


def test_identity_authorization_default_registry_is_empty_and_l2_only():
    import json

    path = os.path.join(
        _ROOT,
        "config",
        "identity_authorization_registry.json",
    )
    document = json.loads(open(path, encoding="utf-8").read())
    assert document == {
        "schema_version": 1,
        "readiness": "L2_DRY_RUN_ONLY",
        "generation": 0,
        "principals": {},
    }


def test_governance_scanner_green_on_promotion_dirs():
    """显式对 services/governance + services/alpha 跑通用治理扫描器(字面量级)。"""
    scanner_path = os.path.join(_ROOT, "apps", "research-console", "scripts", "run_paper_to_live_governance_check.py")
    spec = importlib.util.spec_from_file_location("gov_scanner", scanner_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.scan(
        [
            os.path.join(_ROOT, "services", "governance"),
            os.path.join(_ROOT, "services", "alpha"),
            os.path.join(_ROOT, "services", "promotion_api"),
        ]
    )
    assert result["passed"], f"governance scanner violations: {result['violations']}"


# --- 状态机不变量(与 approval.py 边界测试同法,对着晋级 subject 钉一遍)---------


def test_non_human_cannot_reach_approved_in_promotion_flow():
    from quantos.contracts.alpha import Actor, ActorKind
    from services.governance.approval import ApprovalViolation, approve, open_request, review
    from services.alpha.guards import promotion_subject
    from quantos.contracts.research import AlphaState

    subject = promotion_subject("a1", AlphaState.APPROVED)
    a = Actor(actor_id="alice", kind=ActorKind.HUMAN)
    b = Actor(actor_id="bob", kind=ActorKind.HUMAN)
    bot = Actor(actor_id="bot", kind=ActorKind.AGENT)
    r = review(open_request("r", subject, a, at=1), b, at=2)
    with pytest.raises(ApprovalViolation):
        approve(r, bot, at=3)


def test_one_or_two_humans_cannot_complete_three_party():
    from quantos.contracts.alpha import Actor, ActorKind
    from services.governance.approval import ApprovalViolation, approve, open_request, review

    a = Actor(actor_id="alice", kind=ActorKind.HUMAN)
    b = Actor(actor_id="bob", kind=ActorKind.HUMAN)
    r = open_request("r", "s", a, at=1)
    with pytest.raises(ApprovalViolation):  # 同一人复核自己的申请
        review(r, a, at=2)
    r = review(r, b, at=2)
    for dup in (a, b):
        with pytest.raises(ApprovalViolation):  # 前两人任何一个都不能兼任批准人
            approve(r, dup, at=3)


def test_tampered_persisted_step_rejected_by_chain(tmp_path):
    from quantos.contracts.alpha import Actor, ActorKind
    from services.governance.approval import approve, open_request, review
    from services.governance.approval_store import ApprovalStore, ApprovalStoreError

    a = Actor(actor_id="alice", kind=ActorKind.HUMAN)
    b = Actor(actor_id="bob", kind=ActorKind.HUMAN)
    c = Actor(actor_id="carol", kind=ActorKind.HUMAN)
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    req = approve(review(open_request("r1", "s", a, at=1), b, at=2), c, at=3)
    store.save(req, now=3)
    raw = sqlite3.connect(str(tmp_path / "approvals.db"))
    raw.execute("UPDATE approval_steps SET actor_id='mallory' WHERE step_index=2")
    raw.commit()
    raw.close()
    with pytest.raises(ApprovalStoreError):
        store.load("r1")


# --- API 认证 + LIVE 旗帜零接触 --------------------------------------------------


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import services.promotion_api.main as m

    monkeypatch.setattr(m, "_APPROVALS_DB", str(tmp_path / "approvals.db"))
    monkeypatch.setattr(m, "_EXP_DB", str(tmp_path / "experiments_reg.db"))
    monkeypatch.setattr(m, "_PAPER_DB", str(tmp_path / "paper_track.db"))
    monkeypatch.setattr(m, "_AUDIT_DB", str(tmp_path / "gov_audit.db"))
    monkeypatch.setattr(m, "_COMMAND_DB", str(tmp_path / "promotion_commands.db"))
    return TestClient(m.app), m


def test_all_data_endpoints_require_strong_bearer_identity(tmp_path, monkeypatch):
    client, m = _client(tmp_path, monkeypatch)
    for method, path in [
        ("GET", "/api/v1/governance/promotion/candidates"),
        ("GET", "/api/v1/governance/promotion/requests"),
        ("GET", "/api/v1/governance/promotion/requests/x"),
        ("POST", "/api/v1/governance/promotion/commands/preview"),
        ("POST", "/api/v1/governance/promotion/requests"),
        ("POST", "/api/v1/governance/promotion/requests/x/review"),
        ("POST", "/api/v1/governance/promotion/requests/x/approve"),
        ("POST", "/api/v1/governance/promotion/requests/x/reject"),
        ("POST", "/api/v1/governance/promotion/requests/x/execute"),
    ]:
        r = client.request(method, path, headers={"X-Operator": "alice"}, json={} if method == "POST" else None)
        assert r.status_code == 401, f"{method} {path} must require strong identity"


def test_unbound_bearer_token_is_401(tmp_path, monkeypatch):
    client, _m = _client(tmp_path, monkeypatch)
    r = client.get(
        "/api/v1/governance/promotion/requests",
        headers={"Authorization": "Bearer unbound-token"},
    )
    assert r.status_code == 401


def test_live_flags_untouched_by_promotion_modules():
    import services.alpha.promotion  # noqa: F401 — import 本身不得碰旗帜
    import services.governance.approval_store  # noqa: F401
    import services.promotion_api.main  # noqa: F401
    from quantos.contracts.config import SafetyConfig

    cfg = SafetyConfig()
    assert cfg.live_trading_armed is False  # 晋级 ≠ 真钱:APPROVED 只是 alpha 生命周期状态
    for p in _FILES:
        src = open(p, encoding="utf-8").read()
        for flag in (
            "live_trading_enabled",
            "real_broker_enabled",
            "allow_order_submission",
            "allow_ai_direct_trading",
        ):
            assert f"{flag}=True" not in src.replace(" ", ""), f"{os.path.basename(p)} arms {flag}"
