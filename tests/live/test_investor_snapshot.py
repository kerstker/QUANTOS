"""live/execution/investor_snapshot.py(W9 纯核)单元测试。

钉死:快照哈希确定性(同输入同哈希、dict 键序/版本列表序无关、任一输入变化即变)、
ledger root 行序无关与内容敏感、日频 NAV 现金流调整数学(手算可复核)、maxDD/恢复天数/
最差日周(手算可复核)、UNKNOWN 传播(不可导出字段恒 None + 点名)、ABSENT venue /
双组合视图 ABSENT、哈希不含 generated_at_ms(同一数据截止点重跑同哈希)。
"""
from __future__ import annotations

import pytest

from live.execution import investor_snapshot as inv

DAY = inv.DAY_MS
T0 = 1_780_000_000_000


def _sample(day: int, equity: float, *, offset_ms: int = 1000) -> dict:
    return {"ts": T0 + day * DAY + offset_ms, "equity": equity}


def _report(**over) -> dict:
    base = {
        "strategy_id": "crowd_contra", "venue": "v1",
        "window": {"n_days": 5, "mark_end_ts": T0 + 4 * DAY},
        "live_net_pnl": 200.0, "live_gross_pnl": 200.0,
        "replay_net_pnl": 209.0, "replay_gross_pnl": 210.0,
        "live_degradation_ratio": 200.0 / 209.0, "degradation_ratio_reason": None,
        "slippage_bps_weighted": -33.85, "n_orders": 2, "n_fills": 2,
        "components": {"fee_actual": None, "funding_actual": None,
                       "fee_expected": 1.0, "funding_expected": 0.5,
                       "slippage_cost_actual": 10.0},
        "daily": [{"t": T0 + i * DAY, "live_equity": 1000.0 + i,
                   "replay_equity": 1001.0 + i, "gap": -1.0} for i in range(5)],
    }
    base.update(over)
    return base


def _venue(**over) -> dict:
    base = {
        "venue": "v1", "mainnet": False, "spot": False,
        "orders": [{"client_order_id": "c1", "instrument_id": "BTC", "side": "BUY",
                    "quantity": 5.0, "submitted_at": T0, "venue_order_id": "vo1",
                    "status": "FILLED", "strategy_id": "crowd_contra",
                    "order_type": "MARKET", "limit_price": None,
                    "canonical_state": "FILLED"}],
        "fills": [{"fill_id": "f1", "client_order_id": "c1", "instrument_id": "BTC",
                   "side": "BUY", "quantity": 5.0, "price": 102.0, "filled_at": T0 + 100,
                   "strategy_id": "crowd_contra"}],
        "tca": [{"fill_id": "f1", "client_order_id": "c1", "instrument_id": "BTC",
                 "side": "BUY", "decision_price": 100.0, "fill_price": 102.0,
                 "quantity": 5.0, "slippage_bps": 196.0, "recorded_at": T0 + 200,
                 "strategy_id": "crowd_contra"}],
        "equity_samples": [_sample(i, v) for i, v in
                           enumerate([100.0, 110.0, 105.0, 120.0, 90.0])],
        "external_flows": None,
        "status": {"ran_at": T0 + 4 * DAY,
                   "reconciliation": {"drift": {"BTC": 0.0}, "in_sync": True,
                                      "max_abs_drift": 0.0, "proceed": True},
                   "batch_skipped": None},
        "kill_switch": {"provenance": "KILL_SWITCH_AUTHORITY_DB", "tripped": False,
                        "incident_id": None,
                        "transitions": [{"action": "BOOTSTRAP", "recorded_at": 0,
                                         "transition_id": "t-0", "incident_id": None,
                                         "reason": None, "tripped": False}]},
        "mandate": {"present": True, "expired": False, "max_gross_notional": 10000.0,
                    "max_single_order_notional": 2500.0, "max_net_notional": 3000.0,
                    "allowed_instruments": ["BTC", "ETH"]},
        "attestations": {"crowd_contra": {"present": True, "kind": "human_override"}},
        "capacity_safety": {"safe_capacity_est": 3.58e6, "passes": True},
        "degradation_reports": [_report()],
        "lifecycle_alphas": [{"alpha_id": "crowd_contra", "derived_stage": "LIVE_TRACK"}],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 哈希(§13.2)
# ---------------------------------------------------------------------------

def test_snapshot_hash_deterministic_same_input_same_hash():
    versions = [{"strategy_id": "a", "strategy_version": None, "code_hash": None}]
    h1 = inv.compute_snapshot_hash(123, "root", versions)
    h2 = inv.compute_snapshot_hash(123, "root", versions)
    assert h1 == h2 and len(h1) == 128          # sha512 hex


def test_snapshot_hash_field_order_independent_via_canonicalization():
    v1 = [{"strategy_id": "a", "strategy_version": None, "code_hash": None},
          {"strategy_id": "b", "strategy_version": "1.0", "code_hash": "deadbeef"}]
    # 同内容、不同键序 + 不同列表序 -> 同一哈希(canonicalization + strategy_id 排序)
    v2 = [{"code_hash": "deadbeef", "strategy_version": "1.0", "strategy_id": "b"},
          {"code_hash": None, "strategy_id": "a", "strategy_version": None}]
    assert inv.compute_snapshot_hash(7, "r", v1) == inv.compute_snapshot_hash(7, "r", v2)


def test_snapshot_hash_sensitive_to_each_input():
    versions = [{"strategy_id": "a", "strategy_version": None, "code_hash": None}]
    base = inv.compute_snapshot_hash(123, "root", versions)
    assert inv.compute_snapshot_hash(124, "root", versions) != base
    assert inv.compute_snapshot_hash(123, "root2", versions) != base
    changed = [{"strategy_id": "a", "strategy_version": "1.0", "code_hash": None}]
    assert inv.compute_snapshot_hash(123, "root", changed) != base


def test_snapshot_hash_none_cutoff_well_defined():
    versions = [{"strategy_id": "a", "strategy_version": None, "code_hash": None}]
    h = inv.compute_snapshot_hash(None, "root", versions)
    assert isinstance(h, str) and len(h) == 128
    assert h != inv.compute_snapshot_hash(0, "root", versions)


def test_venue_ledger_root_row_order_independent_and_content_sensitive():
    v = _venue()
    r1 = inv.compute_venue_ledger_root(v["orders"], v["fills"], v["tca"])
    # 行序不同(同主键集)-> 同一 root(按主键排序)
    r2 = inv.compute_venue_ledger_root(list(reversed(v["orders"])),
                                       list(reversed(v["fills"])),
                                       list(reversed(v["tca"])))
    assert r1 == r2
    # 任一字段内容变化 -> root 变化(篡改可见)
    tampered = [dict(v["fills"][0], price=103.0)]
    assert inv.compute_venue_ledger_root(v["orders"], tampered, v["tca"]) != r1
    # 快照级 root 合并:ABSENT venue 显式 null 入哈希
    combined = inv.combine_ledger_roots({"v1": r1, "v2": None})
    assert combined != inv.combine_ledger_roots({"v1": r1})
    assert combined == inv.combine_ledger_roots({"v2": None, "v1": r1})   # 键序无关


def test_ledger_root_rejects_non_finite():
    bad = [dict(_venue()["fills"][0], price=float("nan"))]
    with pytest.raises(ValueError):
        inv.compute_venue_ledger_root([], bad, [])


# ---------------------------------------------------------------------------
# 日频 NAV + 现金流调整(手算可复核)
# ---------------------------------------------------------------------------

def test_daily_nav_series_last_sample_per_day_and_flow_adjustment():
    samples = [_sample(0, 100.0), _sample(0, 101.0, offset_ms=2000),   # 同日两条,取最后
               _sample(1, 110.0), _sample(2, 105.0)]
    flows = [{"ts": T0 + 500, "amount": 50.0},                          # day0 最后采样前 -> 计入 day0 起
             {"ts": T0 + 2 * DAY + 500, "amount": -20.0}]               # day2 采样(1000)前 -> 计入 day2
    days, unknown = inv.daily_nav_series(samples, flows)
    assert unknown == []
    assert [d["n_samples"] for d in days] == [2, 1, 1]
    assert days[0]["nav_raw"] == 101.0                                  # 同日最后一条
    assert days[0]["cum_flow"] == 50.0
    assert days[0]["nav_adjusted"] == pytest.approx(51.0)               # 101 − 50
    assert days[1]["cum_flow"] == 50.0                                  # day1 采样 ts < flow2.ts -> 不增
    assert days[1]["nav_adjusted"] == pytest.approx(60.0)               # 110 − 50
    assert days[2]["cum_flow"] == 30.0                                  # 50 − 20
    assert days[2]["nav_adjusted"] == pytest.approx(75.0)               # 105 − 30


def test_daily_nav_series_unrecorded_flows_are_unknown_not_zero():
    days, unknown = inv.daily_nav_series([_sample(0, 100.0)], None)
    assert unknown == ["external_flows"]                # 无记录 ≠ 零出入金
    assert days[0]["cum_flow"] is None and days[0]["nav_adjusted"] is None
    assert days[0]["nav_raw"] == 100.0


# ---------------------------------------------------------------------------
# 风险块:maxDD / 恢复天数 / 最差日周(手算可复核)
# ---------------------------------------------------------------------------

def _risk_days(values):
    samples = [_sample(i, v) for i, v in enumerate(values)]
    days, _ = inv.daily_nav_series(samples, [])
    return days


def test_nav_risk_metrics_hand_computed_not_recovered():
    # [100,110,105,120,90,95,118,119,118,100]
    # maxDD:峰 120(day3)→ 谷 90(day4)= 30(day7=119 不创新高,窗口内回不到 120 = 未恢复)
    days = _risk_days([100, 110, 105, 120, 90, 95, 118, 119, 118, 100])
    risk, unknown = inv.nav_risk_metrics(days, value_key="nav_raw")
    assert unknown == []
    assert risk["max_drawdown"] == pytest.approx(30.0)
    assert risk["max_drawdown_pct"] == pytest.approx(30.0 / 120.0)
    assert risk["max_drawdown_peak_date"] == days[3]["date"]
    assert risk["max_drawdown_trough_date"] == days[4]["date"]
    assert risk["recovered"] is False and risk["recovery_days"] is None
    assert risk["current_drawdown"] == pytest.approx(20.0)              # max=120 − last=100
    # 最差日:day3→day4 = 90−120 = −30
    assert risk["worst_day"]["pnl"] == pytest.approx(-30.0)
    assert risk["worst_day"]["pct"] == pytest.approx(-30.0 / 120.0)
    # ISO 周:day0(1970-01-01 Thu 起)day0–3 = week1(末值 120),day4–9 = week2(末值 100)
    assert risk["worst_week"]["pnl"] == pytest.approx(-20.0)            # 100 − 120
    assert risk["worst_week"]["pct"] == pytest.approx(-20.0 / 120.0)


def test_nav_risk_metrics_recovery_days():
    # [100, 80, 100]:峰 100 → 谷 80(day1)dd=20;day2 回到 100 -> recovery = 1 天
    risk, _ = inv.nav_risk_metrics(_risk_days([100, 80, 100]), value_key="nav_raw")
    assert risk["max_drawdown"] == pytest.approx(20.0)
    assert risk["recovered"] is True and risk["recovery_days"] == 1


def test_nav_risk_metrics_zero_drawdown():
    risk, _ = inv.nav_risk_metrics(_risk_days([100, 101, 102]), value_key="nav_raw")
    assert risk["max_drawdown"] == 0.0
    assert risk["recovered"] is True and risk["recovery_days"] == 0
    assert risk["worst_day"]["pnl"] == pytest.approx(1.0)               # 最小的正 delta


def test_nav_risk_metrics_all_none_series_unknown():
    days, _ = inv.daily_nav_series([_sample(0, 100.0)], None)           # nav_adjusted 全 None
    risk, unknown = inv.nav_risk_metrics(days, value_key="nav_adjusted")
    assert risk["max_drawdown"] is None and risk["worst_day"] is None
    assert unknown == ["nav_series.nav_adjusted"]


# ---------------------------------------------------------------------------
# 装配:UNKNOWN 传播 / ABSENT / 视图 / 哈希与墙钟无关
# ---------------------------------------------------------------------------

def test_build_snapshot_end_to_end_unknown_propagation():
    snap = inv.build_snapshot(now_ms=999, venues=[_venue()],
                              source_generated_at=[T0 + 10 * DAY])
    assert snap["artifact"] == "INVESTOR_SNAPSHOT"
    assert snap["generated_at_ms"] == 999
    assert snap["data_cutoff_ms"] == T0 + 10 * DAY                       # 含源 artifact 时刻
    b = snap["venues"][0]
    assert b["status"] == "ACTIVE"
    # NAV:external_flows=None -> basis RAW_FLOWS_UNRECORDED,nav_adjusted None
    assert b["nav"]["basis"] == "RAW_FLOWS_UNRECORDED"
    assert b["nav"]["last_nav"] == 90.0
    assert b["nav"]["days"][-1]["nav_adjusted"] is None
    # 分解:fee/funding actual 恒 UNKNOWN;gross/net/slippage 可导出
    d0 = b["decomposition"][0]
    assert d0["gross"] == 200.0 and d0["net"] == 200.0
    assert d0["fee_actual"] is None and d0["funding_actual"] is None
    assert d0["slippage_cost_actual"] == 10.0
    # 三线:live/replay 可导出;paper ABSENT + reason(绝不拿 replay 冒充 paper)
    tl = b["three_line"][0]
    assert tl["live"]["net"] == 200.0 and tl["replay"]["net"] == 209.0
    assert tl["paper"] is None and tl["paper_reason"]
    assert len(tl["daily"]) == 5
    # 版本:strategy_version/code_hash 恒 UNKNOWN,derived_stage 可导出
    v0 = b["versions"][0]
    assert v0["strategy_version"] is None and v0["code_hash"] is None
    assert v0["derived_stage"] == "LIVE_TRACK"
    assert v0["attestation_kind"] == "human_override"
    # 对账/事故/容量
    assert b["reconciliation"]["in_sync"] is True
    assert b["incidents"]["tripped"] is False
    assert b["capacity"]["max_gross_notional"] == 10000.0
    assert b["capacity"]["capacity_safety"]["passes"] is True
    # unknown_fields 点名(全局去重排序)
    unk = set(snap["unknown_fields"])
    assert "venue.v1.decomposition.crowd_contra.fee_actual" in unk
    assert "venue.v1.decomposition.crowd_contra.funding_actual" in unk
    assert "venue.v1.cluster_contribution" in unk
    assert "venue.v1.three_line.crowd_contra.paper" in unk
    assert "venue.v1.versions.crowd_contra.strategy_version" in unk
    assert "venue.v1.versions.crowd_contra.code_hash" in unk
    assert "venue.v1.external_flows" in unk
    # 双组合视图 ABSENT(§13.3 未实例化,绝不伪造)
    assert snap["portfolio_views"]["showcase"]["status"] == "ABSENT"
    assert snap["portfolio_views"]["growth"]["status"] == "ABSENT"
    assert snap["amendments"] == []
    # summary
    assert snap["summary"]["total_last_nav"] == 90.0
    assert snap["summary"]["reconciliation_all_in_sync"] is True
    assert snap["summary"]["kill_switch_tripped_any"] is False


def test_build_snapshot_hash_independent_of_now_ms():
    s1 = inv.build_snapshot(now_ms=1, venues=[_venue()], source_generated_at=[T0])
    s2 = inv.build_snapshot(now_ms=2, venues=[_venue()], source_generated_at=[T0])
    assert s1["report_snapshot_hash"] == s2["report_snapshot_hash"]
    assert s1["generated_at_ms"] != s2["generated_at_ms"]


def test_build_snapshot_hash_changes_with_ledger_content():
    s1 = inv.build_snapshot(now_ms=1, venues=[_venue()])
    tampered = _venue(fills=[dict(_venue()["fills"][0], quantity=6.0)])
    s2 = inv.build_snapshot(now_ms=1, venues=[tampered])
    assert s1["report_snapshot_hash"] != s2["report_snapshot_hash"]


def test_absent_venue_honest_no_activity():
    snap = inv.build_snapshot(now_ms=1, venues=[_venue(
        venue="v_mainnet", mainnet=True, orders=[], fills=[], tca=[],
        equity_samples=[], status=None, kill_switch=None,
        degradation_reports=[])])
    b = snap["venues"][0]
    assert b["status"] == "ABSENT_NO_ACTIVITY"
    assert b["nav"] is None and b["risk"] is None
    assert b["ledger_root"] is None
    assert "venue.v_mainnet.nav" in snap["unknown_fields"]
    assert snap["summary"]["n_active_venues"] == 0
    assert snap["summary"]["total_last_nav"] is None


def test_kill_switch_absent_and_reconciliation_absent_are_unknown():
    snap = inv.build_snapshot(now_ms=1, venues=[_venue(kill_switch=None, status=None)])
    b = snap["venues"][0]
    assert b["incidents"]["status"] == "ABSENT"
    assert b["reconciliation"]["status"] == "ABSENT"
    unk = set(snap["unknown_fields"])
    assert "venue.v1.incidents" in unk and "venue.v1.reconciliation" in unk
    assert snap["summary"]["reconciliation_all_in_sync"] is None
    # 三值逻辑:kill-switch 无记录(tripped 未知)绝不报"全清"
    assert snap["summary"]["kill_switch_tripped_any"] is None
