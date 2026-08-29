"""tools/investor_snapshot.py(W9 驱动器)单元测试。

全 hermetic:tmp_path 播种真实 LiveLedger/equity jsonl/status json/kill-switch
(权威 SQLite 与 legacy JSON 两路)+ 合成 degradation/go-live/cockpit artifact,
注入假 venue 注册表/路径函数——**绝不读写仓库真实的 data/cache 或 evidence/**。
钉死:端到端装配数字(手算可复核)、kill-switch 权威链/legacy 镜像/不可读 fail-closed
三路 provenance、无活动 venue 诚实 ABSENT、哈希与墙钟无关(同一数据截止点同哈希)。
"""
from __future__ import annotations

import json

import pytest

from live.execution.filters import client_order_id
from live.execution.position_ledger import LiveLedger
from quantos.contracts.killswitch import KillSwitchState
from quantos.contracts.trading import OrderSide
from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore
from tools import investor_snapshot as drv

DAY = 86_400_000
T0 = 1_780_000_000_000


def _seed_ledger(path):
    """手算可复核的小账本:T0+5h 买 5 BTC @102(决策价 100)。"""
    ledger = LiveLedger(str(path), create=True)
    cid = client_order_id("crowd_contra", T0, "BTC", OrderSide.BUY, 5.0)
    ledger.record_submission(cid, "BTC", OrderSide.BUY, 5.0, strategy_id="crowd_contra",
                             submitted_at=T0 + 5 * 3_600_000)
    ledger.update_status(cid, "FILLED")
    ledger.record_fill("f-1", cid, "BTC", OrderSide.BUY, 5.0, 102.0,
                       strategy_id="crowd_contra", filled_at=T0 + 6 * 3_600_000)
    ledger.record_tca("f-1", client_order_id=cid, instrument_id="BTC", side=OrderSide.BUY,
                      decision_price=100.0, fill_price=102.0, quantity=5.0,
                      strategy_id="crowd_contra", recorded_at=T0 + 6 * 3_600_000)
    ledger.close()
    return str(path)


def _seed_equity(path):
    rows = [{"ts": T0 + 1000, "equity": 1000.0, "available": 900.0},
            {"ts": T0 + 2000, "equity": 1001.0},                       # 同日第二条(取最后)
            {"ts": T0 + DAY + 1000, "equity": 990.0},
            "not-json",
            {"ts": T0 + 2 * DAY + 1000, "equity": 1010.0}]
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(r if isinstance(r, str) else json.dumps(r))
            fh.write("\n")
    return str(path)


def _seed_status(path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"ran_at": T0 + 2 * DAY, "mode": "submit", "strategy": "crowd_contra",
                   "venue": "fv",
                   "reconciliation": {"drift": {"BTC": 0.0}, "in_sync": True,
                                      "max_abs_drift": 0.0, "proceed": True},
                   "batch_skipped": None}, fh)
    return str(path)


def _seed_ks_authority(path):
    store = KillSwitchStateStore(str(path), create=True)
    store.bootstrap(KillSwitchState(), recorded_at=0)
    store.close()
    return str(path)


def _seed_ks_legacy(path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(KillSwitchState().model_dump_json())
    return str(path)


def _write_json(path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return str(path)


def _degradation_artifact(path):
    return _write_json(path, {
        "artifact": "LIVE_DEGRADATION", "generated_at_ms": T0 + 3 * DAY,
        "reports": [{
            "strategy_id": "crowd_contra", "venue": "fv",
            "window": {"n_days": 3, "mark_end_ts": T0 + 2 * DAY},
            "live_net_pnl": 200.0, "live_gross_pnl": 200.0,
            "replay_net_pnl": 209.0, "replay_gross_pnl": 210.0,
            "live_degradation_ratio": 200.0 / 209.0, "degradation_ratio_reason": None,
            "slippage_bps_weighted": 196.0, "n_orders": 1, "n_fills": 1,
            "components": {"fee_actual": None, "funding_actual": None,
                           "fee_expected": 0.51, "funding_expected": 0.0,
                           "slippage_cost_actual": 10.0},
            "daily": [{"t": T0 + i * DAY, "live_equity": 1000.0 + i,
                       "replay_equity": 1001.0 + i, "gap": -1.0} for i in range(3)],
        }],
    })


def _go_live_artifact(path):
    return _write_json(path, {
        "generated_at_ms": T0 + 3 * DAY,
        "venues": [{"venue": "fv", "mainnet": False,
                    "mandate": {"present": True, "expired": False,
                                "max_gross_notional": 10000.0,
                                "max_single_order_notional": 2500.0,
                                "max_net_notional": 3000.0,
                                "allowed_instruments": ["BTC"]},
                    "attestations": {"crowd_contra": {"present": True,
                                                      "kind": "human_override"}}}],
        "guidance": {"venue": "fv",
                     "capacity_safety": {"safe_capacity_est": 3.58e6, "passes": True}},
    })


def _cockpit_artifact(path):
    return _write_json(path, {
        "generated_at_ms": T0 + 3 * DAY,
        "alphas": [{"alpha_id": "crowd_contra", "derived_stage": "LIVE_TRACK"}],
    })


def _hermetic(tmp_path, *, ks="authority"):
    ledger = _seed_ledger(tmp_path / "live_track_fv.db")
    equity = _seed_equity(tmp_path / "live_equity_fv.jsonl")
    status = _seed_status(tmp_path / "live_track_status_fv.json")
    if ks == "authority":
        ks_json = str(tmp_path / "live_kill_switch_fv.json")
        _seed_ks_authority(tmp_path / "live_kill_switch_fv.db")
    elif ks == "legacy":
        ks_json = _seed_ks_legacy(tmp_path / "live_kill_switch_fv.json")
    else:                                                            # "unreadable"
        ks_json = str(tmp_path / "live_kill_switch_fv.json")
        with open(tmp_path / "live_kill_switch_fv.db", "wb") as fh:
            fh.write(b"not a sqlite database at all")
    paths = {"ledger": ledger, "equity": equity, "status": status, "kill_switch": ks_json}
    return {
        "venues_registry": {"fv": {"suffix": "_fv"}, "fv_empty": {"suffix": "_fv_empty"}},
        "venue_paths_fn": lambda suffix: (paths if suffix == "_fv" else {
            "ledger": str(tmp_path / "nope.db"), "equity": str(tmp_path / "nope.jsonl"),
            "status": str(tmp_path / "nope.json"),
            "kill_switch": str(tmp_path / "nope_ks.json")}),
        "degradation_path": _degradation_artifact(tmp_path / "live_degradation.json"),
        "go_live_path": _go_live_artifact(tmp_path / "go_live_readiness.json"),
        "cockpit_path": _cockpit_artifact(tmp_path / "alpha_lifecycle_cockpit.json"),
    }


# ---------------------------------------------------------------------------
# 端到端(手算可复核)
# ---------------------------------------------------------------------------

def test_build_snapshot_end_to_end(tmp_path):
    snap = drv.build_snapshot(now_ms=123, venues=["fv"], **_hermetic(tmp_path))
    assert snap["artifact"] == "INVESTOR_SNAPSHOT"
    assert snap["generated_at_ms"] == 123
    assert snap["readiness"] == "L2_DRY_RUN_ONLY"
    assert snap["data_cutoff_ms"] == T0 + 3 * DAY
    assert len(snap["report_snapshot_hash"]) == 128

    b = snap["venues"][0]
    assert b["venue"] == "fv" and b["status"] == "ACTIVE"
    assert b["ledger_root"] is not None
    # NAV:3 天,同日两条取最后(1001.0),最后一天 1010.0;flows 无记录 -> RAW basis
    assert b["nav"]["n_days"] == 3
    assert b["nav"]["days"][0]["nav_raw"] == 1001.0
    assert b["nav"]["days"][0]["n_samples"] == 2
    assert b["nav"]["last_nav"] == 1010.0
    assert b["nav"]["basis"] == "RAW_FLOWS_UNRECORDED"
    # 风险:1001 → 990(day1)dd=11 → 1010(day2)恢复,recovery=1
    assert b["risk"]["max_drawdown"] == pytest.approx(11.0)
    assert b["risk"]["recovery_days"] == 1 and b["risk"]["recovered"] is True
    assert b["risk"]["worst_day"]["pnl"] == pytest.approx(-11.0)
    # The injected W1/cockpit/capacity fixtures are self-described legacy
    # artifacts.  Live execution facts remain, but none of their backtest-
    # derived economics or lifecycle labels may be republished.
    assert b["decomposition"] == []
    assert b["three_line"] == []
    # 对账/事故/版本/容量
    assert b["reconciliation"]["in_sync"] is True
    assert b["reconciliation"]["as_of_ms"] == T0 + 2 * DAY
    assert b["incidents"]["provenance"] == "KILL_SWITCH_AUTHORITY_DB"
    assert b["incidents"]["tripped"] is False
    assert [t["action"] for t in b["incidents"]["transitions"]] == ["BOOTSTRAP"]
    assert len(b["versions"]) == 1
    assert b["versions"][0]["strategy_id"] == "crowd_contra"
    assert b["versions"][0]["derived_stage"] is None
    assert b["versions"][0]["strategy_version"] is None
    assert b["capacity"]["mandate_present"] is True
    assert b["capacity"]["capacity_safety"] is None
    # summary + unknown
    assert snap["summary"]["venues_active"] == ["fv"]
    assert snap["summary"]["total_last_nav"] == 1010.0
    assert "venue.fv.versions.crowd_contra.strategy_version" in snap["unknown_fields"]


def test_legacy_profitability_artifacts_are_not_republished(tmp_path):
    snap = drv.build_snapshot(now_ms=123, venues=["fv"], **_hermetic(tmp_path))
    encoded = json.dumps(snap, sort_keys=True)

    assert "209.0" not in encoded
    assert all(
        row["derived_stage"] is None
        for venue in snap["venues"]
        for row in venue["versions"]
    )
    assert "3580000.0" not in encoded


def test_build_snapshot_hash_independent_of_now_ms(tmp_path):
    s1 = drv.build_snapshot(now_ms=1, venues=["fv"], **_hermetic(tmp_path))
    s2 = drv.build_snapshot(now_ms=999999, venues=["fv"], **_hermetic(tmp_path))
    assert s1["report_snapshot_hash"] == s2["report_snapshot_hash"]


def test_kill_switch_legacy_json_fallback(tmp_path):
    snap = drv.build_snapshot(now_ms=1, venues=["fv"], **_hermetic(tmp_path, ks="legacy"))
    inc = snap["venues"][0]["incidents"]
    assert inc["provenance"] == "LEGACY_JSON_MIRROR"
    assert inc["tripped"] is False
    assert inc["transitions"] == []            # 默认未武装状态:armed_at=0 -> 无合成转移


def test_kill_switch_unreadable_authority_fail_closed(tmp_path):
    snap = drv.build_snapshot(now_ms=1, venues=["fv"], **_hermetic(tmp_path, ks="unreadable"))
    inc = snap["venues"][0]["incidents"]
    assert inc["provenance"] == "KILL_SWITCH_AUTHORITY_DB_UNREADABLE_FAIL_CLOSED"
    assert inc["tripped"] is True              # 仓库惯例:corrupt authority 读作 tripped
    assert snap["summary"]["kill_switch_tripped_any"] is True


def test_absent_venue_not_in_summary_active(tmp_path):
    snap = drv.build_snapshot(now_ms=1, **_hermetic(tmp_path))          # 全部注册 venue
    by_venue = {b["venue"]: b for b in snap["venues"]}
    assert by_venue["fv"]["status"] == "ACTIVE"
    assert by_venue["fv_empty"]["status"] == "ABSENT_NO_ACTIVITY"
    assert by_venue["fv_empty"]["nav"] is None
    assert snap["summary"]["n_venues"] == 2
    assert snap["summary"]["n_active_venues"] == 1
    # ABSENT venue 的 ledger_root 以显式 null 进快照级 root(哈希仍良定义)
    assert by_venue["fv_empty"]["ledger_root"] is None
    assert len(snap["report_snapshot_hash"]) == 128


def test_missing_evidence_artifacts_fail_soft(tmp_path):
    h = _hermetic(tmp_path)
    h["degradation_path"] = str(tmp_path / "missing1.json")
    h["go_live_path"] = str(tmp_path / "missing2.json")
    h["cockpit_path"] = str(tmp_path / "missing3.json")
    snap = drv.build_snapshot(now_ms=1, venues=["fv"], **h)
    b = snap["venues"][0]
    assert b["status"] == "ACTIVE"                       # 账本/权益仍在
    assert b["decomposition"] == [] and b["three_line"] == []
    assert b["capacity"]["mandate_present"] is False
    assert b["versions"][0]["derived_stage"] is None
    # data_cutoff 只剩账本/权益/status 时刻(无源 artifact generated_at);
    # 最大者 = 最后一条权益采样 ts(T0+2d+1000,> status.ran_at 的 T0+2d)
    assert snap["data_cutoff_ms"] == T0 + 2 * DAY + 1000
