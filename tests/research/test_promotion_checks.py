"""Pure tests for services/research/promotion_checks.py — §6.2 promotion-gate components.

Hand-computed cases for the three reusable reports (cost stress / perturbation /
concentration), the per-instrument attribution primitive (incl. the identity against the
real run_multi_period_dry_run), the _plan_quantities equivalence mirror, and determinism.
"""
from __future__ import annotations

import pytest

from services.execution.dry_run_pipeline import (
    _run_multi_period_dry_run_legacy_diagnostic as run_multi_period_dry_run,
    plan_target_quantities,
)
from services.research.promotion_checks import (CONCENTRATION_THRESHOLDS,
                                                COST_STRESS_THRESHOLDS,
                                                PERTURBATION_THRESHOLDS,
                                                _plan_quantities,
                                                attribute_period_contributions,
                                                concentration_report, cost_stress_report,
                                                perturbation_report)
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine

_WEEK = 7 * 24 * 3600 * 1000
_DAY = 24 * 3600 * 1000


# ---------------------------------------------------------------------------
# 1) cost_stress_report
# ---------------------------------------------------------------------------


def test_cost_stress_pass_monotonic_degradation_with_elasticity():
    out = cost_stress_report({10.0: 1.20, 15.0: 1.10, 20.0: 1.00})
    assert out["verdict"] == "PASS"
    assert out["base_cost_bps"] == 10.0
    # elasticity = (1.20 - 1.00) / (20 - 10) = 0.02 sharpe/bps
    assert out["elasticity_sharpe_per_bps"] == pytest.approx(0.02)
    assert out["degradation_at_max_tier"] == pytest.approx(0.20)
    assert out["degradation_pct_at_max_tier"] == pytest.approx(0.1667, abs=1e-9)  # 4dp 取整
    # breakeven = 10 + 1.20 / 0.02 = 70 bps
    assert out["breakeven_cost_bps"] == pytest.approx(70.0)
    assert out["monotonic"] is True


def test_cost_stress_flat_surface_passes_and_breakeven_is_none():
    out = cost_stress_report({10.0: 1.0, 15.0: 1.0, 20.0: 1.0})
    assert out["verdict"] == "PASS"
    assert out["elasticity_sharpe_per_bps"] == pytest.approx(0.0)
    assert out["breakeven_cost_bps"] is None  # 外推下 edge 不消亡 -> None,不编造


def test_cost_stress_edge_dying_only_at_max_tier_warns():
    out = cost_stress_report({10.0: 1.20, 15.0: 0.60, 20.0: -0.10})
    assert out["verdict"] == "WARN"
    assert any("EDGE_DIES_AT_HIGHER_TIER" in r for r in out["reasons"])


def test_cost_stress_edge_dying_at_first_stress_fails():
    out = cost_stress_report({10.0: 1.20, 15.0: -0.20, 20.0: -0.50})
    assert out["verdict"] == "FAIL"
    assert any("EDGE_DIES_AT_FIRST_STRESS" in r for r in out["reasons"])


def test_cost_stress_nonpositive_base_fails():
    out = cost_stress_report({10.0: -0.10, 15.0: -0.20, 20.0: -0.30})
    assert out["verdict"] == "FAIL"
    assert any("BASE_NONPOSITIVE" in r for r in out["reasons"])


def test_cost_stress_non_monotonic_beyond_tolerance_warns():
    out = cost_stress_report({10.0: 1.00, 15.0: 1.20, 20.0: 0.90})  # +0.20 > 0.05 tol
    assert out["verdict"] == "WARN"
    assert out["monotonic"] is False
    assert any("NON_MONOTONIC" in r for r in out["reasons"])
    # within tolerance -> still PASS
    out2 = cost_stress_report({10.0: 1.00, 15.0: 1.04, 20.0: 0.95})
    assert out2["verdict"] == "PASS"


def test_cost_stress_missing_or_insufficient_data_is_none_not_fake_pass():
    assert cost_stress_report({})["verdict"] is None
    assert cost_stress_report({10.0: 1.0})["verdict"] is None           # 单档无法加压
    out = cost_stress_report({10.0: 1.0, 15.0: None, 20.0: 0.9})
    assert out["verdict"] is None
    assert any("INSUFFICIENT_DATA" in r for r in out["reasons"])
    out = cost_stress_report({10.0: 1.0, 15.0: float("nan"), 20.0: 0.9})
    assert out["verdict"] is None


# ---------------------------------------------------------------------------
# 2) perturbation_report
# ---------------------------------------------------------------------------


def _grid(center_metric, neighbors, center="q30_7d"):
    g = {center: center_metric}
    for i, v in enumerate(neighbors):
        g[f"pt{i}"] = v
    return g, center


def test_perturbation_flat_surface_passes():
    grid, center = _grid(1.00, [0.98, 1.02, 1.00, 0.97, 1.01])
    out = perturbation_report(grid, center)
    assert out["verdict"] == "PASS"
    assert any("ON_PLATEAU" in r for r in out["reasons"])
    assert out["center_rank_pct"] == pytest.approx(0.6)  # 3/5 neighbors <= 1.00
    assert out["n_neighborhood"] == 5


def test_perturbation_spike_center_fails():
    grid, center = _grid(2.0, [1.0] * 14)
    out = perturbation_report(grid, center)
    assert out["verdict"] == "FAIL"
    assert any("SPIKE_OPTIMUM" in r for r in out["reasons"])
    assert out["spike_gap"] == pytest.approx(1.0)


def test_perturbation_center_towering_over_all_negative_neighbors_is_spike():
    # 符号无关:邻域全负、center 独正 = 孤立尖峰(过拟合签名),不是「相对好」。
    grid, center = _grid(1.0, [-0.20, -0.30, -0.25, -0.15])
    out = perturbation_report(grid, center)
    assert out["verdict"] == "FAIL"
    assert any("SPIKE_OPTIMUM" in r for r in out["reasons"])


def test_perturbation_below_neighborhood_median_fails():
    grid, center = _grid(0.50, [1.0, 1.1, 0.9, 1.2])
    out = perturbation_report(grid, center)
    assert out["verdict"] == "FAIL"
    assert any("BELOW_NEIGHBORHOOD_MEDIAN" in r for r in out["reasons"])
    assert out["center_rank_pct"] == 0.0


def test_perturbation_off_plateau_warns():
    # center=0.88: rank = 2/4 = 0.5(不低于中位数);gap to max = 0.12 > band max(0.05, 0.10)
    grid, center = _grid(0.88, [1.00, 0.90, 0.85, 0.87])
    out = perturbation_report(grid, center)
    assert out["verdict"] == "WARN"
    assert any("OFF_PLATEAU" in r for r in out["reasons"])
    assert out["plateau_gap"] == pytest.approx(0.12)


def test_perturbation_missing_center_is_none_not_fake_pass():
    grid, _ = _grid(1.0, [1.0, 1.0, 1.0, 1.0])
    assert perturbation_report(grid, "nope")["verdict"] is None
    out = perturbation_report({**grid, "q30_7d": None}, "q30_7d")
    assert out["verdict"] is None
    assert any("CENTER_MISSING" in r for r in out["reasons"])


def test_perturbation_insufficient_neighborhood_is_none():
    grid, center = _grid(1.0, [0.9, 1.1, None])  # 有效邻域只有 2 < 4
    out = perturbation_report(grid, center)
    assert out["verdict"] is None
    assert out["n_neighborhood"] == 2
    assert out["n_missing"] == 1
    assert any("INSUFFICIENT_DATA" in r for r in out["reasons"])
    assert perturbation_report({}, "c")["verdict"] is None


def test_perturbation_surface_is_returned_rounded():
    grid, center = _grid(1.0, [0.9, 1.1, 1.0, 0.95])
    out = perturbation_report(grid, center)
    assert set(out["surface"]) == set(grid)
    assert out["surface"]["q30_7d"] == 1.0


# ---------------------------------------------------------------------------
# 3) concentration_report
# ---------------------------------------------------------------------------


def _periods_flat(n=20, v=100.0):
    return [v] * n


def test_concentration_balanced_book_passes():
    inst = {f"C{i}": 100.0 for i in range(10)}
    out = concentration_report(_periods_flat(), inst)
    assert out["verdict"] == "PASS"
    dims = out["dimensions"]
    assert dims["instrument"]["verdict"] == "PASS"
    assert dims["period"]["verdict"] == "PASS"
    assert dims["month"]["verdict"] is None  # period_times 未给 -> None,不假装
    assert dims["instrument"]["top1"]["share"] == pytest.approx(0.1)


def test_concentration_single_instrument_carries_edge_fails():
    inst = {"BTC": 1000.0, "ETH": -100.0, "SOL": 50.0, "XRP": 10.0}  # residual ex BTC = -40
    out = concentration_report(_periods_flat(), inst)
    dim = out["dimensions"]["instrument"]
    assert dim["verdict"] == "FAIL"
    assert any("SINGLE_CONTRIBUTOR_CARRIES_EDGE" in r for r in dim["reasons"])
    assert dim["residual_ex_top1"] == pytest.approx(-40.0)
    assert out["verdict"] == "FAIL"


def test_concentration_top1_share_above_fail_threshold_fails():
    inst = {"BTC": 800.0, "ETH": 100.0, "SOL": 100.0, "XRP": 50.0}  # share 0.762 > 0.67
    out = concentration_report(_periods_flat(), inst)
    dim = out["dimensions"]["instrument"]
    assert dim["verdict"] == "FAIL"
    assert any("TOP1_SHARE" in r for r in dim["reasons"])


def test_concentration_top3_carries_edge_warns():
    # total 950, top1 600 (share 0.63 → WARN 带), top3 = 1000 → residual ex top3 = -50
    inst = {"BTC": 600.0, "ETH": 250.0, "SOL": 150.0, "XRP": -50.0}
    out = concentration_report(_periods_flat(), inst)
    dim = out["dimensions"]["instrument"]
    assert dim["verdict"] == "WARN"
    assert any("TOP3_CARRIES_EDGE" in r for r in dim["reasons"])
    assert out["verdict"] == "WARN"  # 其余维度 PASS,整体取最差


def test_concentration_top1_share_in_warn_band_warns():
    inst = {"BTC": 550.0, "ETH": 200.0, "SOL": 150.0, "XRP": 100.0}  # share 0.55, top3=900
    out = concentration_report(_periods_flat(), inst)
    dim = out["dimensions"]["instrument"]
    assert dim["verdict"] == "WARN"
    assert any("TOP1_SHARE" in r for r in dim["reasons"])
    assert dim["residual_ex_top3"] == pytest.approx(100.0)


def test_concentration_nonpositive_total_fails():
    inst = {"BTC": -100.0, "ETH": -50.0, "SOL": 20.0}
    out = concentration_report(_periods_flat(10, -5.0), inst)
    assert out["dimensions"]["instrument"]["verdict"] == "FAIL"
    assert any("TOTAL_NONPOSITIVE" in r for r in out["dimensions"]["instrument"]["reasons"])
    assert out["verdict"] == "FAIL"


def test_concentration_empty_or_sparse_is_none_not_fake_pass():
    out = concentration_report([], {})
    assert out["verdict"] is None
    assert out["dimensions"]["instrument"]["verdict"] is None
    assert out["dimensions"]["period"]["verdict"] is None
    # 2 个贡献者 < min_contributors=3
    out = concentration_report(_periods_flat(2), {"BTC": 100.0, "ETH": 50.0})
    assert out["dimensions"]["instrument"]["verdict"] is None
    # None 值剔除后计数
    out = concentration_report([100.0, None, 50.0, 25.0],
                               {"BTC": 100.0, "ETH": None, "SOL": 50.0, "XRP": 25.0})
    assert out["dimensions"]["instrument"]["n_contributors"] == 3
    assert out["dimensions"]["instrument"]["n_missing"] == 1


def test_concentration_month_dimension_from_period_times():
    # 10 周,跨 3 个月;一个月份贡献占绝对主导 -> 月份维度 FAIL,期间维度 PASS
    t0 = 1640995200000  # 2022-01-01
    times = [t0 + i * _WEEK for i in range(10)]
    contribs = [10.0] * 9 + [1000.0]
    # 最后一周落在与前几周不同的月份:2022-01-01 + 9 周 = 2022-03-05
    inst = {"BTC": 300.0, "ETH": 300.0, "SOL": 255.0, "XRP": 155.0}
    out = concentration_report(contribs, inst, period_times=times)
    mdim = out["dimensions"]["month"]
    assert mdim["n_contributors"] == 3
    months = sorted({k.strip("'") for k in [mdim["top1"]["key"]]})
    assert months == ["2022-03"]
    assert mdim["verdict"] == "FAIL"  # residual ex top1 = 90 - ... top1 1000, total 1090 -> 90 > 0
    # top1 share = 1000/1090 ≈ 0.917 > 0.67 -> FAIL TOP1_SHARE
    assert any("TOP1_SHARE" in r for r in mdim["reasons"])
    assert out["dimensions"]["period"]["verdict"] == "FAIL"  # 单期 1000/1090 同样 >0.67
    assert out["dimensions"]["instrument"]["verdict"] == "PASS"
    assert out["verdict"] == "FAIL"


def test_concentration_misaligned_period_times_month_dimension_none():
    out = concentration_report(_periods_flat(10), {"BTC": 100.0, "ETH": 80.0, "SOL": 60.0},
                               period_times=[1, 2, 3])
    assert out["dimensions"]["month"]["verdict"] is None
    assert any("INSUFFICIENT_DATA" in r for r in out["dimensions"]["month"]["reasons"])


def test_concentration_thresholds_overridable():
    inst = {"BTC": 550.0, "ETH": 200.0, "SOL": 150.0, "XRP": 100.0}
    strict = dict(CONCENTRATION_THRESHOLDS, warn_top1_share=0.40, fail_top1_share=0.50)
    out = concentration_report(_periods_flat(), inst, thresholds=strict)
    assert out["dimensions"]["instrument"]["verdict"] == "FAIL"  # 0.55 > fail 0.50


# ---------------------------------------------------------------------------
# 4) _plan_quantities 镜像等价 + 归因手算
# ---------------------------------------------------------------------------


def test_plan_quantities_matches_pipeline_primitive_exactly():
    weights = {"BTC": 0.25, "ETH": -0.25, "SOL": 0.5 / 3, "XRP": -0.001, "DEAD": 0.5}
    prices = {"BTC": 50000.0, "ETH": 3000.0, "SOL": 150.0, "XRP": 0.5, "DEAD": None}
    for aum in (1_000_000.0, 1e7, 123_456.0):
        assert _plan_quantities(weights, prices, aum) == plan_target_quantities(weights, prices, aum)


def test_attribution_hand_computed_two_instruments():
    # 3 期、2 标的、恒定权重 +0.5/-0.5,aum=1e6,10bps:
    #   BTC: 100 -> 110 -> 121; ETH: 100 -> 90 -> 99。资金费率 t1: BTC 0.001, ETH -0.002。
    periods = [1000, 1000 + _WEEK, 1000 + 2 * _WEEK]
    weights = {t: {"BTC": 0.5, "ETH": -0.5} for t in periods}
    prices = {periods[0]: {"BTC": 100.0, "ETH": 100.0},
              periods[1]: {"BTC": 110.0, "ETH": 90.0},
              periods[2]: {"BTC": 121.0, "ETH": 99.0}}
    rates = {periods[1]: {"BTC": 0.001, "ETH": -0.002}}
    out = attribute_period_contributions(periods=periods, weights_by_period=weights,
                                         prices_by_period=prices, aum=1e6, cost_bps=10.0,
                                         funding_rate_by_period=rates)
    pi = out["per_instrument"]
    # t0: qty BTC = round(0.5e6/100)=5000, ETH=-5000;成本各 500000*10/1e4=500。
    # t0->t1: BTC 5000*(110-100)=+50000;ETH -5000*(90-100)=+50000
    # t1 调仓:BTC 目标 round(0.5e6/110)=4545,dq=-455,成本 455*110*1e-3=50.05
    #          ETH 目标 round(-0.5e6/90)=-5556,dq=-556,成本 556*90*1e-3=50.04
    # t2 调仓:BTC 目标 round(0.5e6/121)=4132,dq=-413,成本 413*121*1e-3=49.973
    #          ETH 目标 round(-0.5e6/99)=-5051,dq=+505,成本 505*99*1e-3=49.995
    assert pi["BTC"]["price_pnl"] == pytest.approx(5000 * 10 + 4545 * 11)   # 50000 + 49995
    assert pi["ETH"]["price_pnl"] == pytest.approx(50000 + (-5556) * 9)     # 50000 - 50004
    assert pi["BTC"]["cost"] == pytest.approx(500.0 + 50.05 + 49.973)
    assert pi["ETH"]["cost"] == pytest.approx(500.0 + 50.04 + 49.995)
    # t1 settlement 先于 t1 调仓，故作用于从 t0 携入的 5000/-5000 手。
    assert pi["BTC"]["funding"] == pytest.approx(-550.0)
    assert pi["ETH"]["funding"] == pytest.approx(-900.0)
    # per_period 总和 == Σ 标的 total == 恒等式两侧
    assert sum(out["per_period"]) == pytest.approx(sum(s["total"] for s in pi.values()))
    assert len(out["per_period"]) == 3


def test_attribution_missing_close_price_keeps_position():
    # 标的跌出价格(退市/缺数):无价平仓单被适配器接缝跳过、持仓延续;t1 标记回退最后成交价
    # (镜像 reconcile 的 last_price 回退)→ t1→t2 的 100→120 全程归属;t2 价格回归后平仓成交。
    periods = [1000, 1000 + _WEEK, 1000 + 2 * _WEEK]
    weights = {periods[0]: {"BTC": 0.5}, periods[1]: {}, periods[2]: {}}
    prices = {periods[0]: {"BTC": 100.0}, periods[1]: {}, periods[2]: {"BTC": 120.0}}
    out = attribute_period_contributions(periods=periods, weights_by_period=weights,
                                         prices_by_period=prices, aum=1e6, cost_bps=10.0)
    pi = out["per_instrument"]["BTC"]
    # 成本:t0 开仓 5000*100*1e-3=500;t2 平仓 5000*120*1e-3=600(t1 无价平仓被跳过)
    assert pi["cost"] == pytest.approx(500.0 + 600.0)
    # t0->t1:p1 缺失回退最后成交价 100 -> Δp=0;t1->t2:p0 回退 100,p1=120 -> 5000*20
    assert pi["price_pnl"] == pytest.approx(5000 * 20)


# ---------------------------------------------------------------------------
# 5) 归因 vs 真管线恒等式(同输入 run_multi_period_dry_run)
# ---------------------------------------------------------------------------


def _wide_gate(names):
    limits = RiskLimits(
        allowed_instruments=frozenset(names), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10 ** 13, max_order_notional=1e14, fat_finger_qty=10 ** 13,
        max_gross_exposure=1e14, max_net_exposure=1e14, max_leverage=1e7, max_single_name_pct=1e7,
        max_turnover=1e7, max_participation_rate=1e7, max_instrument_vol=5.0, daily_loss_limit=1e14,
        max_drawdown=1.0)
    return PreTradeRiskGate(limits, kill_switch=KillSwitch())


def _ctx_factory(intent, prices, period):
    return RiskContext(
        account=AccountRiskState(account_id="paper", cash=1e8, equity=1e8, day_pnl=0.0,
                                 peak_equity=1e8, positions=()),
        market=MarketContext(instrument_id=intent.instrument_id, known=True,
                             reference_price=prices.get(intent.instrument_id, 1.0),
                             market_open=True, price_as_of=period, est_daily_volume=1e12,
                             instrument_vol=0.8),
        strategy=StrategyApproval(strategy_id="t", approved=True, approval_id="ok"),
        now=period, max_staleness=10 ** 15)


def test_attribution_identity_against_real_pipeline():
    periods = [1000 + i * _WEEK for i in range(5)]
    weights = {periods[0]: {"BTC": 0.5, "ETH": -0.5},
               periods[1]: {"BTC": 0.25, "ETH": -0.25, "SOL": 0.25, "XRP": -0.25},
               periods[2]: {"BTC": 0.5, "SOL": -0.5},
               periods[3]: {"ETH": 0.5, "XRP": -0.5},
               periods[4]: {"BTC": 0.5, "ETH": -0.5}}
    base = {"BTC": 100.0, "ETH": 50.0, "SOL": 20.0, "XRP": 1.0}
    drift = {"BTC": 1.03, "ETH": 0.97, "SOL": 1.05, "XRP": 0.90}
    prices = {}
    for k, t in enumerate(periods):
        prices[t] = {n: round(base[n] * drift[n] ** k, 6) for n in base}
    rates = {t: {"BTC": 0.0005, "ETH": -0.0002, "SOL": 0.0001, "XRP": 0.0} for t in periods}

    def cash_flow_fn(held, px, period):
        r = rates.get(period, {})
        return sum(-r.get(i, 0.0) * q * px.get(i, 0.0) for i, q in held.items())

    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights, prices_by_period=prices,
        gate=_wide_gate(base), ctx_factory=_ctx_factory, adapter=DryRunAdapterEngine(),
        strategy_id="t", aum=1e6, cost_bps=10.0, cash_flow_fn=cash_flow_fn,
        event_bus=None, publish_completion_event=False)
    assert run.n_rejected == 0  # 恒等式前提:门全批准(宽门,任何拒绝都应暴露)

    attrib = attribute_period_contributions(
        periods=periods, weights_by_period=weights, prices_by_period=prices, aum=1e6,
        cost_bps=10.0, funding_rate_by_period=rates)
    equity_gain = run.detail["equity_curve"][-1]["equity"] - 1e6
    # 恒等式 1:Σ per_period == 管线权益增量(6dp 曲线舍入内)
    assert sum(attrib["per_period"]) == pytest.approx(equity_gain, abs=1e-3)
    # 恒等式 2:Σ 标的 (price+funding-cost) == 同值
    assert sum(s["total"] for s in attrib["per_instrument"].values()) == pytest.approx(
        equity_gain, abs=1e-3)
    # 恒等式 3:Σ 标的 funding == 管线 total_funding
    assert sum(s["funding"] for s in attrib["per_instrument"].values()) == pytest.approx(
        run.pnl["total_funding"], abs=1e-6)


# ---------------------------------------------------------------------------
# 6) 确定性
# ---------------------------------------------------------------------------


def test_reports_are_deterministic():
    a = cost_stress_report({10.0: 1.2, 15.0: 1.0, 20.0: 0.8})
    b = cost_stress_report({10.0: 1.2, 15.0: 1.0, 20.0: 0.8})
    assert a == b
    grid, center = _grid(1.0, [0.9, 1.1, 1.0, 0.95, None])
    assert perturbation_report(grid, center) == perturbation_report(grid, center)
    inst = {"BTC": 500.0, "ETH": 300.0, "SOL": 150.0, "XRP": 50.0}
    c1 = concentration_report([100.0, -20.0, 130.0, 40.0], inst,
                              period_times=[1640995200000 + i * _WEEK for i in range(4)])
    c2 = concentration_report([100.0, -20.0, 130.0, 40.0], inst,
                              period_times=[1640995200000 + i * _WEEK for i in range(4)])
    assert c1 == c2


def test_threshold_defaults_are_documented_uncalibrated():
    for th in (COST_STRESS_THRESHOLDS, PERTURBATION_THRESHOLDS, CONCENTRATION_THRESHOLDS):
        assert th["provenance"] == "UNCALIBRATED_ENGINEERING_DEFAULT"
