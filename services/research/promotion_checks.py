"""§6.2 晋升门槛(promotion gate)可复用检查组件 —— 纯函数、无 IO/RNG/墙钟。

平台此前没有的三项一等检查,本模块一次性补齐为**纯函数**(只吃预算好的数字,不碰数据/时钟):

1. ``cost_stress_report`` —— 成本压力:base / 1.5x / 2x 成本档下的夏普衰减与成本弹性
   (ΔSharpe/bps + 线性外推 breakeven 成本)。edge 不必在每档都为正,但报告必须把弹性摆出来。
2. ``perturbation_report`` —— 参数扰动:部署点在参数网格上不得是孤立尖峰(spike-optimum);
   也不得低于邻域中位数(部署点局部代表性)。PASS/WARN/FAIL 结构化判定 + 完整表面。
3. ``concentration_report`` —— 集中度:剔除最佳标的 / 最佳月份 / 极端事件(单期)后,
   edge 必须仍保留正贡献(leave-one-out / remove-best-k)。

外加一个诚实的逐标的 PnL 归因原语 ``attribute_period_contributions``:干跑簿的
detail/equity 结构**不存**逐腿 PnL,本函数用与
``services.execution.dry_run_pipeline.run_multi_period_dry_run`` 完全相同的语义
(整手目标数量、按决策价成交、平 bps 佣金、调仓后簿上计资金费)重建逐标的贡献。
恒等式(测试钉死):Σ_标的 (price_pnl + funding − cost) == 管线同输入的 equity_final − aum。

所有判定阈值都是 **UNCALIBRATED 工程默认**(逐个函数 docstring 记录),与平台其它
UNCALIBRATED_PRINCIPLED 口径同一诚实姿态:数字可查、阈值可辩、绝不假装已校准。
"""
from __future__ import annotations

import math
import statistics
import time
from typing import Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# 阈值(UNCALIBRATED 工程默认;改动 = 新口径,须显式记录)
# ---------------------------------------------------------------------------

COST_STRESS_THRESHOLDS = {
    # 首个加压档(高于 base 的最低档)夏普 ≤ 0 → FAIL(edge 在 +50% 成本内蒸发);
    # 仅最高档 ≤ 0 → WARN(edge 死在 1.5x–2x 之间,边际)。
    "min_sharpe_stressed": 0.0,
    # 逐档夏普允许的回升幅度(换手路径依赖可让更高成本偶然改善夏普;超过即非单调 → WARN)。
    "monotonic_tolerance": 0.05,
    "provenance": "UNCALIBRATED_ENGINEERING_DEFAULT",
}

PERTURBATION_THRESHOLDS = {
    # 邻域至少 4 个有效点才下判定,否则 None(不假装通过)。
    "min_neighborhood": 4,
    # 平台带:部署点距邻域最大值 ≤ max(plateau_abs, plateau_rel*|max|) 视为「在平台上」→ PASS。
    "plateau_abs": 0.05,
    "plateau_rel": 0.10,
    # 尖峰:部署点高出邻域最大值 > max(spike_abs, spike_rel*|max|) 视为孤立尖峰 → FAIL。
    "spike_abs": 0.10,
    "spike_rel": 0.25,
    "provenance": "UNCALIBRATED_ENGINEERING_DEFAULT",
}

CONCENTRATION_THRESHOLDS = {
    # 每个维度至少 3 个贡献者才下判定;top-3 诊断要求 ≥ 4 个(3 个时剔前 3 恒为空,跳过)。
    "min_contributors": 3,
    # top-1 贡献占比 > 0.50 → WARN;> 0.67 → FAIL。
    "warn_top1_share": 0.50,
    "fail_top1_share": 0.67,
    "provenance": "UNCALIBRATED_ENGINEERING_DEFAULT",
}

_VERDICT_RANK = {"PASS": 0, "WARN": 1, "FAIL": 2}


def _r(x, nd=6):
    """None/非有限值安全取整;非有限浮点 → None(绝不落 NaN/Inf 进证据)。"""
    if x is None:
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return round(x, nd)


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _worst(verdicts) -> Optional[str]:
    """FAIL > WARN > PASS;None(数据不足)不参与,全 None → None。"""
    vs = [v for v in verdicts if v is not None]
    if not vs:
        return None
    return max(vs, key=lambda v: _VERDICT_RANK[v])


# ---------------------------------------------------------------------------
# 1) 成本压力
# ---------------------------------------------------------------------------


def cost_stress_report(sharpe_by_tier: Mapping, *, thresholds: Optional[Mapping] = None) -> dict:
    """成本压力报告(纯函数,只吃各成本档预算好的夏普)。

    `sharpe_by_tier`: {cost_bps(float): sharpe(float|None)} —— 至少 base 与一档加压;
    最低成本档视为 base。任一有效档缺失/非有限 → verdict None(数据不足绝不假通过)。

    判定(thresholds 可覆盖,默认 UNCALIBRATED 工程默认):
      - base 档夏普 ≤ 0 → FAIL(BASE_NONPOSITIVE:edge 在基准成本下就不存在);
      - 首个加压档夏普 ≤ 0 → FAIL(EDGE_DIES_AT_FIRST_STRESS:+50% 成本内蒸发);
      - 仅更高加压档 ≤ 0 → WARN(EDGE_DIES_AT_HIGHER_TIER:死在 1.5x–2x 之间,边际);
      - 逐档夏普回升超过 monotonic_tolerance → WARN(NON_MONOTONIC:路径依赖伪影,人工复核);
      - 否则 PASS。
    弹性 = (s_base − s_max_tier) / (cost_max − cost_base)(夏普/bps);弹性 > 0 时线性外推
    breakeven 成本(夏普归零处),≤ 0 → None(edge 在外推下不消亡,如实记录)。
    """
    th = dict(COST_STRESS_THRESHOLDS)
    if thresholds:
        th.update(thresholds)
    out = {"tiers": {}, "base_cost_bps": None, "n_tiers": 0,
           "elasticity_sharpe_per_bps": None, "degradation_at_max_tier": None,
           "degradation_pct_at_max_tier": None, "breakeven_cost_bps": None,
           "monotonic": None, "verdict": None, "reasons": [],
           "thresholds": dict(th), "provenance": th["provenance"]}
    if not sharpe_by_tier:
        out["reasons"].append("INSUFFICIENT_DATA:empty_tiers")
        return out
    tiers = {}
    missing = []
    for cost, s in sharpe_by_tier.items():
        c = float(cost)
        if _finite(s):
            tiers[c] = float(s)
        else:
            missing.append(c)
    out["n_tiers"] = len(tiers)
    out["tiers"] = {repr(c): _r(s, 4) for c, s in sorted(tiers.items())}
    if missing:
        out["reasons"].append("INSUFFICIENT_DATA:missing_sharpe_at_bps=" + ",".join(repr(c) for c in sorted(missing)))
        return out
    if len(tiers) < 2:
        out["reasons"].append("INSUFFICIENT_DATA:need_base_plus_one_stressed_tier")
        return out

    costs = sorted(tiers)
    base = costs[0]
    out["base_cost_bps"] = base
    s_base = tiers[base]
    stressed = costs[1:]
    s_max = tiers[costs[-1]]
    span = costs[-1] - base
    elasticity = (s_base - s_max) / span if span > 0 else None
    out["elasticity_sharpe_per_bps"] = _r(elasticity, 6)
    out["degradation_at_max_tier"] = _r(s_base - s_max, 4)
    out["degradation_pct_at_max_tier"] = _r((s_base - s_max) / abs(s_base), 4) if s_base else None
    if elasticity is not None and elasticity > 0 and s_base > 0:
        out["breakeven_cost_bps"] = _r(base + s_base / elasticity, 2)

    # 单调性:逐档夏普不得显著回升(路径依赖容差内允许)。
    monotonic = all(
        tiers[costs[i + 1]] - tiers[costs[i]] <= th["monotonic_tolerance"]
        for i in range(len(costs) - 1)
    )
    out["monotonic"] = monotonic

    floor = th["min_sharpe_stressed"]
    if s_base <= 0:
        out["verdict"] = "FAIL"
        out["reasons"].append("BASE_NONPOSITIVE:edge_absent_at_base_cost")
    elif tiers[stressed[0]] <= floor:
        out["verdict"] = "FAIL"
        out["reasons"].append(f"EDGE_DIES_AT_FIRST_STRESS:sharpe@{stressed[0]}bps={_r(tiers[stressed[0]], 4)}")
    else:
        dying = [c for c in stressed[1:] if tiers[c] <= floor]
        if dying:
            out["verdict"] = "WARN"
            out["reasons"].append("EDGE_DIES_AT_HIGHER_TIER:bps=" + ",".join(repr(c) for c in dying))
        elif not monotonic:
            out["verdict"] = "WARN"
            out["reasons"].append("NON_MONOTONIC_DEGRADATION:path_dependence_artifact")
        else:
            out["verdict"] = "PASS"
            out["reasons"].append("EDGE_SURVIVES_ALL_TIERS_MONOTONIC")
    return out


# ---------------------------------------------------------------------------
# 2) 参数扰动
# ---------------------------------------------------------------------------


def perturbation_report(grid: Mapping, center, *, thresholds: Optional[Mapping] = None) -> dict:
    """参数扰动报告(纯函数):部署点在邻域中不得是孤立尖峰,也不得是局部低点。

    `grid`: {参数点(任意可哈希): 指标(float|None)};`center`: 部署点的键。
    邻域 = 除 center 外的**有效**(有限浮点)点;缺失点计数但不参与统计。

    判定(thresholds 可覆盖,默认 UNCALIBRATED 工程默认):
      - center 缺失/无效 → verdict None(CENTER_MISSING);
      - 有效邻域 < min_neighborhood → verdict None(INSUFFICIENT_DATA);
      - center 高出邻域最大值超过尖峰容差 → FAIL(SPIKE_OPTIMUM:孤立尖峰=过拟合签名);
      - center 低于邻域中位数 → FAIL(BELOW_NEIGHBORHOOD_MEDIAN:部署点是局部低点,
        宣称的 edge 在该参数选择上不具代表性);
      - center 落在邻域最大值的平台带内 → PASS(ON_PLATEAU:参数选择处于稳健平台);
      - 其间 → WARN(OFF_PLATEAU:edge 在但部署点离局部最优偏远,参数选择待复核)。
    """
    th = dict(PERTURBATION_THRESHOLDS)
    if thresholds:
        th.update(thresholds)
    out = {"center": center if isinstance(center, str) else repr(center), "center_metric": None,
           "n_grid": len(grid) if grid else 0,
           "n_neighborhood": 0, "n_missing": 0, "surface": {},
           "neighborhood_median": None, "neighborhood_max": None, "neighborhood_min": None,
           "center_rank_pct": None, "plateau_gap": None, "spike_gap": None,
           "verdict": None, "reasons": [], "thresholds": dict(th),
           "provenance": th["provenance"]}
    if not grid:
        out["reasons"].append("INSUFFICIENT_DATA:empty_grid")
        return out
    out["surface"] = {(k if isinstance(k, str) else repr(k)): _r(v, 4)
                      for k, v in sorted(grid.items(), key=lambda kv: repr(kv[0]))}
    if center not in grid or not _finite(grid[center]):
        out["reasons"].append("CENTER_MISSING:deployed_point_absent_or_invalid")
        return out
    c_metric = float(grid[center])
    out["center_metric"] = _r(c_metric, 4)
    neighborhood = []
    n_missing = 0
    for k, v in grid.items():
        if k == center:
            continue
        if _finite(v):
            neighborhood.append(float(v))
        else:
            n_missing += 1
    out["n_neighborhood"] = len(neighborhood)
    out["n_missing"] = n_missing  # center 已在上游校验有效;此处只统计邻域缺失点
    if len(neighborhood) < th["min_neighborhood"]:
        out["reasons"].append(
            f"INSUFFICIENT_DATA:neighborhood_{len(neighborhood)}<min_{th['min_neighborhood']}")
        return out

    nb_max, nb_min = max(neighborhood), min(neighborhood)
    nb_med = statistics.median(neighborhood)
    rank = sum(1 for v in neighborhood if v <= c_metric) / len(neighborhood)
    plateau_band = max(th["plateau_abs"], th["plateau_rel"] * abs(nb_max))
    spike_band = max(th["spike_abs"], th["spike_rel"] * abs(nb_max))
    out["neighborhood_median"] = _r(nb_med, 4)
    out["neighborhood_max"] = _r(nb_max, 4)
    out["neighborhood_min"] = _r(nb_min, 4)
    out["center_rank_pct"] = _r(rank, 4)
    out["plateau_gap"] = _r(nb_max - c_metric, 4)
    out["spike_gap"] = _r(c_metric - nb_max, 4)

    if c_metric - nb_max > spike_band:
        out["verdict"] = "FAIL"
        out["reasons"].append(
            f"SPIKE_OPTIMUM:center_exceeds_neighborhood_max_by>{_r(spike_band, 4)}")
    elif rank < 0.5:
        out["verdict"] = "FAIL"
        out["reasons"].append(
            f"BELOW_NEIGHBORHOOD_MEDIAN:center_rank={_r(rank, 4)}<0.5")
    elif nb_max - c_metric <= plateau_band:
        out["verdict"] = "PASS"
        out["reasons"].append("ON_PLATEAU:center_within_plateau_band_of_neighborhood_max")
    else:
        out["verdict"] = "WARN"
        out["reasons"].append(
            f"OFF_PLATEAU:gap_to_neighborhood_max={_r(nb_max - c_metric, 4)}>band={_r(plateau_band, 4)}")
    return out


# ---------------------------------------------------------------------------
# 3) 集中度
# ---------------------------------------------------------------------------


def _dimension_report(contribs: Mapping, *, name: str, th: Mapping) -> dict:
    """单维度集中度:top-1/top-3 占比 + 剔除后残差。total ≤ 0 → FAIL;数据不足 → None。"""
    out = {"dimension": name, "total": None, "n_contributors": 0, "n_missing": 0,
           "top1": None, "top3_share": None, "residual_ex_top1": None,
           "residual_ex_top3": None, "verdict": None, "reasons": []}
    valid = {}
    n_missing = 0
    for k, v in (contribs or {}).items():
        if _finite(v):
            valid[k] = float(v)
        else:
            n_missing += 1
    out["n_contributors"] = len(valid)
    out["n_missing"] = n_missing
    if len(valid) < th["min_contributors"]:
        out["reasons"].append(
            f"INSUFFICIENT_DATA:contributors_{len(valid)}<min_{th['min_contributors']}")
        return out
    ordered = sorted(valid.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(valid.values())
    out["total"] = _r(total, 2)
    if total <= 0:
        out["verdict"] = "FAIL"
        out["reasons"].append("TOTAL_NONPOSITIVE:no_edge_to_concentrate")
        return out
    top1_key, top1_val = ordered[0]
    top3 = ordered[:3]
    share1 = top1_val / total
    out["top1"] = {"key": top1_key if isinstance(top1_key, str) else repr(top1_key),
                   "contribution": _r(top1_val, 2), "share": _r(share1, 4)}
    residual1 = total - top1_val
    out["residual_ex_top1"] = _r(residual1, 2)
    if len(valid) >= 4:
        top3_val = sum(v for _, v in top3)
        out["top3_share"] = _r(top3_val / total, 4)
        out["residual_ex_top3"] = _r(total - top3_val, 2)
    else:
        out["reasons"].append("TOP3_SKIPPED:fewer_than_4_contributors")

    if residual1 <= 0:
        out["verdict"] = "FAIL"
        out["reasons"].append("SINGLE_CONTRIBUTOR_CARRIES_EDGE:residual_ex_top1<=0")
    elif share1 > th["fail_top1_share"]:
        out["verdict"] = "FAIL"
        out["reasons"].append(f"TOP1_SHARE:{_r(share1, 4)}>fail_{th['fail_top1_share']}")
    elif out["residual_ex_top3"] is not None and out["residual_ex_top3"] <= 0:
        out["verdict"] = "WARN"
        out["reasons"].append("TOP3_CARRIES_EDGE:residual_ex_top3<=0")
    elif share1 > th["warn_top1_share"]:
        out["verdict"] = "WARN"
        out["reasons"].append(f"TOP1_SHARE:{_r(share1, 4)}>warn_{th['warn_top1_share']}")
    else:
        out["verdict"] = "PASS"
        out["reasons"].append("EDGE_SURVIVES_LEAVE_ONE_OUT")
    return out


def concentration_report(period_contribs: Sequence, instrument_contribs: Mapping, *,
                         period_times: Optional[Sequence[int]] = None,
                         thresholds: Optional[Mapping] = None) -> dict:
    """集中度报告(纯函数):剔除最佳标的 / 最佳月份 / 极端单期后,edge 须仍为正。

    `period_contribs`: 逐期美元贡献序列(float|None)——「极端事件」维度(单期 top-1/top-3)。
    `instrument_contribs`: {标的: 美元贡献(float|None)}——「标的」维度。
    `period_times`(可选,epoch ms,与 period_contribs 对齐):给出即派生「月份」维度
    (time.gmtime 格式化给定值,纯函数;ic_panel 的 per_year 同一先例)。缺 → 月份维度
    verdict None(INSUFFICIENT_DATA),不假装通过。

    每维度判定(阈值 UNCALIBRATED 工程默认):残差剔 top-1 ≤ 0 → FAIL;top-1 占比 > 0.67
    → FAIL;剔 top-3 残差 ≤ 0 → WARN;top-1 占比 > 0.50 → WARN;否则 PASS。
    overall = 三维度最差(FAIL > WARN > PASS;None 不参与;全 None → None)。
    """
    th = dict(CONCENTRATION_THRESHOLDS)
    if thresholds:
        th.update(thresholds)
    periods = list(period_contribs or [])
    dims = {
        "instrument": _dimension_report(dict(instrument_contribs or {}), name="instrument", th=th),
        "period": _dimension_report({i: v for i, v in enumerate(periods)}, name="period", th=th),
    }
    if period_times is not None and len(period_times) == len(periods) and periods:
        by_month: dict = {}
        for i, v in enumerate(periods):
            if not _finite(v):
                continue
            key = time.strftime("%Y-%m", time.gmtime(int(period_times[i]) / 1000))
            by_month[key] = by_month.get(key, 0.0) + float(v)
        dims["month"] = _dimension_report(by_month, name="month", th=th)
    else:
        dims["month"] = {"dimension": "month", "total": None, "n_contributors": 0, "n_missing": 0,
                         "top1": None, "top3_share": None, "residual_ex_top1": None,
                         "residual_ex_top3": None, "verdict": None,
                         "reasons": ["INSUFFICIENT_DATA:period_times_absent_or_misaligned"]}
    overall = _worst(d["verdict"] for d in dims.values())
    return {"dimensions": dims, "verdict": overall,
            "thresholds": dict(th), "provenance": th["provenance"]}


# ---------------------------------------------------------------------------
# 逐标的 PnL 归因原语(喂 concentration_report;与干跑管线同语义)
# ---------------------------------------------------------------------------


def _plan_quantities(weights: Mapping, prices: Mapping, aum: float) -> dict:
    """镜像 services.execution.dry_run_pipeline.plan_target_quantities 的 no-filters 路径
    (round(w*aum/px),价缺失/≤0 跳过,0 手不下)——与真函数的逐位等价由测试钉死。
    本地镜像是为了让本模块保持 stdlib-only(不反向拉入执行层的重依赖图)。"""
    out = {}
    for inst, w in (weights or {}).items():
        px = (prices or {}).get(inst)
        if px is None or px <= 0:
            continue
        q = int(round(w * aum / px))
        if q != 0:
            out[inst] = q
    return out


def attribute_period_contributions(*, periods, weights_by_period, prices_by_period, aum,
                                   cost_bps: float = 0.0, funding_rate_by_period=None) -> dict:
    """多期干跑簿的逐标的美元 PnL 归因(纯函数;管线 detail 不存逐腿 PnL,故诚实重建)。

    与 run_multi_period_dry_run 逐位相同的语义:整手目标数量(_plan_quantities 镜像)→
    按决策价成交(价缺失的平仓单跳过、持仓延续,镜像 simulate_fills)→ 平 cost_bps 佣金
    按成交增量计 → 调仓后簿上按 funding_rate_by_period 计资金费(−rate·qty·price,
    与 crypto 干跑驱动器 cash_flow_fn 同式;价缺失时该名贡献 0,与管线一致不用回退价)→
    持仓期价格变动按 t_k→t_{k+1} 归属;**标记缺失时回退最后成交价**(镜像
    paper_reconciliation.reconcile 的 resolved_marks.setdefault(instrument, last_price))。

    恒等式(测试钉死;驱动器实跑记录残差):Σ_标的 (price_pnl+funding−cost)
    == Σ per_period == 管线同输入的 equity_final − aum。**前提:门全批准**
    (若门拒绝过意图,持仓簿与管线分叉,残差会如实变大——驱动器必须记录 n_rejected 与残差)。

    per_period[k] = 归属到 periods[k] 决策的 P&L:[t_k→t_{k+1}] 价格变动 + t_k 资金费
    − t_k 佣金(末日无价格变动)。返回 {"per_instrument": {inst: {...}}, "per_period": [...]}。
    """
    rates_by = funding_rate_by_period or {}
    held: dict = {}
    last_fill_px: dict = {}
    per_inst: dict = {}
    per_period: list = []

    def _slot(inst):
        if inst not in per_inst:
            per_inst[inst] = {"price_pnl": 0.0, "funding": 0.0, "cost": 0.0, "total": 0.0}
        return per_inst[inst]

    def _mark(inst, prices):
        """reconcile 同式标记:当期价优先,缺失回退最后成交价(仍无 → None)。"""
        px = prices.get(inst)
        if px is not None and px > 0:
            return px
        return last_fill_px.get(inst)

    for k, t in enumerate(periods):
        prices_t = prices_by_period.get(t, {}) or {}
        targets = _plan_quantities(weights_by_period.get(t, {}), prices_t, aum)
        # 1) 成交增量 → 佣金(价缺失的意图被适配器接缝跳过,持仓延续)。
        period_cost = 0.0
        for inst in sorted(set(targets) | set(held)):
            dq = targets.get(inst, 0) - held.get(inst, 0)
            if dq == 0:
                continue
            px = prices_t.get(inst)
            if px is None or px <= 0:
                continue
            c = abs(dq) * px * cost_bps / 1e4
            _slot(inst)["cost"] += c
            period_cost += c
            last_fill_px[inst] = px
            if targets.get(inst, 0) == 0:
                held.pop(inst, None)
            else:
                held[inst] = targets[inst]
        # 2) 资金费:调仓后簿上,t 期费率(cash_flow_fn 同式;价缺失贡献 0,不回退)。
        period_funding = 0.0
        rates = rates_by.get(t, {}) or {}
        for inst in sorted(held):
            f = -rates.get(inst, 0.0) * held[inst] * prices_t.get(inst, 0.0)
            _slot(inst)["funding"] += f
            period_funding += f
        # 3) 价格变动 t_k → t_{k+1}(末日无;标记缺失回退最后成交价,镜像 reconcile)。
        period_price = 0.0
        if k + 1 < len(periods):
            prices_n = prices_by_period.get(periods[k + 1], {}) or {}
            for inst in sorted(held):
                p0, p1 = _mark(inst, prices_t), _mark(inst, prices_n)
                if p0 is None or p1 is None or p0 <= 0:
                    continue
                v = held[inst] * (p1 - p0)
                _slot(inst)["price_pnl"] += v
                period_price += v
        per_period.append(period_price + period_funding - period_cost)

    for inst, slot in per_inst.items():
        slot["total"] = slot["price_pnl"] + slot["funding"] - slot["cost"]
    return {"per_instrument": per_inst, "per_period": per_period}
