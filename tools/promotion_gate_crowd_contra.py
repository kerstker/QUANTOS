"""§6.2 晋升门槛三闸驱动器:crowd_contra 作为证明案例(成本压力 / 参数扰动 / 集中度)。

机构纪律(与 crowd_contra_variants 同一姿态):**预注册先于任何评估写入本文件**;网格 15 个参数点
全部经 run_registered_candidate_batch 记为 trial(register-then-evaluate);判定阈值全部
UNCALIBRATED 工程默认、随证据落盘;三闸 verdict 只落证据文件,不驱动任何自动晋级/下单。

三闸(可复用纯组件在 services/research/promotion_checks.py):
  1. 成本压力:部署点(q=0.30, 7d)book 过 {10, 15, 20}bps 三档同一 run_multi_period_dry_run,
     报夏普衰减 + 成本弹性 + breakeven 外推(edge 不必每档为正,弹性必须摆出)。
  2. 参数扰动:quantile {0.20,0.25,0.30,0.35,0.40} × cadence {3d,7d,14d} 15 点网格(部署点
     = 中心),同一簿构造同一管线同一成本(10bps),指标 = vol-target 年化夏普(可部署形态,
     按点自身节奏年化)——部署点不得是孤立尖峰、不得低于邻域中位数。
  3. 集中度:部署点基档(10bps)簿的逐标的美元 PnL 归因(promotion_checks.
     attribute_period_contributions,与管线同语义;恒等式残差实跑记录)+ 逐期贡献按月聚合,
     剔最佳标的/最佳月份/极端单期后 edge 须仍为正。

诚实边界:网格 15 点中 12 点与 crowd_contra_variants 族(17 trial)重叠——本驱动器是另一次
预注册实验(晋升门槛视角),15 点全部重新注册(保守方向:trial 计数只增不减、DSR 泄气更重);
与 variants 证据的同点夏普差落 cross_checks。集中度在 RAW 簿口径(实际持仓,非 vol-target
缩放);归因假设门全批准(n_rejected 实跑记录,残差暴露任何分叉)。变体/本驱动器均不写
dry_run.db、不开 event bus;实盘晋级 = 另一独立人工决策 = 非目标(绝不碰 _STRATEGIES/mandate/
attestation/live 治理文件)。
"""
from __future__ import annotations

import json
import os
import time

from services.execution.backtest_authority import quarantine_legacy_backtest
from services.portfolio.crowding_gate import evaluate_crowding_gate, summarize_gate_decisions
from services.research import alpha_expression as ax
from services.research.promotion_checks import (CONCENTRATION_THRESHOLDS, COST_STRESS_THRESHOLDS,
                                                PERTURBATION_THRESHOLDS,
                                                attribute_period_contributions,
                                                concentration_report, cost_stress_report,
                                                perturbation_report)
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from quantos.contracts.alpha import AlphaDefinition
from services.alpha.config import AlphaFactoryConfig
from services.intelligence.registered_candidate_batch import run_registered_candidate_batch
from tools.cache_io import atomic_write_json
from tools.crowd_contra_variants import _ann, _mdd, cadence_rows, ppy_for_cadence
from tools.crypto_oi_research import _legs, build_panel
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.dry_run_crowd_contra_multi import _MAX_CROWDING_Z, _crowding_rank, _existing_crowd_run
from tools.search_trial_registry import (field_panel_dataset_version, make_search_id,
                                         production_experiment_registry)
from live.execution.crowding_score import crowding_zscore as _crowding_zscore  # 与干跑驱动器同一实现

_OUT = os.path.join("evidence", "promotion_gate_crowd_contra.json")
_VARIANTS_EVIDENCE = os.path.join("evidence", "crowd_contra_variants.json")
_AUM = 1_000_000.0
_BASE_COST_BPS = 10.0       # 与官方 crowd 干跑同一平费假设
_FAMILY = "promotion_gate_crowd_contra_v1"
_SEARCH_KIND = "PROMOTION_GATE_PERTURBATION"
_SOURCE = "tools.promotion_gate_crowd_contra"

# ---------------------------------------------------------------------------
# PRE-REGISTRATION(2026-07-25,先于任何评估写入;改动本块 = 新实验,须换 _FAMILY 版本号)
# ---------------------------------------------------------------------------

_COST_TIERS_BPS = (10.0, 15.0, 20.0)                 # base / 1.5x / 2x
_GRID_QUANTILES = (0.20, 0.25, 0.30, 0.35, 0.40)
_GRID_CADENCES = (3, 7, 14)
_CENTER = (0.30, 7)                                   # 部署点:crowd_contra 的生产参数
_GATE_METRIC = "sharpe_vt"                            # 可部署(vol-target)形态,按点自身节奏年化

PRE_REGISTRATION = {
    "family": _FAMILY,
    "registered_before_evaluation": True,
    "date": "2026-07-25",
    "trial_count_planned": 15,
    "center": {"quantile": _CENTER[0], "cadence_days": _CENTER[1],
               "expression": "cs_rank(neg(global_ls))"},
    "cost_tiers_bps": list(_COST_TIERS_BPS),
    "perturbation_grid": {"quantile": list(_GRID_QUANTILES), "cadence_days": list(_GRID_CADENCES),
                          "metric": _GATE_METRIC, "cost_bps": _BASE_COST_BPS},
    "thresholds": {"cost_stress": dict(COST_STRESS_THRESHOLDS),
                   "perturbation": dict(PERTURBATION_THRESHOLDS),
                   "concentration": dict(CONCENTRATION_THRESHOLDS)},
    "notes": [
        "网格 15 点全部经 run_registered_candidate_batch 注册(3 个节奏批);12/15 点与 "
        "crowd_contra_variants 族重叠,属另一次预注册实验的保守重复计数(trial 只增不减)。",
        "三闸判定阈值全部 UNCALIBRATED 工程默认,随证据落盘;verdict 只落本文件,不驱动自动晋级。",
        "成本压力/扰动指标 = vol-target 年化夏普(按点自身节奏 ppy 重算);集中度在 RAW 簿口径。",
        "集中度归因假设门全批准:n_rejected 与 Σ归因−(equity_final−aum) 残差实跑记录。",
    ],
}


def grid_key(quantile: float, cadence_days: int) -> str:
    return f"q{int(round(quantile * 100))}_{int(cadence_days)}d"


def build_grid_specs():
    """15 个预注册网格点(quantile × cadence);部署点标 role=center。"""
    specs = []
    for c in _GRID_CADENCES:
        for q in _GRID_QUANTILES:
            role = "center" if (q, c) == _CENTER else "grid"
            specs.append({"id": f"pgate_crowd_{grid_key(q, c)}", "quantile": q,
                          "cadence_days": c, "role": role})
    ids = [s["id"] for s in specs]
    assert len(ids) == 15 and len(set(ids)) == 15, "预注册网格必须是 15 个唯一 id"
    assert sum(1 for s in specs if s["role"] == "center") == 1
    return tuple(specs)


# ---------------------------------------------------------------------------
# 簿构造 + 管线执行(与 tools/crowd_contra_variants.py stage_dry_run 同一接线,成本参数化)
# ---------------------------------------------------------------------------


def build_book(panel, sig, quantile: float, cadence_days: int) -> dict:
    """逐期权重/价格/费率簿:与 tools/dry_run_crowd_contra_multi.py 及 variants stage_dry_run
    逐位相同的接线(_legs 分位腿 + 拥挤门 z>3.0 BLOCK 跳周 carry 持仓 + 腿内价格/费率)。"""
    insts = panel.instruments
    close, fmat, gmat, ev = (panel.matrices["close"], panel.matrices["funding"],
                             panel.matrices["global_ls"], panel.event_times)
    rows = cadence_rows(ev, cadence_days)
    periods, weights_by_period, prices_by_period, rate_by_period = [], {}, {}, {}
    gate_decisions, prev_weights = [], None
    for r in rows:
        legs = _legs(sig[r], quantile)
        if not legs:
            continue
        t = int(ev[r])
        target = {insts[i]: w for i, w in legs.items()}
        crowd = {insts[i]: rank for i, rank in _crowding_rank(gmat, r, list(legs)).items()}
        z_map = {insts[i]: z for i in legs if (z := _crowding_zscore(gmat, r, i)) is not None}
        blocked = False
        if crowd:
            decision = evaluate_crowding_gate(
                target, crowd, average_warn_only=True,
                crowding_z_by_symbol=z_map, max_crowding_z=_MAX_CROWDING_Z)
            gate_decisions.append(decision)
            blocked = decision.status == "BLOCK"
        periods.append(t)
        idx = set(legs)
        if blocked and prev_weights is not None:
            weights_by_period[t] = dict(prev_weights)
            idx |= {i for i in range(len(insts)) if insts[i] in prev_weights}
        else:
            weights_by_period[t] = target
            prev_weights = target
        prices_by_period[t] = {insts[i]: close[r][i] for i in idx if close[r][i] and close[r][i] > 0}
        rate_by_period[t] = {insts[i]: (fmat[r][i] or 0.0) for i in idx}
    return {"periods": periods, "weights": weights_by_period, "prices": prices_by_period,
            "rates": rate_by_period, "gate_decisions": gate_decisions}


def run_book(book: dict, strategy_id: str, cost_bps: float, ppy: int) -> dict:
    """同一 run_multi_period_dry_run(整手、fail-closed 门、资金费、vol-target),成本参数化;
    不写 dry_run.db、不开 event bus。年化按点自身节奏(_ann/_mdd,7d 与管线逐位一致)。"""
    periods = book["periods"]
    weights_by_period, prices_by_period, rate_by_period = (book["weights"], book["prices"],
                                                           book["rates"])
    held_names = sorted({n for w in weights_by_period.values() for n in w})

    def cash_flow_fn(held, prices, period):
        rates = rate_by_period.get(period, {})
        return sum(-rates.get(i, 0.0) * q_ * prices.get(i, 0.0) for i, q_ in held.items())

    limits = RiskLimits(
        allowed_instruments=frozenset(held_names), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10 ** 13, max_order_notional=1e14, fat_finger_qty=10 ** 13,
        max_gross_exposure=1e14, max_net_exposure=1e14, max_leverage=1e7, max_single_name_pct=1e7,
        max_turnover=1e7, max_participation_rate=1e7, max_instrument_vol=5.0, daily_loss_limit=1e14,
        max_drawdown=1.0)
    gate = PreTradeRiskGate(limits, kill_switch=KillSwitch())

    def ctx_factory(intent, prices, period):
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=_AUM * 10, equity=_AUM * 10, day_pnl=0.0,
                                     peak_equity=_AUM * 10, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                                 price_as_of=period, est_daily_volume=1e12, instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id=strategy_id, approved=True,
                                      approval_id="research-dry-run"),
            now=period, max_staleness=10 ** 15)

    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period,
        gate=gate, ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id=strategy_id,
        aum=_AUM, cost_bps=cost_bps, cash_flow_fn=cash_flow_fn,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": ppy},
        event_bus=None, publish_completion_event=False)
    raw_rets = [x["ret"] for x in run.detail["equity_curve"]]
    vt_rets = [x["ret"] for x in run.detail["equity_curve_vt"]]
    return {"run": run, "n_periods": run.pnl["n_periods"], "n_rejected": run.n_rejected,
            "total_return": run.pnl["total_return"], "total_funding": run.pnl["total_funding"],
            "reconciliation_residual": run.pnl["residual"],
            "sharpe_raw": _ann(raw_rets, ppy), "max_drawdown_raw": _mdd(raw_rets),
            "sharpe_vt": _ann(vt_rets, ppy), "max_drawdown_vt": _mdd(vt_rets),
            "crowding_gate": summarize_gate_decisions(book["gate_decisions"]),
            "ppy": ppy, "cost_bps": cost_bps}


# ---------------------------------------------------------------------------
# trial 注册(register-then-evaluate;按节奏 3 批,ppy 各按节奏)—— 与 variants 同一组合
# ---------------------------------------------------------------------------


def _definition_for(spec):
    return AlphaDefinition(
        alpha_id=spec["id"], name=spec["id"],
        factor_ids=("close", "global_ls"),
        universe="crypto_oi_majors",
        params={"family": _FAMILY, "kind": "perturbation_grid", "role": spec["role"],
                "expr": ax.serialize(CROWD_CONTRA), "quantile": repr(spec["quantile"]),
                "cadence_days": str(spec["cadence_days"])})


def register_grid_trials(specs, sig, panel, *, registry, recorded_at: int) -> dict:
    """15 个网格点按节奏分 3 批注册并过评估器(DSR 按全 trial 总体泄气,逐候选收进 honesty)。"""
    dataset_version = field_panel_dataset_version(
        panel, source_versions={"family": _FAMILY, "universe": "crypto_oi_majors"})
    out = {"dataset_version": dataset_version, "trial_count_before": registry._store.trial_count(),
           "batches": [], "candidates": {}}
    for cadence in _GRID_CADENCES:
        group = [s for s in specs if s["cadence_days"] == cadence]
        candidates = tuple(
            (_definition_for(spec),
             tuple(ax.panel_to_periods(sig, panel, fwd_horizon=cadence, non_overlapping=True)))
            for spec in group)
        search_id = make_search_id(
            source=_SOURCE, search_kind=_SEARCH_KIND, dataset_version=dataset_version,
            recorded_at=recorded_at,
            params={"family": _FAMILY, "cadence_days": cadence, "candidate_count": len(candidates)})
        batch = run_registered_candidate_batch(
            candidates, registry=registry, search_id=search_id, search_kind=_SEARCH_KIND,
            search_source=_SOURCE, dataset_version=dataset_version, recorded_at=recorded_at,
            periods_per_year=ppy_for_cadence(cadence),
            config=AlphaFactoryConfig(compute_regime_evidence=True))
        report = batch.get("report", {})
        for spec in group:
            entry = report.get(spec["id"], {})
            ev = entry.get("evaluation")
            out["candidates"][spec["id"]] = {
                "search_id": search_id,
                "evaluator_verdict": ev.verdict.value if ev else None,
                "state": entry.get("state"),
                "deflated_sharpe": None if ev is None else round(ev.deflated_sharpe, 4),
                "ic_tstat": None if ev is None else ev.ic_tstat,
                "net_sharpe": None if ev is None else round(ev.net_sharpe, 4),
                "n_periods": None if ev is None else ev.n_periods,
            }
        out["batches"].append({"cadence_days": cadence, "search_id": search_id,
                               "n_candidates": len(candidates),
                               "registered_candidate_count": batch.get("registered_candidate_count")})
    out["trial_count_after"] = registry._store.trial_count()
    out["trial_count_delta"] = out["trial_count_after"] - out["trial_count_before"]
    return out


# ---------------------------------------------------------------------------
# 三闸装配(纯装配,可单测:全部吃预算好的数字)
# ---------------------------------------------------------------------------


def evaluate_gates(*, grid_metrics: dict, center_key: str, cost_sharpe_by_tier: dict,
                   period_contribs, instrument_contribs, period_times) -> dict:
    """三闸 verdict 装配:overall = FAIL > WARN > None(有闸数据不足则整体不完整,不假装 PASS) > PASS。"""
    gates = {
        "cost_stress": cost_stress_report(cost_sharpe_by_tier),
        "perturbation": perturbation_report(grid_metrics, center_key),
        "concentration": concentration_report(period_contribs, instrument_contribs,
                                              period_times=period_times),
    }
    verdicts = [g["verdict"] for g in gates.values()]
    if any(v == "FAIL" for v in verdicts):
        overall = "FAIL"
    elif any(v == "WARN" for v in verdicts):
        overall = "WARN"
    elif any(v is None for v in verdicts):
        overall = None
    else:
        overall = "PASS"
    return {"gates": gates, "overall_verdict": overall}


def variants_overlap_map(variants_payload: dict) -> dict:
    """从 crowd_contra_variants 证据的预注册变体表抽出 (quantile,cadence) → 网格键 的映射
    (只取纯网格/分位/节奏/基线变体;复合变体不在本网格)。"""
    out = {}
    pre = (variants_payload or {}).get("pre_registration", {})
    for v in pre.get("variants", ()):
        if v.get("kind") not in ("baseline", "quantile", "cadence", "grid"):
            continue
        q, c = v.get("quantile"), v.get("cadence_days")
        if q is None or c is None:
            continue
        out[v["id"]] = grid_key(float(q), int(c))
    return out


def gate_reasons_for_display(gate: dict) -> list:
    """打印用 reason 扁平化:平铺报告(cost/perturbation)直接取 reasons;concentration 的
    reasons 嵌在 dimensions 里,按「维度:原因」扁平化(2026-07-25 首跑打印崩溃修复——
    崩溃发生在证据落盘之后,纯展示层)。"""
    reasons = gate.get("reasons")
    if isinstance(reasons, list):
        return [str(r) for r in reasons]
    dims = gate.get("dimensions")
    if isinstance(dims, dict):
        return [f"{name}:{r}" for name, dim in dims.items()
                for r in (dim.get("reasons") or ())]
    return []


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _main(registry) -> None:
    quarantine_legacy_backtest("tools.promotion_gate_crowd_contra")
    recorded_at = int(time.time() * 1000)
    specs = build_grid_specs()
    center_spec = next(s for s in specs if s["role"] == "center")
    center_key = grid_key(*_CENTER)
    panel, instruments = build_panel()
    ev = panel.event_times
    print(f"面板: {len(instruments)} 币 × {len(ev)} 日 "
          f"({time.strftime('%Y-%m-%d', time.gmtime(ev[0]/1000))} → "
          f"{time.strftime('%Y-%m-%d', time.gmtime(ev[-1]/1000))})", flush=True)
    sig = ax.evaluate(CROWD_CONTRA, panel)   # 信号与 (q, cadence) 无关,只评一次

    # ---- Step 0: trial 注册(先于一切评估;3 个节奏批 × 5 点)----
    print("=== trial 注册(15 网格点 × 3 节奏批)===", flush=True)
    registration = register_grid_trials(specs, sig, panel, registry=registry,
                                        recorded_at=recorded_at)
    print(f"  trial_count {registration['trial_count_before']} → {registration['trial_count_after']} "
          f"(Δ {registration['trial_count_delta']})", flush=True)

    # ---- Step 1: 扰动网格(15 点 × 10bps)----
    print("=== 参数扰动网格(15 点,成本 10bps,指标=VT 夏普)===", flush=True)
    grid_results, books = {}, {}
    for spec in specs:
        book = build_book(panel, sig, spec["quantile"], spec["cadence_days"])
        books[spec["id"]] = book
        res = run_book(book, spec["id"], _BASE_COST_BPS, ppy_for_cadence(spec["cadence_days"]))
        grid_results[grid_key(spec["quantile"], spec["cadence_days"])] = res
        print(f"  {grid_key(spec['quantile'], spec['cadence_days']):8s} "
              f"n={res['n_periods']:3d} VT={res['sharpe_vt']:7.4f} raw={res['sharpe_raw']:7.4f} "
              f"mdd_vt={res['max_drawdown_vt']:6.4f} rej={res['n_rejected']}", flush=True)
    grid_metrics = {k: v["sharpe_vt"] for k, v in grid_results.items()}

    # ---- Step 2: 成本压力(部署点簿 × {10,15,20}bps;10bps 复用网格中心)----
    print("=== 成本压力(部署点 q30_7d × {10,15,20}bps)===", flush=True)
    center_book = books[center_spec["id"]]
    center_ppy = ppy_for_cadence(_CENTER[1])
    cost_runs = {_BASE_COST_BPS: grid_results[center_key]}
    for tier in _COST_TIERS_BPS:
        if tier == _BASE_COST_BPS:
            continue
        cost_runs[tier] = run_book(center_book, f"{center_spec['id']}_cost{int(tier)}bps",
                                   tier, center_ppy)
    for tier in _COST_TIERS_BPS:
        r = cost_runs[tier]
        print(f"  {tier:5.1f}bps  VT={r['sharpe_vt']:7.4f} raw={r['sharpe_raw']:7.4f} "
              f"tot_ret={r['total_return']:8.4f} rej={r['n_rejected']}", flush=True)
    cost_sharpe_by_tier = {tier: cost_runs[tier]["sharpe_vt"] for tier in _COST_TIERS_BPS}
    cost_sharpe_raw_by_tier = {tier: cost_runs[tier]["sharpe_raw"] for tier in _COST_TIERS_BPS}

    # ---- Step 3: 集中度(部署点基档簿的逐标的归因 + 逐期/逐月贡献)----
    print("=== 集中度(部署点基档簿逐标的归因)===", flush=True)
    attrib = attribute_period_contributions(
        periods=center_book["periods"], weights_by_period=center_book["weights"],
        prices_by_period=center_book["prices"], aum=_AUM, cost_bps=_BASE_COST_BPS,
        funding_rate_by_period=center_book["rates"])
    center_run = grid_results[center_key]["run"]
    curve = center_run.detail["equity_curve"]
    equity_gain = curve[-1]["equity"] - _AUM
    attrib_total = sum(attrib["per_period"])
    attrib_residual = attrib_total - equity_gain
    instrument_contribs = {inst: slot["total"] for inst, slot in attrib["per_instrument"].items()}
    print(f"  Σ归因 ${attrib_total:,.2f} vs 管线权益增量 ${equity_gain:,.2f} "
          f"· 残差 ${attrib_residual:,.6f} · 门拒绝 {grid_results[center_key]['n_rejected']}",
          flush=True)

    gates = evaluate_gates(
        grid_metrics=grid_metrics, center_key=center_key,
        cost_sharpe_by_tier=cost_sharpe_by_tier,
        period_contribs=attrib["per_period"], instrument_contribs=instrument_contribs,
        period_times=center_book["periods"])

    # ---- 交叉检验(诚实,不硬断言:数据演进会如实体现为差值)----
    cross_checks = {}
    canonical = _existing_crowd_run()
    if canonical is not None:
        cp = canonical.get("pnl") if isinstance(canonical.get("pnl"), dict) else {}
        cross_checks["canonical_dry_run_db"] = {
            "stored_run_id": canonical.get("run_id"),
            "stored_total_return": cp.get("total_return"), "stored_sharpe": cp.get("sharpe"),
            "stored_sharpe_vt": cp.get("sharpe_vt"), "stored_n_periods": cp.get("n_periods"),
            "recomputed_total_return": grid_results[center_key]["total_return"],
            "recomputed_sharpe_ppy52": center_run.pnl["sharpe"],
            "delta_total_return": (None if cp.get("total_return") is None else
                                   round(grid_results[center_key]["total_return"] - cp["total_return"], 8)),
            "delta_sharpe": (None if cp.get("sharpe") is None else
                             round(center_run.pnl["sharpe"] - cp["sharpe"], 6)),
            "note": "正典库记录 vs 本次同接线重算;数据演进会体现为非零差值(如实记录,不硬断言)。",
        }
    if os.path.isfile(_VARIANTS_EVIDENCE):
        with open(_VARIANTS_EVIDENCE, encoding="utf-8") as fh:
            variants_payload = json.load(fh)
        vres = (variants_payload or {}).get("results", {})
        deltas = {}
        for vid, key in variants_overlap_map(variants_payload).items():
            vt = ((vres.get(vid) or {}).get("stages", {}).get("dry_run") or {}).get("sharpe_vt")
            if vt is not None and key in grid_metrics:
                deltas[key] = round(grid_metrics[key] - vt, 6)
        cross_checks["variants_family_overlap"] = {
            "n_overlap": len(deltas), "delta_sharpe_vt_by_point": deltas,
            "max_abs_delta": max((abs(d) for d in deltas.values()), default=None),
            "note": "同一管线/同一簿构造/同一面板,12/15 网格点与 variants 族重叠;差值应≈0。",
        }

    payload = {
        "schema_version": 1,
        "evidence_class": "PROMOTION_GATE_REPORT",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": recorded_at // 1000,
        "alpha_id": "crowd_contra",
        "pre_registration": PRE_REGISTRATION,
        "dataset": {"universe": "crypto_oi_majors", "n_instruments": len(instruments),
                    "n_event_times": len(ev),
                    "date_start": time.strftime("%Y-%m-%d", time.gmtime(ev[0] / 1000)),
                    "date_end": time.strftime("%Y-%m-%d", time.gmtime(ev[-1] / 1000)),
                    "dataset_version": registration["dataset_version"]},
        "trial_registration": registration,
        "inputs": {
            "cost_stress": {"sharpe_vt_by_tier_bps": {repr(t): s for t, s in
                                                      sorted(cost_sharpe_by_tier.items())},
                            "sharpe_raw_by_tier_bps": {repr(t): s for t, s in
                                                       sorted(cost_sharpe_raw_by_tier.items())},
                            "tiers_bps": list(_COST_TIERS_BPS)},
            "perturbation": {"grid_sharpe_vt": grid_metrics,
                             "grid_sharpe_raw": {k: v["sharpe_raw"] for k, v in grid_results.items()},
                             "grid_max_drawdown_vt": {k: v["max_drawdown_vt"]
                                                      for k, v in grid_results.items()},
                             "grid_n_periods": {k: v["n_periods"] for k, v in grid_results.items()},
                             "center": center_key, "metric": _GATE_METRIC},
            "concentration": {"aum": _AUM, "cost_bps": _BASE_COST_BPS, "series": "raw_book",
                              "n_periods": len(center_book["periods"]),
                              "per_instrument": {inst: {k: round(v, 2) for k, v in slot.items()}
                                                 for inst, slot in
                                                 sorted(attrib["per_instrument"].items())},
                              "per_period_dollars": [round(v, 2) for v in attrib["per_period"]],
                              "attribution_residual_dollars": round(attrib_residual, 6),
                              "gate_n_rejected": grid_results[center_key]["n_rejected"]},
        },
        "gates": gates["gates"],
        "overall_verdict": gates["overall_verdict"],
        "cross_checks": cross_checks,
        "honesty": {
            "thresholds_provenance": "UNCALIBRATED_ENGINEERING_DEFAULT",
            "trial_count": 15,
            "registry_trial_count_before": registration["trial_count_before"],
            "registry_trial_count_after": registration["trial_count_after"],
            "caveats": [
                "对已知历史的执行现实回测,非前向/实盘;三闸是晋升*门槛证据*,晋级本身是另一独立人工决策。",
                "网格 15 点中 12 点与 crowd_contra_variants 族重叠,本实验保守重复注册(trial 只增不减,"
                "DSR 泄气更重);有效独立赌注数远小于 15(同一 global_ls 信号族)。",
                "集中度在 RAW 簿口径(实际持仓美元 PnL);vol-target 是组合层风险缩放,不改变贡献构成。",
                "归因假设门全批准(n_rejected 已记录);Σ归因 与管线权益增量的残差已记录,暴露任何分叉。",
                "成本弹性/breakeven 是线性外推(三档),非成本曲线的全形状;判定阈值全部 UNCALIBRATED。",
            ],
        },
    }
    atomic_write_json(_OUT, payload, indent=2)

    print("\n=== 三闸判定(crowd_contra §6.2)===", flush=True)
    for name, g in gates["gates"].items():
        print(f"  {name:14s} {str(g['verdict']):5s} · {'; '.join(gate_reasons_for_display(g))}",
              flush=True)
    print(f"  OVERALL        {gates['overall_verdict']}", flush=True)
    print(f"-> {_OUT}", flush=True)


def main() -> None:
    quarantine_legacy_backtest("tools.promotion_gate_crowd_contra")
    with production_experiment_registry() as registry:
        _main(registry)


if __name__ == "__main__":
    main()
