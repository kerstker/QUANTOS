"""research_runner 本机共享密钥认证(2026-07-06,安全审查 punch-list:此前 POST /run 和
/cancel 与 live_ops_api 修复前同款——同机任意进程零门槛可触发。研究作业不碰订单/credential/
lifecycle(governance boundary test 钉死),但仍是真实计算资源(CPU/时间),且 /cancel 能
撤销别人的合法任务。镜像 live_ops_api 的本机共享密钥模式(`key/<service>_token.txt`,首次
启动生成,gitignored,只检查不外泄)。/health 是静态元数据,不需要认证。"""
import os
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

import services.research_runner.main as m
from services.monitoring.audit_store import DurableAuditLog
from services.research.run_registry.contract import InvocationConflictError


class _StubQueue:
    def __init__(self):
        self.calls = []

    def submit(self, kind, params, now, operator=None, invocation_id=None):
        self.calls.append({"kind": kind, "params": params, "now": now, "operator": operator,
                           "invocation_id": invocation_id})
        if invocation_id == "conflicting-key":
            raise InvocationConflictError("invocation conflict")
        return f"run_{kind}_{now}"

    def cancel(self, run_id):
        self.calls.append({"cancel": run_id})
        return True


def _client(tmp_path, monkeypatch):
    stub = _StubQueue()
    monkeypatch.setattr(m, "_queue", stub)
    monkeypatch.setattr(m, "_audit", DurableAuditLog(os.path.join(str(tmp_path), "gov.db")))
    return TestClient(m.app), stub


def test_managed_runner_requires_preprovisioned_private_token(
    tmp_path: Path, monkeypatch
):
    token = tmp_path / "runner-token"
    monkeypatch.setenv("QUANTOS_REQUIRE_EXISTING_RUNNER_TOKEN", "true")

    with pytest.raises(RuntimeError, match="pre-provisioned"):
        m._load_or_create_token(str(token))

    token.write_text("managed-secret", encoding="utf-8")
    token.chmod(0o644)
    with pytest.raises(RuntimeError, match="0600"):
        m._load_or_create_token(str(token))

    token.chmod(0o600)
    assert m._load_or_create_token(str(token)) == "managed-secret"


def test_post_run_without_token_is_401(tmp_path, monkeypatch):
    client, stub = _client(tmp_path, monkeypatch)
    r = client.post("/run", json={"kind": "carry_strategy", "params": {}},
                    headers={"X-Operator": "alice"})
    assert r.status_code == 401
    assert stub.calls == []


def test_post_run_with_wrong_token_is_401(tmp_path, monkeypatch):
    client, stub = _client(tmp_path, monkeypatch)
    r = client.post("/run", json={"kind": "carry_strategy", "params": {}},
                    headers={"X-Operator": "alice", "X-Runner-Token": "wrong"})
    assert r.status_code == 401
    assert stub.calls == []


def test_post_run_with_correct_token_succeeds(tmp_path, monkeypatch):
    client, stub = _client(tmp_path, monkeypatch)
    r = client.post("/run", json={"kind": "data_refresh", "params": {}},
                    headers={"X-Operator": "alice", "X-Runner-Token": m._RUNNER_TOKEN})
    assert r.status_code == 200
    assert stub.calls[0]["operator"] == "alice"
    assert r.json()["purpose"] == "DIAGNOSTIC_ONLY"
    assert r.json()["selection_eligible"] is False


def test_post_run_maps_idempotency_conflict_to_409(tmp_path, monkeypatch):
    client, stub = _client(tmp_path, monkeypatch)
    response = client.post(
        "/run",
        json={"kind": "data_refresh", "params": {}},
        headers={
            "X-Operator": "alice",
            "X-Runner-Token": m._RUNNER_TOKEN,
            "Idempotency-Key": "conflicting-key",
        },
    )
    assert response.status_code == 409
    assert "invocation conflict" in response.json()["detail"]
    assert stub.calls[0]["invocation_id"] == "conflicting-key"


def test_post_cancel_without_token_is_401(tmp_path, monkeypatch):
    client, stub = _client(tmp_path, monkeypatch)
    r = client.post("/cancel", json={"run_id": "run_1"}, headers={"X-Operator": "alice"})
    assert r.status_code == 401
    assert stub.calls == []


def test_post_cancel_with_correct_token_succeeds(tmp_path, monkeypatch):
    client, stub = _client(tmp_path, monkeypatch)
    r = client.post("/cancel", json={"run_id": "run_1"},
                    headers={"X-Operator": "alice", "X-Runner-Token": m._RUNNER_TOKEN})
    assert r.status_code == 200


def test_health_needs_no_token(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200
    policies = r.json()["kind_policies"]
    assert r.json()["kinds"] == [
        "ashare_forward_observation",
        "ashare_technical_search",
        "data_refresh",
        "perpetual_replay",
    ]
    assert r.json()["quarantined_kinds"] == {
        "ashare_alpha": "INVALIDATED_PERP_LEDGER_V1",
        "ashare_paper_track": "INVALIDATED_PERP_LEDGER_V1",
        "carry_strategy": "INVALIDATED_PERP_LEDGER_V1",
        "combo_book": "INVALIDATED_PERP_LEDGER_V1",
        "factory_loop": "INVALIDATED_PERP_LEDGER_V1",
        "factory_orthogonal": "INVALIDATED_PERP_LEDGER_V1",
        "ic_panel": "INVALIDATED_PERP_LEDGER_V1",
        "oi_research": "INVALIDATED_PERP_LEDGER_V1",
        "paper_cycle": "INVALIDATED_PERP_LEDGER_V1",
        "survivorship": "INVALIDATED_PERP_LEDGER_V1",
        "wide_gauntlet": "INVALIDATED_PERP_LEDGER_V1",
    }
    assert policies["factory_loop"]["purpose"] == "SELECTION_BEARING"
    assert policies["factory_loop"]["evidence_authority"] == (
        "EXPERIMENT_REGISTRY_GLOBAL_TRIAL_LEDGER"
    )
    assert policies["ic_panel"] == {
        "purpose": "DIAGNOSTIC_ONLY",
        "evidence_authority": None,
    }


def test_post_run_quarantines_legacy_kind_before_stub_queue(tmp_path, monkeypatch):
    client, stub = _client(tmp_path, monkeypatch)
    response = client.post(
        "/run",
        json={"kind": "carry_strategy", "params": {}},
        headers={"X-Operator": "alice", "X-Runner-Token": m._RUNNER_TOKEN},
    )

    assert response.status_code == 409
    assert "INVALIDATED_PERP_LEDGER_V1" in response.json()["detail"]
    assert stub.calls == []
