"""tools/alpha_lifecycle_cockpit.py(2026-07-24,Alpha 生命周期驾驶舱后端驱动器)的单元测试。

全部 hermetic:tmp_path fixture + 显式 Paths 注入 + 微型 sqlite 种子库——**绝不读写仓库真实的
data/cache 或 evidence/ 文件**。覆盖:
- 阶段 ABSENT 处理(缺文件/缺表/坏 JSON 绝不崩)
- derive_stage 推导规则全阶梯(LIVE_TRACK > PAPER_TRACKED > DRY_RUN > VALIDATED > CANDIDATE > UNTRACKED)
- dry_run / paper_track sqlite 解析(canonical run_id 优先、(timestamp, run_id) 兜底、窗口端点、标量)
- falsification verdict_pass 三规则(checks 全过 / n/m 计数 / 研究 verdict 兜底)
- live status glob 归属(strategy 字段匹配、无 strategy 跳过、文件名推 venue)
- maintenance 衰退/退役(空权威 / 有动作 / 缺库)
- warnings(证伪未过、阶段跳跃、RISK_REJECTED、前向不足、合成时间戳、hard_stop)
- build_report 装配(全证据 tmp 组装 + 空目录全 ABSENT)
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from tools import alpha_lifecycle_cockpit as alc


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _seed_dry_run_db(path, rows):
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE dry_run (run_id TEXT, strategy_id TEXT, timestamp INTEGER, "
        "n_intents INTEGER, n_approved INTEGER, n_rejected INTEGER, rejected_reasons TEXT, "
        "pnl TEXT, audit_root TEXT, detail TEXT)")
    for row in rows:
        con.execute(
            "INSERT INTO dry_run (run_id, strategy_id, timestamp, n_intents, n_approved, "
            "n_rejected, rejected_reasons, pnl, audit_root, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row["run_id"], row["strategy_id"], row["timestamp"], row.get("n_intents", 3),
             row.get("n_approved", 3), row.get("n_rejected", 0), None,
             json.dumps(row.get("pnl", {})), None, json.dumps(row.get("detail", {}))))
    con.commit()
    con.close()
    return str(path)


def _dry_row(run_id, strategy_id, timestamp, *, sharpe=1.0, decision="RISK_APPROVED"):
    return {
        "run_id": run_id, "strategy_id": strategy_id, "timestamp": timestamp,
        "pnl": {"total_return": 0.5, "sharpe": sharpe, "max_drawdown": 0.1,
                "sharpe_vt": sharpe + 0.1, "max_drawdown_vt": 0.09, "final_equity": 1.5e6,
                "total_funding": 12.0, "n_periods": 2, "residual": 1e-09,
                "avg_net_exposure_pct": 0.01},
        "detail": {"mode": "multi_period", "cadence": "weekly",
                   "equity_curve": [{"t": 1000, "equity": 1.0}, {"t": 2000, "equity": 1.5}],
                   "research_risk": {"decision": decision},
                   "cost_model": "FLAT_BPS", "cost_bps": 10.0,
                   "crowding_gate": {"n_pass": 5, "n_warn": 1, "n_block": 2, "worst_score": 0.9}},
    }


def _seed_paper_db(path, *, track_rows=(), period_rows=()):
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE paper_track (alpha_id TEXT, start_ts INTEGER, last_ts INTEGER, "
        "n_periods INTEGER, cum_return REAL, paper_sharpe REAL, paper_ic REAL, "
        "backtest_sharpe REAL, perf_gap REAL)")
    con.execute(
        "CREATE TABLE paper_periods (alpha_id TEXT, as_of_ts INTEGER, realized_return REAL, "
        "realized_ic REAL, source TEXT)")
    for r in track_rows:
        con.execute("INSERT INTO paper_track VALUES (?,?,?,?,?,?,?,?,?)", r)
    for r in period_rows:
        con.execute("INSERT INTO paper_periods VALUES (?,?,?,?,?)", r)
    con.commit()
    con.close()
    return str(path)


def _seed_maintenance_db(path, snapshot, events):
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE maintenance_records (record_seq INTEGER, snapshot_json TEXT, events_json TEXT)")
    con.execute("INSERT INTO maintenance_records VALUES (?,?,?)",
                (1, json.dumps(snapshot), json.dumps(events)))
    con.commit()
    con.close()
    return str(path)


def _stage(status, **kw):
    return {"status": status, **kw}


# ---------------------------------------------------------------------------
# fail-soft 基础
# ---------------------------------------------------------------------------

def test_load_json_or_none_fail_soft(tmp_path):
    assert alc._load_json_or_none(str(tmp_path / "missing.json")) is None
    bad = _write_json(tmp_path / "bad.json", {})
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert alc._load_json_or_none(bad) is None
    _write_json(tmp_path / "list.json", [1, 2])
    assert alc._load_json_or_none(str(tmp_path / "list.json")) is None


def test_read_sqlite_missing_file_and_missing_table(tmp_path):
    assert alc._read_sqlite(str(tmp_path / "nope.db"), "SELECT 1") == []
    con = sqlite3.connect(str(tmp_path / "empty.db"))
    con.execute("CREATE TABLE other (x TEXT)")
    con.commit()
    con.close()
    assert alc._read_sqlite(str(tmp_path / "empty.db"), "SELECT * FROM dry_run") == []


def test_sanitize_replaces_non_finite():
    assert alc._sanitize({"a": float("nan"), "b": [float("inf"), 1.5], "c": "x"}) == {
        "a": None, "b": [None, 1.5], "c": "x"}


# ---------------------------------------------------------------------------
# derive_stage 推导规则
# ---------------------------------------------------------------------------

_ABSENT_STAGES = {s: {"status": "ABSENT"} for s in
                  ("discovery", "falsification", "stress", "backtest", "capacity",
                   "portfolio", "paper", "live", "decay", "retirement")}


def _stages(**overrides):
    stages = {k: dict(v) for k, v in _ABSENT_STAGES.items()}
    stages.update(overrides)
    return stages


@pytest.mark.parametrize("stages,expected", [
    (_stages(), "UNTRACKED"),
    (_stages(discovery=_stage("PRESENT", mean_ic=0.01)), "CANDIDATE"),
    (_stages(falsification=_stage("PRESENT", verdict_pass=False)), "CANDIDATE"),
    (_stages(falsification=_stage("PRESENT", verdict_pass=True)), "VALIDATED"),
    (_stages(falsification=_stage("PRESENT", verdict_pass=False),
             backtest=_stage("PRESENT")), "DRY_RUN"),
    (_stages(backtest=_stage("PRESENT")), "DRY_RUN"),
    (_stages(backtest=_stage("PRESENT"), paper=_stage("PRESENT", n_periods=0)), "DRY_RUN"),
    (_stages(backtest=_stage("PRESENT"), paper=_stage("PRESENT", n_periods=3)), "PAPER_TRACKED"),
    (_stages(paper=_stage("PRESENT", n_periods=3)), "PAPER_TRACKED"),
    (_stages(backtest=_stage("PRESENT"), paper=_stage("PRESENT", n_periods=3),
             live=_stage("PRESENT", runs=[{"venue": "binance"}])), "LIVE_TRACK"),
    (_stages(live=_stage("PRESENT", runs=[]), paper=_stage("PRESENT", n_periods=1)),
     "PAPER_TRACKED"),
])
def test_derive_stage_ladder(stages, expected):
    assert alc.derive_stage(stages) == expected


# ---------------------------------------------------------------------------
# 各阶段函数
# ---------------------------------------------------------------------------

def test_discovery_stage_present_and_absent():
    rows = {"crowd_contra": {"universe": "crypto_oi_majors", "mean_ic": 0.035, "icir": 0.2,
                             "t_stat": 4.32, "hit_rate": 0.55, "n_periods": 157,
                             "n_instruments": 24, "per_year": {"2024": 0.05},
                             "by_period": {"7": {"mean_ic": 0.03, "t_stat": 2.7}}}}
    out = alc.discovery_stage("crowd_contra", rows, source="ic.json")
    assert out["status"] == "PRESENT"
    assert out["t_stat"] == 4.32 and out["by_holding_period"]["7"]["mean_ic"] == 0.03
    assert alc.discovery_stage(None, rows, source="ic.json") == {"status": "ABSENT"}
    assert alc.discovery_stage("ghost", rows, source="ic.json") == {"status": "ABSENT"}


def test_falsification_checks_all_pass():
    verify = {"refutation_checks": {"a": True, "b": True, "c": True}, "verdict": "SURVIVES 3/3"}
    out = alc.falsification_stage("oi_verify", oi_verify=verify, oi_top_verify=None,
                                  paper_entry=None, oi_verify_path="v.json",
                                  oi_top_verify_path="t.json")
    assert out["status"] == "PRESENT"
    assert (out["n_checks_pass"], out["n_checks_total"], out["verdict_pass"]) == (3, 3, True)


def test_falsification_dedicated_partial_from_paper_cycle():
    paper_entry = {"research": {"verdict": "PASS_WITH_WARNINGS", "evidence": {
        "dedicated_refutation_pass_count": 4,
        "dedicated_refutation_verdict": "SURVIVES 4/5 refutation checks"}}}
    out = alc.falsification_stage("oi_top_verify", oi_verify=None,
                                  oi_top_verify={"baseline": {"price_sharpe": 0.9}},
                                  paper_entry=paper_entry, oi_verify_path="v.json",
                                  oi_top_verify_path="t.json")
    assert out["status"] == "PRESENT"
    assert (out["n_checks_pass"], out["n_checks_total"]) == (4, 5)
    assert out["verdict_pass"] is False


def test_falsification_research_verdict_fallback():
    paper_entry = {"research": {"verdict": "PASS_WITH_WARNINGS", "evidence": {}, "warnings": ["w"]}}
    out = alc.falsification_stage(None, oi_verify=None, oi_top_verify=None,
                                  paper_entry=paper_entry, oi_verify_path="v.json",
                                  oi_top_verify_path="t.json")
    assert out["status"] == "PRESENT"
    assert out["verdict_pass"] is True and out["research_warnings"] == ["w"]
    assert alc.falsification_stage(None, oi_verify=None, oi_top_verify=None,
                                   paper_entry=None, oi_verify_path="v.json",
                                   oi_top_verify_path="t.json") == {"status": "ABSENT"}


def test_stress_stage():
    scenario = {"provenance": "REAL_HISTORICAL_REPLAY",
                "books": {"carry": {"n_periods": 313, "hill_tail_index": 2.43,
                                    "lower_tail_dep_vs_btc": 0.129,
                                    "crises": {"2022-11 FTX": {"total_return": -0.03}},
                                    "worst_drawdown_window": {"drawdown": 0.19},
                                    "var_cvar_95": {"var": 0.03}}}}
    out = alc.stress_stage("carry", scenario, source="s.json")
    assert out["status"] == "PRESENT" and out["hill_tail_index"] == 2.43
    assert alc.stress_stage("top_contra", scenario, source="s.json") == {"status": "ABSENT"}
    assert alc.stress_stage(None, scenario, source="s.json") == {"status": "ABSENT"}
    assert alc.stress_stage("carry", None, source="s.json") == {"status": "ABSENT"}


def test_backtest_stage_canonical_preferred(tmp_path):
    db = _seed_dry_run_db(tmp_path / "dry_run.db", [
        _dry_row("run_a", "carry", 100, sharpe=0.4),
        _dry_row("run_b", "carry", 100, sharpe=0.7),
        _dry_row("run_c", "other", 100),
    ])
    rows = alc._read_sqlite(db, "SELECT run_id, strategy_id, timestamp, n_intents, n_approved, "
                                "n_rejected, pnl, detail FROM dry_run")
    by_strategy = {}
    for r in rows:
        by_strategy.setdefault(r["strategy_id"], []).append(r)
    out = alc.backtest_stage("carry", by_strategy, source=db, canonical_run_id="run_a")
    assert out["status"] == "PRESENT"
    assert out["run_id"] == "run_a" and out["canonical"] is True
    assert out["sharpe_raw"] == 0.4 and out["n_runs_for_strategy"] == 2
    # 无 canonical -> (timestamp, run_id) 最大者
    out2 = alc.backtest_stage("carry", by_strategy, source=db, canonical_run_id=None)
    assert out2["run_id"] == "run_b" and out2["canonical"] is False
    # 标量 + 窗口端点 + research-risk + crowding
    assert (out["window_start_ts"], out["window_end_ts"]) == (1000, 2000)
    assert out["research_risk_decision"] == "RISK_APPROVED"
    assert out["crowding_gate"] == {"n_pass": 5, "n_warn": 1, "n_block": 2, "worst_score": 0.9}
    assert out["reconciliation_residual"] == 1e-09
    # strategy 无行 -> ABSENT
    assert alc.backtest_stage("ghost", by_strategy, source=db,
                              canonical_run_id=None) == {"status": "ABSENT"}


def test_paper_stage_seeded(tmp_path):
    db = _seed_paper_db(
        tmp_path / "paper.db",
        track_rows=[("a1", 100, 300, 3, 0.02, 1.1, 0.05, 0.9, -0.2)],
        period_rows=[("a1", 100, 0.01, 0.1, "FORWARD_LIVE"),
                     ("a1", 200, 0.005, 0.2, "FORWARD_LIVE"),
                     ("a1", 300, 0.005, 0.15, "FORWARD_LIVE")])
    track = {r["alpha_id"]: dict(r) for r in alc._read_sqlite(db, "SELECT * FROM paper_track")}
    periods = {}
    for r in alc._read_sqlite(db, "SELECT * FROM paper_periods ORDER BY as_of_ts"):
        periods.setdefault(r["alpha_id"], []).append(dict(r))
    out = alc.paper_stage("a1", track, periods, source=db, paper_entry=None,
                          required_forward_periods=8)
    assert out["status"] == "PRESENT" and out["n_periods"] == 3
    assert out["track"]["paper_sharpe"] == 1.1
    assert out["last_period"]["as_of_ts"] == 300 and out["last_period"]["realized_ic"] == 0.15
    # 只有 forward 块(track/periods 皆无)仍 PRESENT,n_periods=0
    entry = {"forward": {"n_forward_periods": 0, "evidence_state": "DATA_BLOCKED"},
             "paper_verdict": "BLOCKED", "blockers": ["X"]}
    out2 = alc.paper_stage("ghost", {}, {}, source=db, paper_entry=entry,
                           required_forward_periods=8)
    assert out2["status"] == "PRESENT" and out2["n_periods"] == 0
    assert out2["forward"]["paper_verdict"] == "BLOCKED" and out2["forward"]["blockers"] == ["X"]
    assert alc.paper_stage("ghost", {}, {}, source=db, paper_entry=None,
                           required_forward_periods=8) == {"status": "ABSENT"}


def test_load_live_status_runs_attribution(tmp_path):
    _write_json(tmp_path / "live_track_status.json",
                {"strategy": "crowd_contra", "mode": "submit", "tier": "AUTO",
                 "n_submitted": 14, "n_pending_review": 0, "n_rejected_by_gate": 0,
                 "order_stats": {"FILLED": 29}, "ran_at": 123,
                 "tca": {"huge": "block_not_carried"}})
    _write_json(tmp_path / "live_track_status_bybit.json",
                {"mode": "preview", "hard_stop": True})  # 无 strategy -> 不归属
    _write_json(tmp_path / "live_track_status_mexc_btc_spot_trend.json",
                {"strategy": "btc_spot_trend", "venue": "mexc", "mode": "preview",
                 "armed": False, "ran_at": 99})
    runs = alc.load_live_status_runs(str(tmp_path / "live_track_status*.json"))
    assert set(runs) == {"crowd_contra", "btc_spot_trend"}
    assert runs["crowd_contra"][0]["venue"] == "binance"  # 文件名兜底
    assert runs["crowd_contra"][0]["n_submitted"] == 14
    assert "tca" not in runs["crowd_contra"][0]
    assert runs["btc_spot_trend"][0]["venue"] == "mexc"
    assert runs["btc_spot_trend"][0]["armed"] is False
    assert runs["btc_spot_trend"][0]["hard_stop"] is False
    assert alc.live_stage("ghost", runs) == {"status": "ABSENT"}
    assert alc.live_stage("crowd_contra", runs)["status"] == "PRESENT"


def test_decay_and_retirement(tmp_path):
    db = _seed_maintenance_db(
        tmp_path / "maint.db",
        {"generated_at": 7, "actor_kind": "AGENT", "n_actions": 0, "strategies": {}}, [])
    snap = alc.load_maintenance_snapshot(db)
    events = alc.load_maintenance_events(db)
    out = alc.decay_stage("a1", snap, source=db)
    assert out["status"] == "PRESENT" and out["degraded"] is False and out["alpha_action"] is None
    assert alc.retirement_stage("a1", snap, events, source=db)["retired"] is False
    # 有降级动作 + RETIRED 事件
    db2 = _seed_maintenance_db(
        tmp_path / "maint2.db",
        {"generated_at": 9, "actor_kind": "AGENT", "n_actions": 1,
         "strategies": {"a1": {"action": "DEGRADED", "reason": "ic_decay"}}},
        [{"alpha_id": "a1", "to": "RETIRED"}, {"alpha_id": "b2", "to": "RETIRED"}])
    snap2 = alc.load_maintenance_snapshot(db2)
    events2 = alc.load_maintenance_events(db2)
    assert alc.decay_stage("a1", snap2, source=db2)["degraded"] is True
    ret = alc.retirement_stage("a1", snap2, events2, source=db2)
    assert ret["retired"] is True and ret["n_retirement_events"] == 1
    # 缺库 -> ABSENT
    assert alc.decay_stage("a1", None, source="x") == {"status": "ABSENT"}
    assert alc.retirement_stage("a1", None, [], source="x") == {"status": "ABSENT"}
    assert alc.load_maintenance_snapshot(str(tmp_path / "missing.db")) is None


# ---------------------------------------------------------------------------
# warnings
# ---------------------------------------------------------------------------

def test_build_warnings():
    stages = _stages(
        backtest=_stage("PRESENT", research_risk_decision="RISK_REJECTED"),
        paper=_stage("PRESENT", n_periods=1,
                     forward={"paper_verdict": "BLOCKED", "blockers": ["B1", "B2"]},
                     required_forward_periods=8),
        capacity=_stage("PRESENT", provenance="UNCALIBRATED_PRINCIPLED"),
        live=_stage("PRESENT", runs=[{"hard_stop": True}]),
    )
    warnings = alc.build_warnings("a1", stages, "LIVE_TRACK")
    assert any("RISK_REJECTED" in w for w in warnings)
    assert any("1/8" in w and "BLOCKED" in w for w in warnings)
    assert any("B1, B2" in w for w in warnings)
    assert any("阶段跳跃" in w and "证伪" in w for w in warnings)  # 无 falsification 却 LIVE
    assert any("未校准" in w for w in warnings)
    assert any("hard_stop" in w for w in warnings)
    # 合成时间戳
    stages2 = _stages(paper=_stage("PRESENT", n_periods=24,
                                   last_period={"as_of_ts": 6566400000},
                                   required_forward_periods=None))
    assert any("2000" in w for w in alc.build_warnings("h_x", stages2, "PAPER_TRACKED"))
    # 干净证据链 -> 无告警
    clean = _stages(falsification=_stage("PRESENT", verdict_pass=True),
                    backtest=_stage("PRESENT", research_risk_decision="RISK_APPROVED"))
    assert alc.build_warnings("ok", clean, "DRY_RUN") == []


def test_build_warnings_surfaces_maintenance_downgrade():
    # PAUSED(spec §12 QUARANTINED 的 canonical 映射)-> 停加仓告警;RETIRED 同理
    stages = _stages(decay=_stage("PRESENT", alpha_action={
        "state": "PAUSED", "reasons": [{"metric": "drawdown", "detail": "drawdown 0.31 >= 0.3"}]}))
    warnings = alc.build_warnings("top_contra", stages, "DRY_RUN")
    assert any("PAUSED" in w and "停止新增敞口" in w for w in warnings)
    stages_ret = _stages(decay=_stage("PRESENT", alpha_action={"state": "RETIRED", "reasons": []}))
    assert any("RETIRED" in w for w in alc.build_warnings("a1", stages_ret, "DRY_RUN"))
    # DEGRADED -> 观察态告警(刻意不停加仓);无动作 -> 无 maintenance 告警
    stages_deg = _stages(decay=_stage("PRESENT", alpha_action={"state": "DEGRADED", "reasons": []}))
    assert any("DEGRADED" in w and "观察态" in w for w in alc.build_warnings("a1", stages_deg, "DRY_RUN"))
    quiet = _stages(decay=_stage("PRESENT", alpha_action=None))
    assert not any("maintenance 权威" in w for w in alc.build_warnings("a1", quiet, "DRY_RUN"))


# ---------------------------------------------------------------------------
# build_report 装配(全 hermetic)
# ---------------------------------------------------------------------------

def _full_tmp_paths(tmp_path):
    _write_json(tmp_path / "ic_panel.json", {"rows": [{
        "name": "crowd_contra", "universe": "crypto_oi_majors", "mean_ic": 0.035,
        "icir": 0.22, "t_stat": 4.32, "hit_rate": 0.6, "n_periods": 157,
        "n_instruments": 24, "per_year": {"2024": 0.05}, "by_period": {}}]})
    _write_json(tmp_path / "oi_verify.json", {
        "refutation_checks": {"a": True, "b": True, "c": True, "d": True, "e": True},
        "verdict": "SURVIVES 5/5", "net_sharpe_by_fee_bps": {"10": 0.687}})
    _write_json(tmp_path / "scenario.json", {"provenance": "REAL_HISTORICAL_REPLAY",
                                             "books": {"crowd_contra": {"n_periods": 233,
                                                                        "hill_tail_index": 2.67}}})
    _write_json(tmp_path / "capacity.json", {"provenance": "UNCALIBRATED_PRINCIPLED",
                                             "books": {"crowd_contra": {
                                                 "participation_cap_aum": 7.16e6,
                                                 "binding": "participation",
                                                 "calibrated_range": {"low": 3.5e6, "high": 7.1e7},
                                                 "gross_sharpe": 1.566, "n_periods": 234}}})
    _write_json(tmp_path / "combo.json", {"standalone": {"crowd_contra": {"ann_sharpe": 1.297}},
                                          "combo": {"equal_weight": {"ann_sharpe": 1.949},
                                                    "inverse_vol": {"ann_sharpe": 1.977}},
                                          "inverse_vol_weights": {"crowd_contra": 0.44},
                                          "corr_carry_crowd": -0.038, "common_weeks": 231})
    _write_json(tmp_path / "crypto_combo.json", {"weights": {"crowd_contra": 0.3511},
                                                 "alone": {"crowd_contra": {"econ_sharpe": 1.277}},
                                                 "combo_econ_sharpe": 1.787,
                                                 "lift_vs_best_alone": 0.51,
                                                 "verdict": "diversification helps"})
    _write_json(tmp_path / "paper_cycle.json", {
        "cycle": {"required_forward_periods": 8},
        "alphas": {"crowd_contra": {
            "definition": {"display_name": "crowd_contra", "dry_run_id": "crowd_contra",
                           "capacity_id": "crowd_contra", "forward_id": "crowd_contra"},
            "research": {"verdict": "PASS_WITH_WARNINGS", "evidence": {}},
            "canonical_dry_run": {"run_id": "run_canonical"},
            "forward": {"n_forward_periods": 0, "evidence_state": "DATA_BLOCKED"},
            "paper_verdict": "BLOCKED", "blockers": ["FORWARD_EVIDENCE_INSUFFICIENT"]}}})
    _seed_dry_run_db(tmp_path / "dry_run.db", [
        _dry_row("run_other", "crowd_contra", 100, sharpe=0.5),
        _dry_row("run_canonical", "crowd_contra", 100, sharpe=1.07)])
    _seed_paper_db(tmp_path / "paper.db",
                   track_rows=[("crowd_contra", 100, 100, 1, 0.001, 0.0, 0.345, 3.66, 1.0)],
                   period_rows=[("crowd_contra", 1783555200000, 0.001, 0.345, "FORWARD_LIVE")])
    _seed_maintenance_db(tmp_path / "maint.db",
                         {"generated_at": 7, "actor_kind": "AGENT", "n_actions": 0,
                          "strategies": {}}, [])
    _write_json(tmp_path / "live_track_status.json",
                {"strategy": "crowd_contra", "venue": "binance", "mode": "submit",
                 "n_submitted": 14, "ran_at": 1783905000982})
    return alc.Paths(
        ic_panel=str(tmp_path / "ic_panel.json"),
        oi_verify=str(tmp_path / "oi_verify.json"),
        oi_top_verify=str(tmp_path / "missing_top.json"),
        scenario_tail=str(tmp_path / "scenario.json"),
        combo_book=str(tmp_path / "combo.json"),
        crypto_combo=str(tmp_path / "crypto_combo.json"),
        capacity=str(tmp_path / "capacity.json"),
        paper_cycle=str(tmp_path / "paper_cycle.json"),
        dry_run_db=str(tmp_path / "dry_run.db"),
        paper_db=str(tmp_path / "paper.db"),
        maintenance_db=str(tmp_path / "maint.db"),
        live_status_glob=str(tmp_path / "live_track_status*.json"),
    )


def test_build_report_full_hermetic(tmp_path):
    report = alc.build_report(paths=_full_tmp_paths(tmp_path), now_ms=1784900000000,
                              strategy_ids=["crowd_contra"])
    assert report["artifact"] == "ALPHA_LIFECYCLE_COCKPIT"
    assert report["generated_at_ms"] == 1784900000000
    assert set(report["stage_legend"]) >= {"discovery", "falsification", "stress", "backtest",
                                           "capacity", "portfolio", "paper", "live", "decay",
                                           "retirement", "derived_stage"}
    assert len(report["alphas"]) == 1
    alpha = report["alphas"][0]
    assert alpha["alpha_id"] == "crowd_contra"
    assert alpha["derived_stage"] == "LIVE_TRACK"
    assert alpha["lifecycle_state"] is None
    present = {s for s, v in alpha["stages"].items() if v["status"] == "PRESENT"}
    assert present == {"discovery", "falsification", "stress", "backtest", "capacity",
                       "portfolio", "paper", "live", "decay", "retirement"}
    assert alpha["stages"]["backtest"]["run_id"] == "run_canonical"
    assert alpha["stages"]["backtest"]["canonical"] is True
    assert alpha["stages"]["falsification"]["verdict_pass"] is True
    assert alpha["stages"]["paper"]["n_periods"] == 1
    assert alpha["stages"]["live"]["runs"][0]["venue"] == "binance"
    assert any("1/8" in w for w in alpha["warnings"])
    # 产物必须可被 allow_nan=False 序列化(atomic_write_json 契约)
    json.dumps(report, allow_nan=False)


def test_build_report_empty_dir_all_absent(tmp_path):
    paths = alc.Paths(
        ic_panel=str(tmp_path / "a.json"), oi_verify=str(tmp_path / "b.json"),
        oi_top_verify=str(tmp_path / "c.json"), scenario_tail=str(tmp_path / "d.json"),
        combo_book=str(tmp_path / "e.json"), crypto_combo=str(tmp_path / "f.json"),
        capacity=str(tmp_path / "g.json"), paper_cycle=str(tmp_path / "h.json"),
        dry_run_db=str(tmp_path / "i.db"), paper_db=str(tmp_path / "j.db"),
        maintenance_db=str(tmp_path / "k.db"),
        live_status_glob=str(tmp_path / "live_track_status*.json"))
    report = alc.build_report(paths=paths, now_ms=1, strategy_ids=["ghost_alpha"])
    alpha = report["alphas"][0]
    assert alpha["derived_stage"] == "UNTRACKED"
    assert all(v["status"] == "ABSENT" for v in alpha["stages"].values())
    json.dumps(report, allow_nan=False)
