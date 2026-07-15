"""审计 2026-07-07 P0-1 回归测试。

两块自称 REAL 的安全看板此前用 `KillSwitch()` 不传参数构造 —— `KillSwitch.__init__` 的
`state` 缺省 `None` 时用一个全新的 `KillSwitchState()`(从未触发),于是即便实盘 kill-switch
早已跳闸,这两块看板永远读到"未触发/健康":
  - `_real_monitoring_snapshot`(经 `_system_health_payload` 喂 `/api/v1/console/system-health`
    的 `monitoring` 字段 —— 审计原文写的是 `/api/v1/console/monitoring-drift` 端点,但实际那个
    端点由完全不碰 kill-switch 的独立函数 `_monitoring_drift_payload` 提供;这里按实际代码路径
    订正,`_real_monitoring_snapshot` 真正喂的是 `/api/v1/console/system-health`)
  - `_safety_primitives_payload`(喂 `/api/v1/console/safety-primitives`,`Risk.tsx` 熔断器卡片
    直接渲染其 `kill_switch.is_active`/`trip_count` —— 这是审计里最危险的那条真实渲染路径)

真正跨进程持久化的实盘 kill-switch 状态由 `tools/live_track_advance.py` /
`tools/live_risk_sentinel.py` 原子写入 `data/cache/live_kill_switch.db`;同名 JSON 只是兼容镜像。
`services/` 不得 import `live/`
(单向边界,`tests/governance/test_live_execution_boundary.py::test_services_tree_does_not_import_live`
+ `tests/governance/test_domain_import_boundary.py::test_services_does_not_import_live` 机器钉死),
所以 console_api 直接使用 risk 域的 append-only SQLite store;只有数据库尚未创建时才读取旧 JSON,
不导入 `live/` 的任何 Python 代码。数据库存在但打不开/链损坏时必须 fail-closed,不得回退旧镜像。

所有测试用 monkeypatch 把路径常量指向 tmp_path,不碰真实 data/cache/。
"""
from fastapi.testclient import TestClient

from quantos.contracts.killswitch import KillSwitchState
from services.console.main import app
from services.monitoring.audit_store import DurableAuditLog
from services.console.api import main as capi
from services.risk.pre_trade.kill_switch_store import (
    KillSwitchStateStore,
    kill_switch_authority_path,
)

client = TestClient(app)

TRIPPED = KillSwitchState(tripped=True, incident_id="incident-1", reason="drawdown breach",
                          armed_at=1000, trip_count=3)


def _write_state(path, state: KillSwitchState) -> KillSwitchState:
    path.write_text(state.model_dump_json(), encoding="utf-8")
    return state


def _write_authority(path, state: KillSwitchState) -> KillSwitchState:
    store = KillSwitchStateStore(kill_switch_authority_path(str(path)), create=True)
    try:
        store.bootstrap(state, recorded_at=max(state.armed_at, state.reset_at))
    finally:
        store.close()
    return state


# ---------------------------------------------------------------------------
# _safety_primitives_payload  ->  /api/v1/console/safety-primitives  ->  Risk.tsx
# ---------------------------------------------------------------------------

def test_safety_primitives_reports_tripped_when_persisted_state_is_tripped(tmp_path, monkeypatch):
    path = tmp_path / "live_kill_switch.json"
    _write_state(path, TRIPPED)
    monkeypatch.setattr(capi, "_LIVE_KILL_SWITCH_STATE", str(path))

    payload = capi._safety_primitives_payload()

    assert payload["kill_switch"]["is_active"] is True
    assert payload["kill_switch"]["trip_count"] == 3


def test_safety_primitives_reports_clear_when_no_persisted_file(tmp_path, monkeypatch):
    monkeypatch.setattr(capi, "_LIVE_KILL_SWITCH_STATE", str(tmp_path / "never_written.json"))

    payload = capi._safety_primitives_payload()

    assert payload["kill_switch"]["is_active"] is False
    assert payload["kill_switch"]["trip_count"] == 0


def test_safety_primitives_fails_closed_on_corrupt_state_file(tmp_path, monkeypatch):
    path = tmp_path / "live_kill_switch.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(capi, "_LIVE_KILL_SWITCH_STATE", str(path))

    payload = capi._safety_primitives_payload()

    # 状态文件损坏必须读成"已触发"—— 安全状态损坏绝不能被解读为"安全"
    assert payload["kill_switch"]["is_active"] is True


def test_safety_primitives_prefers_authority_over_stale_clear_mirror(tmp_path, monkeypatch):
    path = tmp_path / "live_kill_switch.json"
    _write_state(path, KillSwitchState())
    _write_authority(path, TRIPPED)
    monkeypatch.setattr(capi, "_LIVE_KILL_SWITCH_STATE", str(path))

    payload = capi._safety_primitives_payload()

    assert payload["kill_switch"]["is_active"] is True
    assert payload["kill_switch"]["trip_count"] == 3


def test_safety_primitives_prefers_clear_authority_over_stale_tripped_mirror(tmp_path, monkeypatch):
    path = tmp_path / "live_kill_switch.json"
    _write_state(path, TRIPPED)
    _write_authority(path, KillSwitchState())
    monkeypatch.setattr(capi, "_LIVE_KILL_SWITCH_STATE", str(path))

    payload = capi._safety_primitives_payload()

    assert payload["kill_switch"]["is_active"] is False
    assert payload["kill_switch"]["trip_count"] == 0


def test_safety_primitives_corrupt_authority_overrides_valid_clear_mirror(tmp_path, monkeypatch):
    path = tmp_path / "live_kill_switch.json"
    _write_state(path, KillSwitchState())
    authority = tmp_path / "live_kill_switch.db"
    authority.write_bytes(b"not-a-sqlite-database")
    monkeypatch.setattr(capi, "_LIVE_KILL_SWITCH_STATE", str(path))

    payload = capi._safety_primitives_payload()

    assert payload["kill_switch"]["is_active"] is True


def test_safety_primitives_endpoint_reflects_tripped_state_end_to_end(tmp_path, monkeypatch):
    path = tmp_path / "live_kill_switch.json"
    _write_state(path, TRIPPED)
    monkeypatch.setattr(capi, "_LIVE_KILL_SWITCH_STATE", str(path))

    body = client.get("/api/v1/console/safety-primitives").json()

    assert body["kill_switch"]["is_active"] is True
    assert body["data_provenance"] == "REAL_SAFETY_PRIMITIVES"


# ---------------------------------------------------------------------------
# _real_monitoring_snapshot  ->  _system_health_payload  ->  /api/v1/console/system-health
# ---------------------------------------------------------------------------

def test_monitoring_snapshot_no_audit_chain_branch_reflects_tripped_kill_switch(tmp_path, monkeypatch):
    # 这个 early-return 分支此前无条件硬编码 kill_switch_active=False,压根不看真实状态
    ks_path = tmp_path / "live_kill_switch.json"
    _write_state(ks_path, TRIPPED)
    monkeypatch.setattr(capi, "_LIVE_KILL_SWITCH_STATE", str(ks_path))
    monkeypatch.setattr(capi, "_GOVERNANCE_AUDIT_DB", str(tmp_path / "no_such_audit.db"))

    snap = capi._real_monitoring_snapshot()

    assert snap["provenance"] == "NO_DURABLE_AUDIT_CHAIN"
    assert snap["kill_switch_active"] is True


def test_monitoring_snapshot_with_audit_chain_reflects_tripped_kill_switch(tmp_path, monkeypatch):
    ks_path = tmp_path / "live_kill_switch.json"
    _write_state(ks_path, TRIPPED)
    monkeypatch.setattr(capi, "_LIVE_KILL_SWITCH_STATE", str(ks_path))

    audit_path = tmp_path / "governance_audit.db"
    DurableAuditLog(str(audit_path)).append(event_id="e1", actor="human:ops", action="note", timestamp=1)
    monkeypatch.setattr(capi, "_GOVERNANCE_AUDIT_DB", str(audit_path))

    snap = capi._real_monitoring_snapshot()

    assert snap["provenance"] == "REAL_MONITORING_SNAPSHOT"
    assert snap["kill_switch_active"] is True


def test_monitoring_snapshot_reports_clear_when_no_persisted_file(tmp_path, monkeypatch):
    monkeypatch.setattr(capi, "_LIVE_KILL_SWITCH_STATE", str(tmp_path / "never_written.json"))
    monkeypatch.setattr(capi, "_GOVERNANCE_AUDIT_DB", str(tmp_path / "no_such_audit.db"))

    snap = capi._real_monitoring_snapshot()

    assert snap["kill_switch_active"] is False
