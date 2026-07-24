"""治理:交易台 ops API(tools/live_ops_api.py)的边界。2026-07-05。

它是 LiveDesk 前端的操作面——但**绝不直接下单**:下单只能以固定 argv 子进程运行驱动器 CLI
(`python -m tools.live_track_advance`),与人工敲命令行完全同一条 mandate → gate → 确认 →
记账治理链,没有任何旁路。本文件机器钉死这一点 + 参数白名单 + 不泄密。
"""
import ast
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_OPS = os.path.join(_ROOT, "tools", "live_ops_api.py")


def _src() -> str:
    return open(_OPS, encoding="utf-8").read()


def test_ops_api_never_places_orders_directly():
    src = _src()
    # 不直接调用任何下单/撤单/批量提交入口——订单只能经驱动器 CLI 子进程(同一条治理链)
    for forbidden in (".place_order(", ".cancel_order(", "submit_live_batch",
                      "submit_emergency_flatten"):
        assert forbidden not in src, f"ops api must not call {forbidden} directly"


def test_ops_api_run_command_is_fixed_argv_no_shell():
    src = _src()
    assert "shell=True" not in src                    # 绝无 shell 注入面
    from tools.live_ops_api import _build_run_cmd
    cmd = _build_run_cmd("binance", submit=True, refresh=False)
    assert cmd[1:4] == ["-m", "tools.live_track_advance", "--venue"]
    assert "--submit" in cmd and "--no-refresh" in cmd
    cmd2 = _build_run_cmd("bybit_demo", submit=False, refresh=True)
    assert "--submit" not in cmd2 and "--no-refresh" not in cmd2


def test_ops_api_rejects_unknown_venue():
    from fastapi.testclient import TestClient
    from tools.live_ops_api import _OPS_TOKEN, app
    client = TestClient(app)
    r = client.post("/api/v1/liveops/run", json={"venue": "evil; rm -rf /", "submit": False},
                    headers={"X-Ops-Token": _OPS_TOKEN})
    assert r.status_code == 400


def test_ops_api_rejects_out_of_range_demo_amount():
    from fastapi.testclient import TestClient
    from tools.live_ops_api import _OPS_TOKEN, app
    client = TestClient(app)
    headers = {"X-Ops-Token": _OPS_TOKEN}
    assert client.post("/api/v1/liveops/demo-funds", json={"amount": -5}, headers=headers).status_code == 400
    assert client.post("/api/v1/liveops/demo-funds", json={"amount": 10_000_000}, headers=headers).status_code == 400


# --- 本机共享密钥认证(2026-07-05) ---

def test_ops_api_rejects_requests_without_token():
    from fastapi.testclient import TestClient
    from tools.live_ops_api import app
    client = TestClient(app)
    assert client.get("/api/v1/liveops/dashboard").status_code == 401
    assert client.post("/api/v1/liveops/run", json={"venue": "binance", "submit": False}).status_code == 401
    assert client.post("/api/v1/liveops/demo-funds", json={"amount": 100}).status_code == 401


def test_ops_api_rejects_wrong_token():
    from fastapi.testclient import TestClient
    from tools.live_ops_api import app
    client = TestClient(app)
    r = client.get("/api/v1/liveops/dashboard", headers={"X-Ops-Token": "wrong-token-value"})
    assert r.status_code == 401


def test_ops_api_accepts_correct_token():
    from fastapi.testclient import TestClient
    from tools.live_ops_api import _OPS_TOKEN, app
    client = TestClient(app)
    r = client.get("/api/v1/liveops/dashboard", headers={"X-Ops-Token": _OPS_TOKEN})
    assert r.status_code == 200


def test_ops_token_file_is_gitignored():
    # 一次生成、跨进程复用的本机密钥文件绝不能被提交
    proc_result = __import__("subprocess").run(
        # QUANTOS keeps a bare .git directory for worktree orchestration;
        # explicitly opt into worktree semantics so this check is portable.
        ["git", "--git-dir", os.path.join(_ROOT, ".git"),
         "--work-tree", _ROOT, "-c", "core.bare=false", "check-ignore",
         os.path.join("key", "live_ops_token.txt")],
        cwd=_ROOT, capture_output=True, text=True)
    assert proc_result.returncode == 0, "key/live_ops_token.txt must be gitignored"


def test_ops_api_source_never_serializes_secrets():
    # 凭证只在进程内构造适配器;任何响应字典/JSON 字段名里不得出现 secret
    tree = ast.parse(_src())
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str) \
                        and "secret" in k.value.lower():
                    offenders.append(k.value)
    assert not offenders, f"response dicts must never carry secret-named fields: {offenders}"


def test_ops_api_cors_is_localhost_only():
    src = _src()
    assert 'allow_origins=["http://localhost:3100", "http://127.0.0.1:3100"]' in src
    assert '"*"' not in src.split("allow_origins")[1].split("]")[0]


# --- NEEDS_HUMAN_REVIEW 队列(2026-07-05):只读展示 + 记录决定,绝不自动重新提交 ---

def test_ops_api_pending_review_decision_never_resubmits_orders():
    src = _src()
    assert "submit_live_batch" not in src            # 已在上面钉过,这里再次确认覆盖新端点
    assert "place_order" not in src


def test_ops_api_pending_review_requires_reviewer_name():
    from fastapi.testclient import TestClient
    from tools.live_ops_api import _OPS_TOKEN, app
    client = TestClient(app)
    headers = {"X-Ops-Token": _OPS_TOKEN}
    r = client.post("/api/v1/liveops/pending-review/1/decide",
                    json={"status": "APPROVED", "reviewer": "  "}, headers=headers)
    assert r.status_code == 400


def test_ops_api_pending_review_rejects_invalid_status():
    from fastapi.testclient import TestClient
    from tools.live_ops_api import _OPS_TOKEN, app
    client = TestClient(app)
    headers = {"X-Ops-Token": _OPS_TOKEN}
    r = client.post("/api/v1/liveops/pending-review/1/decide",
                    json={"status": "MAYBE", "reviewer": "alice"}, headers=headers)
    assert r.status_code == 400


def test_ops_api_pending_review_requires_auth():
    from fastapi.testclient import TestClient
    from tools.live_ops_api import app
    client = TestClient(app)
    assert client.get("/api/v1/liveops/pending-review").status_code == 401


# --- venue 注册表白名单(2026-07-14, MEXC 现货) ---

_KNOWN_VENUES = {"binance", "bybit", "bybit_demo", "mexc", "binance_mainnet"}


def test_ops_api_venue_registry_only_known_venues():
    """_VENUES(live_track_advance)是 ops API 命令行白名单的源头——它注册什么,前端就能
    以固定 argv 子进程跑什么。机器钉死注册表只能是已知 venue 的子集,防止任意注册把
    未经治理审查的 venue 暴露给 /run 端点。MEXC 注册后(mexc 现货适配器由并行任务接入)
    必须满足同一形状:固定 argv、无 shell、可走 submit 流程。"""
    from tools.live_track_advance import _VENUES
    assert set(_VENUES) <= _KNOWN_VENUES, \
        f"_VENUES may only register known venues, got: {sorted(set(_VENUES) - _KNOWN_VENUES)}"


def test_ops_api_mexc_venue_builds_fixed_argv_when_registered():
    from tools.live_track_advance import _VENUES
    if "mexc" not in _VENUES:
        pytest.skip("mexc 尚未注册进 _VENUES——注册后本测试自动生效")
    from tools.live_ops_api import _build_run_cmd
    cmd = _build_run_cmd("mexc", submit=True, refresh=False)
    assert cmd[1:4] == ["-m", "tools.live_track_advance", "--venue"]
    assert cmd[4] == "mexc"
    assert "--submit" in cmd and "--no-refresh" in cmd


def test_ops_api_binance_mainnet_venue_builds_fixed_argv_when_registered():
    """binance_mainnet(2026-07 Binance 期货 mainnet 接线)注册后满足同一形状:固定 argv、
    无 shell、可走 submit 流程——但 argv 永不携带 --arm-live(ops API 无 arming 能力,
    该 venue 经 ops API 触发的运行会在驱动器 mainnet_only 闸门上 fail-closed SystemExit)。"""
    from tools.live_track_advance import _VENUES
    if "binance_mainnet" not in _VENUES:
        pytest.skip("binance_mainnet 尚未注册进 _VENUES——注册后本测试自动生效")
    from tools.live_ops_api import _build_run_cmd
    cmd = _build_run_cmd("binance_mainnet", submit=True, refresh=False)
    assert cmd[1:4] == ["-m", "tools.live_track_advance", "--venue"]
    assert cmd[4] == "binance_mainnet"
    assert "--submit" in cmd and "--no-refresh" in cmd
    assert "--arm-live" not in cmd


# --- --arm-live 治理(2026-07, MEXC 对接 1.5)---

def test_ops_api_fixed_argv_never_carries_arm_live():
    """--arm-live 是**人工 CLI 专属**的 arming 路径(唯一允许构造 armed SafetyConfig 的入口)。
    ops API 的 POST /run 固定 argv 绝不携带它——AI/前端可触达的进程没有任何 arming 能力。"""
    src = _src()
    assert "--arm-live" not in src
    from tools.live_ops_api import _build_run_cmd
    for venue in ("binance", "bybit_demo", "mexc"):
        for submit in (True, False):
            cmd = _build_run_cmd(venue, submit=submit, refresh=False)
            assert "--arm-live" not in cmd


def test_armed_safety_config_helper_keeps_ai_and_human_approval_invariants():
    from tools.live_track_advance import armed_safety_config
    assert armed_safety_config(["prog"]) is None
    assert armed_safety_config(["prog", "--submit"]) is None
    with pytest.raises(SystemExit):
        armed_safety_config(["prog", "--arm-live"])          # preview 绝不 arm
    cfg = armed_safety_config(["prog", "--submit", "--arm-live"])
    assert cfg.live_trading_armed is True
    assert cfg.allow_ai_direct_trading is False              # AI 直接交易恒禁(此路径翻不掉)
    assert cfg.require_human_approval is True


def test_no_arm_path_outside_the_human_cli_driver():
    """armed SafetyConfig 的构造面必须收敛到人工 CLI 驱动器一处:`live_trading_enabled=True`
    字面量在 tools/services/live 生产代码里只允许出现在 tools/live_track_advance.py
    (armed_safety_config)——任何第二处构造路径都是未经治理评审的 arming 旁路。"""
    offenders = []
    for tree in ("tools", "services", "live"):
        for dirpath, _dirs, files in os.walk(os.path.join(_ROOT, tree)):
            if "__pycache__" in dirpath:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                if os.path.basename(path) == "live_track_advance.py":
                    continue
                src = open(path, encoding="utf-8").read()
                if "live_trading_enabled=True" in src:
                    offenders.append(os.path.relpath(path, _ROOT))
    assert not offenders, f"armed SafetyConfig construction outside the human CLI driver: {offenders}"
