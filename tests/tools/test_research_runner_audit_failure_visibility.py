"""research_runner 审计可见性回归(P2-15):best-effort ≠ 静默。

POST /run、POST /cancel 的治理触发记录是 best-effort(权威问责是 run 行上的 operator),但此前两处
`_audit.append(...)` 都被 `except Exception: pass` 吞掉——审计库故障在运维侧零信号(对照本文件
:129-130 的 `_LOG.exception(...)` 既有模式)。幂等重试更糟:同一个 Idempotency-Key 会解析回同一
run 行(JobQueue.submit 返回同一 run_id),`event_id=f"run.{run_id}"` 必然撞上 DurableAuditLog 的
event_id 唯一约束,于是一条**确定性**的重复事件异常被当成常态咽下去。

这里钉死两点:(1) append 失败必须写进 logger(不是 stdout);(2) 同一 event_id 的幂等重试成功、
只记一条,且**不再靠异常做控制流**(重复不再被 append 尝试)。HTTP 层用 stub queue,不真跑研究作业。
"""
import logging
import os

from fastapi.testclient import TestClient

import services.research_runner.main as m
from services.monitoring.audit_store import DurableAuditLog


class _StubQueue:
    """记录 submit 入参,不真跑作业;同一 invocation_id 返回同一 run_id,复现真实队列的幂等语义。"""
    def __init__(self):
        self.calls = []

    def submit(self, kind, params, now, operator=None, invocation_id=None):
        self.calls.append({"kind": kind, "params": params, "now": now, "operator": operator,
                           "invocation_id": invocation_id})
        return f"run_{invocation_id}" if invocation_id else f"run_{kind}_{now}"

    def cancel(self, run_id):
        self.calls.append({"cancel": run_id})
        return True


class _FailingAppendAudit:
    """审计存储写入故障:读接口可用、append 必炸(非重复 event_id 的那类故障)。"""
    @property
    def events(self):
        return ()

    def append(self, **kwargs):
        raise RuntimeError("audit store unavailable")


class _AppendCountingAudit:
    """真 durable store 外包一层 append 尝试计数:重复必须是"查过就不写",不是"写炸了再咽"。"""
    def __init__(self, inner):
        self._inner = inner
        self.attempts = []

    @property
    def events(self):
        return self._inner.events

    def chain_valid(self):
        return self._inner.chain_valid()

    def append(self, **kwargs):
        self.attempts.append(kwargs["event_id"])
        return self._inner.append(**kwargs)


def _client(tmp_path, monkeypatch, audit=None):
    stub = _StubQueue()
    monkeypatch.setattr(m, "_queue", stub)
    monkeypatch.setattr(m, "_audit",
                        audit or DurableAuditLog(os.path.join(str(tmp_path), "gov.db")))
    return TestClient(m.app), stub


def _post_run(client, key=None):
    headers = {"X-Operator": "alice", "X-Runner-Token": m._RUNNER_TOKEN}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post("/run", json={"kind": "data_refresh", "params": {}}, headers=headers)


def _errors(caplog):
    return [rec for rec in caplog.records if rec.levelno >= logging.ERROR]


def test_failing_audit_append_is_logged_not_swallowed(tmp_path, monkeypatch, caplog):
    client, stub = _client(tmp_path, monkeypatch, audit=_FailingAppendAudit())
    with caplog.at_level(logging.ERROR, logger=m.__name__):
        r = _post_run(client)
    # best-effort 语义不变:审计故障不得失败已持久化的 run(不 fail closed)
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    records = _errors(caplog)
    assert any(run_id in rec.getMessage() for rec in records), caplog.records
    assert any(rec.exc_info for rec in records), caplog.records          # :129-130 的 exception 模式


def test_idempotent_retry_records_one_event_without_attempting_a_duplicate(tmp_path, monkeypatch):
    client, stub = _client(tmp_path, monkeypatch,
                           audit=_AppendCountingAudit(DurableAuditLog(
                               os.path.join(str(tmp_path), "gov.db"))))
    audit = m._audit
    first = _post_run(client, key="retry-key")
    second = _post_run(client, key="retry-key")
    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    run_id = first.json()["run_id"]
    assert second.json()["run_id"] == run_id                     # 幂等:重试解析回同一 run 行
    # 重复路径不是异常:第二次调用根本不发起 duplicate append(旧行为=append 抛 ValueError 被咽下)
    assert audit.attempts == [f"run.{run_id}"]
    assert [ev.event_id for ev in audit.events] == [f"run.{run_id}"]
    assert audit.chain_valid()


def test_cancel_audit_outage_is_logged_not_swallowed(tmp_path, monkeypatch, caplog):
    client, stub = _client(tmp_path, monkeypatch)
    m._audit.close()                                            # 真存储故障:连接已关闭
    with caplog.at_level(logging.ERROR, logger=m.__name__):
        r = client.post("/cancel", json={"run_id": "run_x"},
                        headers={"X-Operator": "alice", "X-Runner-Token": m._RUNNER_TOKEN})
    assert r.status_code == 200, r.text
    assert r.json()["cancelled"] is True
    assert any("run_x" in rec.getMessage() for rec in _errors(caplog)), caplog.records
