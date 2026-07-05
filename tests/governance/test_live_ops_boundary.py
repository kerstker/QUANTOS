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
    from tools.live_ops_api import app
    client = TestClient(app)
    r = client.post("/api/v1/liveops/run", json={"venue": "evil; rm -rf /", "submit": False})
    assert r.status_code == 400


def test_ops_api_rejects_out_of_range_demo_amount():
    from fastapi.testclient import TestClient
    from tools.live_ops_api import app
    client = TestClient(app)
    assert client.post("/api/v1/liveops/demo-funds", json={"amount": -5}).status_code == 400
    assert client.post("/api/v1/liveops/demo-funds", json={"amount": 10_000_000}).status_code == 400


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
