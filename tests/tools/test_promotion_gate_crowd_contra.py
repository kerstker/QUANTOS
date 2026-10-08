"""Driver plumbing tests for tools/promotion_gate_crowd_contra.py — pre-registration integrity,
grid construction, definition uniqueness, the three-gate assembly wiring (verdict precedence),
and the variants-family overlap mapping. All synthetic; no production registry, no data files,
no network, no dry-run pipeline execution."""
from __future__ import annotations

import pytest

from services.research.alpha_expression import serialize
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.promotion_gate_crowd_contra import (_BASE_COST_BPS, _CENTER, _COST_TIERS_BPS,
                                               _FAMILY, _GRID_CADENCES, _GRID_QUANTILES,
                                               PRE_REGISTRATION, _definition_for,
                                               build_grid_specs, evaluate_gates,
                                               gate_reasons_for_display, grid_key,
                                               variants_overlap_map)

_WEEK = 7 * 24 * 3600 * 1000


# --- pre-registration integrity -------------------------------------------


def test_grid_has_15_unique_points_with_exactly_one_center():
    specs = build_grid_specs()
    assert len(specs) == 15
    assert len({s["id"] for s in specs}) == 15
    centers = [s for s in specs if s["role"] == "center"]
    assert len(centers) == 1
    assert (centers[0]["quantile"], centers[0]["cadence_days"]) == _CENTER == (0.30, 7)
    # 网格 = quantile × cadence 全笛卡尔积
    assert {(s["quantile"], s["cadence_days"]) for s in specs} == {
        (q, c) for q in _GRID_QUANTILES for c in _GRID_CADENCES}


def test_pre_registration_is_registered_before_evaluation_and_complete():
    pre = PRE_REGISTRATION
    assert pre["registered_before_evaluation"] is True
    assert pre["trial_count_planned"] == 15
    assert pre["cost_tiers_bps"] == [10.0, 15.0, 20.0] == list(_COST_TIERS_BPS)
    assert _BASE_COST_BPS == 10.0
    assert pre["center"]["quantile"] == 0.30 and pre["center"]["cadence_days"] == 7
    for gate in ("cost_stress", "perturbation", "concentration"):
        assert pre["thresholds"][gate]["provenance"] == "UNCALIBRATED_ENGINEERING_DEFAULT"


def test_grid_key_format_is_stable():
    assert grid_key(0.30, 7) == "q30_7d"
    assert grid_key(0.20, 3) == "q20_3d"
    assert grid_key(0.40, 14) == "q40_14d"
    # q=0.35 的浮点表示(0.35000000000000003 类)不得改变键
    assert grid_key(0.25 + 0.10, 7) == "q35_7d"


def test_definition_hashes_are_unique_across_grid():
    specs = build_grid_specs()
    hashes = {_definition_for(s).definition_hash for s in specs}
    assert len(hashes) == 15
    d = _definition_for(specs[0])
    assert d.params["family"] == _FAMILY
    assert set(d.factor_ids) == {"close", "global_ls"}
    # 定义表达式恒为 CROWD_CONTRA(扰动轴是 q/cadence,不动信号本身)
    assert d.params["expr"] == serialize(CROWD_CONTRA)


# --- evaluate_gates 装配(合成数字;verdict 优先级)----------------------------


def _flat_grid(center_metric=1.10, neighbor_metrics=None):
    """15 点合成网格:默认全部落在平台带内(部署点 q30_7d 非尖峰、非低点)。"""
    specs = build_grid_specs()
    metrics = {}
    for s in specs:
        key = grid_key(s["quantile"], s["cadence_days"])
        metrics[key] = center_metric if s["role"] == "center" else 1.05
    if neighbor_metrics:
        metrics.update(neighbor_metrics)
    return metrics


def _passing_inputs():
    return {
        "grid_metrics": _flat_grid(),
        "center_key": "q30_7d",
        "cost_sharpe_by_tier": {10.0: 1.10, 15.0: 1.00, 20.0: 0.90},
        "period_contribs": [100.0] * 20,
        "instrument_contribs": {f"C{i}": 500.0 for i in range(10)},
        "period_times": [1640995200000 + i * _WEEK for i in range(20)],
    }


def test_evaluate_gates_all_pass_overall_pass():
    out = evaluate_gates(**_passing_inputs())
    assert out["overall_verdict"] == "PASS"
    assert set(out["gates"]) == {"cost_stress", "perturbation", "concentration"}
    assert all(g["verdict"] == "PASS" for g in out["gates"].values())


def test_evaluate_gates_spike_grid_fails_overall():
    inputs = _passing_inputs()
    inputs["grid_metrics"] = _flat_grid(center_metric=2.50, neighbor_metrics={})
    # center 2.50 vs 邻域全 1.05:spike_gap 1.45 >> 带 → FAIL
    out = evaluate_gates(**inputs)
    assert out["gates"]["perturbation"]["verdict"] == "FAIL"
    assert out["overall_verdict"] == "FAIL"


def test_evaluate_gates_concentration_skew_fails_overall():
    inputs = _passing_inputs()
    inputs["instrument_contribs"] = {"BTC": 9000.0, "ETH": -500.0, "SOL": 200.0, "XRP": 100.0}
    out = evaluate_gates(**inputs)
    assert out["gates"]["concentration"]["verdict"] == "FAIL"
    assert out["overall_verdict"] == "FAIL"


def test_evaluate_gates_warn_beats_pass_and_none_beats_pass():
    inputs = _passing_inputs()
    inputs["cost_sharpe_by_tier"] = {10.0: 1.10, 15.0: 0.40, 20.0: -0.10}  # 死在最档 → WARN
    out = evaluate_gates(**inputs)
    assert out["gates"]["cost_stress"]["verdict"] == "WARN"
    assert out["overall_verdict"] == "WARN"
    # 某闸数据不足 → overall None(不完整,不假装 PASS);FAIL 仍优先于 None
    inputs = _passing_inputs()
    inputs["cost_sharpe_by_tier"] = {10.0: 1.10, 15.0: None, 20.0: 0.90}
    out = evaluate_gates(**inputs)
    assert out["gates"]["cost_stress"]["verdict"] is None
    assert out["overall_verdict"] is None


def test_evaluate_gates_fail_beats_warn():
    inputs = _passing_inputs()
    inputs["cost_sharpe_by_tier"] = {10.0: 1.10, 15.0: 0.40, 20.0: -0.10}     # WARN
    inputs["grid_metrics"] = _flat_grid(center_metric=3.00)                  # FAIL(spike)
    out = evaluate_gates(**inputs)
    assert out["overall_verdict"] == "FAIL"


def test_evaluate_gates_deterministic():
    assert evaluate_gates(**_passing_inputs()) == evaluate_gates(**_passing_inputs())


# --- 打印展示层(2026-07-25 首跑 KeyError 修复的回归钉)------------------------


def test_gate_reasons_for_display_handles_flat_and_nested_reports():
    out = evaluate_gates(**_passing_inputs())
    # 三闸都能扁平化出 reasons(concentration 的嵌在 dimensions 里)——首跑崩溃点的回归钉
    flat = {name: gate_reasons_for_display(g) for name, g in out["gates"].items()}
    assert set(flat) == {"cost_stress", "perturbation", "concentration"}
    assert all(isinstance(rs, list) and rs for rs in flat.values())
    assert any(r.startswith("instrument:") for r in flat["concentration"])
    assert gate_reasons_for_display({}) == []
    assert gate_reasons_for_display({"reasons": ["A"]}) == ["A"]


# --- variants 族重叠映射 ------------------------------------------------------


def test_variants_overlap_map_extracts_only_pure_grid_points():
    payload = {"pre_registration": {"variants": [
        {"id": "crowd_contra", "kind": "baseline", "quantile": 0.30, "cadence_days": 7},
        {"id": "crowd_contra_q20", "kind": "quantile", "quantile": 0.20, "cadence_days": 7},
        {"id": "crowd_contra_3d", "kind": "cadence", "quantile": 0.30, "cadence_days": 3},
        {"id": "crowd_contra_q20_14d", "kind": "grid", "quantile": 0.20, "cadence_days": 14},
        {"id": "crowd_taker_combo", "kind": "composite", "quantile": 0.30, "cadence_days": 7},
        {"id": "crowd_carry_blend", "kind": "composite_extra", "quantile": 0.30, "cadence_days": 7},
    ]}}
    out = variants_overlap_map(payload)
    assert out == {"crowd_contra": "q30_7d", "crowd_contra_q20": "q20_7d",
                   "crowd_contra_3d": "q30_3d", "crowd_contra_q20_14d": "q20_14d"}
    assert variants_overlap_map({}) == {}
    assert variants_overlap_map(None) == {}
