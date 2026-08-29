"""research_runner 调用方身份 + 审计(路线图 Rank #18)。

唯一可变面(POST /run)必须记录 WHO:强制 X-Operator(自述式、未鉴权的问责标签,非 RBAC),
缺失即 400;operator 持久到 run 行(权威问责)+ 向 #20 的 durable 审计链补一条 research.run.triggered。
HTTP 层用 stub queue 验证(不真跑研究作业);另做 store 往返验证 operator 落盘。"""
import os

from fastapi.testclient import TestClient

import services.research_runner.main as m
from services.monitoring.audit_store import DurableAuditLog
from services.research.run_registry.contract import ResearchRun
from services.research.run_registry.store import ResearchRunStore


class _StubQueue:
    """记录 submit 的入参,不真的跑作业(避免触发真实研究/读真库)。"""
    def __init__(self):
        self.calls = []

    def submit(self, kind, params, now, operator=None, invocation_id=None):
        self.calls.append({"kind": kind, "params": params, "now": now, "operator": operator,
                           "invocation_id": invocation_id})
        return f"run_{kind}_{now}"


def _client(tmp_path, monkeypatch):
    stub = _StubQueue()
    monkeypatch.setattr(m, "_queue", stub)
    monkeypatch.setattr(m, "_audit", DurableAuditLog(os.path.join(str(tmp_path), "gov.db")))
    return TestClient(m.app), stub


def test_post_run_without_operator_header_is_400(tmp_path, monkeypatch):
    client, stub = _client(tmp_path, monkeypatch)
    r = client.post("/run", json={"kind": "carry_strategy", "params": {}},
                    headers={"X-Runner-Token": m._RUNNER_TOKEN})
    assert r.status_code == 400
    assert "X-Operator" in r.json()["detail"]
    assert stub.calls == []                                  # never reached submit


def test_post_run_with_blank_operator_is_400(tmp_path, monkeypatch):
    client, stub = _client(tmp_path, monkeypatch)
    r = client.post("/run", json={"kind": "carry_strategy", "params": {}},
                    headers={"X-Operator": "   ", "X-Runner-Token": m._RUNNER_TOKEN})
    assert r.status_code == 400
    assert stub.calls == []


def test_post_run_threads_operator_and_records_audit(tmp_path, monkeypatch):
    client, stub = _client(tmp_path, monkeypatch)
    r = client.post("/run", json={"kind": "data_refresh", "params": {}},
                    headers={"X-Operator": "alice", "X-Runner-Token": m._RUNNER_TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body["operator"] == "alice"
    # operator threaded into the queue
    assert stub.calls[0]["operator"] == "alice"
    # governance audit chain carries the trigger event
    evs = m._audit.events
    assert len(evs) == 1
    assert evs[0].action == "research.run.triggered"
    assert evs[0].actor == "alice"
    assert evs[0].target == body["run_id"]
    assert evs[0].metadata["kind"] == "data_refresh"
    assert m._audit.chain_valid()


def test_store_roundtrips_operator(tmp_path):
    store = ResearchRunStore(os.path.join(str(tmp_path), "runs.db"))
    store.upsert(ResearchRun(run_id="run_x", kind="carry_strategy", title="t", params={},
                             status="queued", created_ts=1, finished_ts=None, metrics={}, series={},
                             verdict="", caveats=[], provenance="e", operator="bob"))
    got = store.get("run_x")
    assert got is not None and got.operator == "bob"


def test_legacy_run_row_without_operator_reads_none(tmp_path):
    # a row persisted before Rank #18 (no operator key in payload_json) must read back as None, not crash
    store = ResearchRunStore(os.path.join(str(tmp_path), "legacy.db"))
    store._c.execute(
        "INSERT INTO runs (run_id,kind,title,status,created_ts,finished_ts,verdict,provenance,error,"
        "payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("old", "carry_strategy", "t", "done", 1, 1, "", "e", None,
         '{"params":{},"metrics":{},"series":{},"caveats":[],"log":[],"code_version":"abc"}'))
    store._c.commit()
    got = store.get("old")
    assert got is not None and got.operator is None
