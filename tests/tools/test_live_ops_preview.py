"""live_ops_api 的 armed-preview(演练)端点(2026-07-24,POST /api/v1/liveops/preview)。

钉死:认证、venue/strategy 白名单、固定 argv(mainnet-only venue 带 --arm-live、任何
venue 绝不带 --submit)、新鲜 status JSON(含 would_submit)透传、陈旧快照诚实 None、
驱动器失败/超时 → 诚实错误载荷、stdout 尾部凭证形状行过滤、单飞锁。
全程假 subprocess + tmp_path——零真网、零真凭证、零真子进程。
"""
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

import tools.live_ops_api as ops
from tools.live_track_advance import _VENUES, _venue_paths

_FUTURE_RAN_AT_MS = 9_999_999_999_999        # 远大于任何真实 started_ms → 恒"新鲜"


@pytest.fixture()
def client():
    return TestClient(ops.app)


@pytest.fixture()
def headers():
    return {"X-Ops-Token": ops._OPS_TOKEN}


class _FakeSubprocess:
    """替换 ops 命名空间里的 subprocess 模块引用(端点每次经模块全局名查 subprocess.run/
    TimeoutExpired)——捕获 argv、返回假 CompletedProcess,零真子进程。"""

    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, returncode=0, stdout="", stderr="", raise_exc=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raise_exc = raise_exc
        self.captured = []

    def run(self, cmd, **kwargs):
        self.captured.append(list(cmd))
        if self.raise_exc is not None:
            raise self.raise_exc
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout=self.stdout, stderr=self.stderr)


def _install_fake_run(monkeypatch, **kwargs):
    fake = _FakeSubprocess(**kwargs)
    monkeypatch.setattr(ops, "subprocess", fake)
    return fake


def _write_status(tmp_path, venue, strategy, payload):
    path = _venue_paths(_VENUES[venue]["suffix"], strategy_id=strategy)["status"]
    full = tmp_path / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- 认证 + 白名单 ---

def test_preview_requires_ops_token(client):
    r = client.post("/api/v1/liveops/preview",
                    json={"venue": "binance", "strategy": "crowd_contra"})
    assert r.status_code == 401


def test_preview_rejects_unknown_venue(client, headers):
    r = client.post("/api/v1/liveops/preview",
                    json={"venue": "evil; rm -rf /", "strategy": "crowd_contra"},
                    headers=headers)
    assert r.status_code == 400


def test_preview_rejects_unknown_strategy(client, headers):
    r = client.post("/api/v1/liveops/preview",
                    json={"venue": "binance", "strategy": "nonexistent_alpha"},
                    headers=headers)
    assert r.status_code == 400


# --- 固定 argv 形状 ---

def test_preview_argv_arms_binance_mainnet_and_never_submits(client, headers, monkeypatch,
                                                             tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = _install_fake_run(monkeypatch, stdout="preview done")
    r = client.post("/api/v1/liveops/preview",
                    json={"venue": "binance_mainnet", "strategy": "crowd_contra"},
                    headers=headers)
    assert r.status_code == 200
    cmd = fake.captured[0]
    assert cmd[1:4] == ["-m", "tools.live_track_advance", "--venue"]
    assert cmd[cmd.index("--venue") + 1] == "binance_mainnet"
    assert cmd[cmd.index("--strategy") + 1] == "crowd_contra"
    assert "--arm-live" in cmd                       # mainnet-only → armed preview
    assert "--submit" not in cmd                     # 治理红线:ops argv 绝不提交
    assert "--no-refresh" in cmd
    body = r.json()
    assert body["ok"] is True and body["returncode"] == 0
    assert body["mode"] == "preview" and body["armed"] is True


def test_preview_argv_testnet_venue_never_arms(client, headers, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = _install_fake_run(monkeypatch)
    r = client.post("/api/v1/liveops/preview",
                    json={"venue": "binance", "strategy": "crowd_contra"}, headers=headers)
    assert r.status_code == 200
    cmd = fake.captured[0]
    assert "--arm-live" not in cmd and "--submit" not in cmd
    assert r.json()["armed"] is False


def test_preview_body_extra_keys_cannot_change_argv(client, headers, monkeypatch, tmp_path):
    # 请求体塞 submit/refresh/arm 类键,pydantic 忽略未知键 → argv 与纯净请求逐字节相同
    monkeypatch.chdir(tmp_path)
    fake = _install_fake_run(monkeypatch)
    r = client.post("/api/v1/liveops/preview",
                    json={"venue": "binance_mainnet", "strategy": "crowd_contra",
                          "submit": True, "refresh": True, "arm_live": False},
                    headers=headers)
    assert r.status_code == 200
    assert fake.captured[0] == ops._build_preview_cmd("binance_mainnet", "crowd_contra")


# --- status JSON 透传(含 would_submit) ---

def test_preview_returns_fresh_status_json_with_would_submit(client, headers, monkeypatch,
                                                             tmp_path):
    monkeypatch.chdir(tmp_path)
    _install_fake_run(monkeypatch, stdout="=== live_track (PREVIEW) tier=AUTO")
    would_submit = [{"instrument_id": "BTC", "side": "BUY", "quantity": 0.001,
                     "order_type": "MARKET", "limit_price": None}]
    _write_status(tmp_path, "binance_mainnet", "crowd_contra", {
        "ran_at": _FUTURE_RAN_AT_MS, "mode": "preview", "armed": True, "tier": "AUTO",
        "strategy": "crowd_contra", "venue": "binance_mainnet",
        "would_submit": would_submit, "n_submitted": 1, "alerts": []})
    r = client.post("/api/v1/liveops/preview",
                    json={"venue": "binance_mainnet", "strategy": "crowd_contra"},
                    headers=headers)
    assert r.status_code == 200
    status = r.json()["status"]
    assert status is not None
    assert status["would_submit"] == would_submit
    assert status["mode"] == "preview" and status["armed"] is True


def test_preview_stale_status_is_honest_none(client, headers, monkeypatch, tmp_path):
    # 上一轮的陈旧快照(ran_at 远早于本次启动)绝不能当本次结果返回
    monkeypatch.chdir(tmp_path)
    _install_fake_run(monkeypatch)
    _write_status(tmp_path, "binance", "crowd_contra",
                  {"ran_at": 1, "mode": "preview", "tier": "AUTO"})
    r = client.post("/api/v1/liveops/preview",
                    json={"venue": "binance", "strategy": "crowd_contra"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] is None


def test_preview_missing_status_is_honest_none(client, headers, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _install_fake_run(monkeypatch)
    r = client.post("/api/v1/liveops/preview",
                    json={"venue": "binance", "strategy": "crowd_contra"}, headers=headers)
    assert r.status_code == 200 and r.json()["status"] is None


# --- 失败/超时 → 诚实错误载荷 ---

def test_preview_driver_failure_is_honest_error_payload(client, headers, monkeypatch,
                                                        tmp_path):
    monkeypatch.chdir(tmp_path)
    _install_fake_run(monkeypatch, returncode=2, stdout="", stderr="boom: gate blew up")
    r = client.post("/api/v1/liveops/preview",
                    json={"venue": "binance", "strategy": "crowd_contra"}, headers=headers)
    body = r.json()
    assert r.status_code == 200                       # 端点本身工作;失败在载荷里诚实表达
    assert body["ok"] is False and body["returncode"] == 2
    assert "boom" in body["stdout_tail"] and body["status"] is None


def test_preview_driver_timeout_is_honest(client, headers, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _install_fake_run(monkeypatch,
                      raise_exc=subprocess.TimeoutExpired(cmd=["x"], timeout=600))
    r = client.post("/api/v1/liveops/preview",
                    json={"venue": "binance", "strategy": "crowd_contra"}, headers=headers)
    body = r.json()
    assert body["ok"] is False and body["returncode"] is None
    assert "timed out" in body["stdout_tail"]


def test_preview_tail_filters_credential_shaped_lines(client, headers, monkeypatch,
                                                      tmp_path):
    monkeypatch.chdir(tmp_path)
    _install_fake_run(monkeypatch, stdout="ok line\nBINANCE_MAINNET_API_SECRET=abc123\n"
                                          "sign signature=deadbeef\ntail line")
    r = client.post("/api/v1/liveops/preview",
                    json={"venue": "binance", "strategy": "crowd_contra"}, headers=headers)
    tail = r.json()["stdout_tail"]
    assert "ok line" in tail and "tail line" in tail
    assert "abc123" not in tail and "deadbeef" not in tail


def test_preview_single_flight_conflict(client, headers):
    ops._RUN_LOCK.acquire()
    try:
        r = client.post("/api/v1/liveops/preview",
                        json={"venue": "binance", "strategy": "crowd_contra"},
                        headers=headers)
        assert r.status_code == 409
    finally:
        ops._RUN_LOCK.release()


def test_preview_run_state_reflects_preview_not_submit(client, headers, monkeypatch,
                                                       tmp_path):
    monkeypatch.chdir(tmp_path)
    _install_fake_run(monkeypatch)
    client.post("/api/v1/liveops/preview",
                json={"venue": "binance", "strategy": "crowd_contra"}, headers=headers)
    assert ops._RUN_STATE["submit"] is False and ops._RUN_STATE["running"] is False
    assert ops._RUN_STATE["returncode"] == 0
