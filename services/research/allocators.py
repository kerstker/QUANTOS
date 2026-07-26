"""W5 条件组合配置器(spec §9)—— 策略层资本配置:从等权基线升级到「簇风险预算 + vol target +
regime 条件化 + 稳健回退」,全部纯函数、确定性、无 IO/RNG/墙钟/网络。

权重语义(文档化约定):策略层权重 = **非负资本配置**(capital allocation),策略内部可有多空腿,
配置层**不做空策略**;Σw = gross(默认 1.0,vol-target 变体允许 >1 至 lev_cap,显式标注为杠杆诊断)。
所有配置器输出 ``{"weights": {sid: w}, "diagnostics": {...}}``。

PIT 纪律:所有 trailing 统计在时点 ``end_idx`` 只用**严格过去**窗口 ``[end_idx-lookback, end_idx)``
(切片不含 end_idx,与 book_combine.rolling_inverse_vol_weights 同约定);regime 决策在 t 用 t-1
已完结周的状态(build_regime_series 内置 shift)。追加未来数据不改变历史时点输出(截断不变,
性质测试钉死)。

六个保留配置器(§9:等权为不可删除对照):
  equal_weight                  等权(undeletable control)
  inverse_vol                   逆波动(包装 book_combine + 约束投影)
  cluster_risk_parity           簇风险预算(簇内逆波动 × 簇间逆波动预算,簇帽投影)
  constrained_max_sharpe        约束最大夏普(trailing 均值向 0 收缩 + 确定性网格求解 + 约束)
  turnover_penalized            换手惩罚(对任意 base 配置器的死区部分调整;λ 单调性可测)
  regime_conditioned            regime 条件化(只调候选权重/风险预算,**绝不产生无约束指令**;
                                低置信 → 精确回退到稳健基线,性质测试钉死)

不确定参数全部 UNCALIBRATED_ENGINEERING_DEFAULT 并在 diagnostics 标注。
numpy 确定性用法同 services/research/ml 惯例(线程在 import numpy 前钉死为 1)。
"""
from __future__ import annotations

import os as _os

# 线程必须在 numpy 初始化 OpenMP/BLAS 之前钉死,确保确定性(同 services/research/ml 惯例)。
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    _os.environ.setdefault(_v, "1")

import math
import statistics
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence

import numpy as np

from services.research import factor_decomp as fd
from services.research.book_combine import rolling_inverse_vol_weights
from services.research.macro_regime import expanding_tercile_labels

_PERIODS_PER_YEAR = 52                      # 策略收益为 ISO 周频
_TOL = 1e-9

UNCALIBRATED = "UNCALIBRATED_ENGINEERING_DEFAULT"


@dataclass(frozen=True)
class AllocationConstraints:
    """配置层约束:gross 总额、单策略帽、簇帽。策略权重非负(资本配置,不做空策略)。

    可行性必要条件:Σ min(strategy_cap, cluster_cap) >= gross,否则 apply_constraints
    报 feasible=False(尽力配置 + 诚实标注,绝不静默超帽)。"""
    gross: float = 1.0
    strategy_cap: float = 0.40
    cluster_cap: float = 0.40


# ---------------------------------------------------------------------------
# 约束投影(确定性注水:每 pass 只加不超过 headroom 的量 → 收敛,不超帽)
# ---------------------------------------------------------------------------

def apply_constraints(weights: Mapping[str, float], cluster_map: Mapping[str, str],
                      constraints: AllocationConstraints) -> tuple:
    """把候选权重投影到可行域:非负 + 单策略帽 + 簇帽 + Σw=gross(可行时)。

    返回 (weights, diagnostics)。diagnostics["feasible"]=False 表示帽太紧配不满 gross
    (Σw < gross,诚实标注);绝不返回超帽权重。cluster_map: {sid: cluster_id}。"""
    ids = sorted(weights)
    w = {i: min(max(float(weights[i]), 0.0), constraints.strategy_cap) for i in ids}
    # 输入超 gross → 先等比缩回(clip 只减不增,但输入 Σ 本身可能 > gross)
    tot = sum(w.values())
    if tot > constraints.gross + _TOL and tot > 0:
        w = {i: v * constraints.gross / tot for i, v in w.items()}
    # 簇帽:簇内等比缩
    for c in sorted({cluster_map.get(i, i) for i in ids}):
        members = [i for i in ids if cluster_map.get(i, i) == c]
        csum = sum(w[i] for i in members)
        if csum > constraints.cluster_cap + _TOL and csum > 0:
            scale = constraints.cluster_cap / csum
            for i in members:
                w[i] *= scale
    # 注水:残差按 headroom 比例分配(min(策略帽余量, 簇帽余量))
    feasible = True
    for _ in range(200):
        residual = constraints.gross - sum(w.values())
        if residual <= _TOL:
            break
        cluster_sum = {}
        for i in ids:
            c = cluster_map.get(i, i)
            cluster_sum[c] = cluster_sum.get(c, 0.0) + w[i]
        headroom = {i: max(0.0, min(constraints.strategy_cap - w[i],
                                    constraints.cluster_cap - cluster_sum[cluster_map.get(i, i)]))
                    for i in ids}
        total_h = sum(headroom.values())
        if total_h <= _TOL:
            feasible = False
            break
        add = min(residual, total_h)
        for i in ids:
            w[i] += add * headroom[i] / total_h
    else:
        feasible = abs(constraints.gross - sum(w.values())) <= 1e-6
    final_sum = sum(w.values())
    if final_sum > constraints.gross + 1e-6:
        w = {i: v * constraints.gross / final_sum for i, v in w.items()}
        final_sum = sum(w.values())
    diagnostics = {"feasible": feasible, "gross": final_sum,
                   "constraints": {"gross": constraints.gross, "strategy_cap": constraints.strategy_cap,
                                   "cluster_cap": constraints.cluster_cap}}
    return w, diagnostics


def _result(weights: Mapping[str, float], diagnostics: dict) -> dict:
    return {"weights": {k: float(v) for k, v in sorted(weights.items())}, "diagnostics": diagnostics}


# ---------------------------------------------------------------------------
# 1) 等权(undeletable control) 2) 逆波动
# ---------------------------------------------------------------------------

def equal_weight(ids: Sequence[str], constraints: AllocationConstraints = AllocationConstraints(),
                 cluster_map: Optional[Mapping[str, str]] = None) -> dict:
    """等权对照(§9 不可删除)。caps 允许时恒 w_i = gross/n;cap < gross/n 时被投影(feasible 标注)。"""
    ids = sorted(ids)
    if not ids:
        raise ValueError("equal_weight: no strategies")
    cmap = cluster_map or {i: i for i in ids}
    w, diag = apply_constraints({i: constraints.gross / len(ids) for i in ids}, cmap, constraints)
    return _result(w, {**diag, "allocator": "equal_weight"})


def inverse_vol(returns_by_id: Mapping[str, Sequence[float]], end_idx: int,
                *, lookback: int = 26, min_history: int = 12,
                constraints: AllocationConstraints = AllocationConstraints(),
                cluster_map: Optional[Mapping[str, str]] = None) -> dict:
    """逆波动配置(包装 book_combine.rolling_inverse_vol_weights,PIT trailing 窗口)+ 约束投影。

    可用历史 < min_history → 等权回退(rolling_inverse_vol_weights 内置),diagnostics 标注。"""
    ids = sorted(returns_by_id)
    _check_aligned(returns_by_id, end_idx)
    series = [list(returns_by_id[i]) for i in ids]
    raw = rolling_inverse_vol_weights(series, end_idx, lookback, min_history)
    lo = max(0, end_idx - lookback)
    windows = [s[lo:end_idx] for s in series]
    # 与 book_combine.inverse_vol_weights 的内部回退条件同口径:历史不足 或 任一条零波动
    used_fallback = (min(len(w) for w in windows) < min_history
                     or any(statistics.pstdev(w) <= 0 if len(w) >= 2 else True for w in windows))
    cmap = cluster_map or {i: i for i in ids}
    w, diag = apply_constraints(dict(zip(ids, raw)), cmap, constraints)
    return _result(w, {**diag, "allocator": "inverse_vol", "lookback": lookback,
                       "min_history": min_history, "used_fallback": used_fallback,
                       "lookback_calibrated": UNCALIBRATED})


# ---------------------------------------------------------------------------
# 3) 簇风险预算(cluster risk parity)
# ---------------------------------------------------------------------------

def cluster_risk_parity(cluster_map: Mapping[str, str],
                        returns_by_id: Mapping[str, Sequence[float]], end_idx: int,
                        *, lookback: int = 26, min_history: int = 12,
                        constraints: AllocationConstraints = AllocationConstraints()) -> dict:
    """簇风险预算:簇内逆波动 × 簇间逆波动预算(两步风险平价),再投影到簇帽/策略帽。

    诚实注记:W3 簇图当前全为单例簇(|ρ|<0.5),此时簇间逆波动预算 ≈ 逆波动(数学等价,
    仅簇帽投影使其不同)——机制已就位,待真多成员簇出现即生效;不加帽的簇间预算会在
    高波动簇上集中,簇帽是 §9 簇集中度惩罚的硬实现。"""
    ids = sorted(returns_by_id)
    _check_aligned(returns_by_id, end_idx)
    lo = max(0, end_idx - lookback)
    windows = {i: list(returns_by_id[i][lo:end_idx]) for i in ids}
    if min(len(windows[i]) for i in ids) < min_history:
        out = equal_weight(ids, constraints, cluster_map)
        out["diagnostics"].update({"allocator": "cluster_risk_parity", "used_fallback": True,
                                   "fallback_reason": "insufficient_history"})
        return out
    vols = {i: statistics.pstdev(windows[i]) for i in ids}
    if any(v <= 0 for v in vols.values()):
        out = equal_weight(ids, constraints, cluster_map)
        out["diagnostics"].update({"allocator": "cluster_risk_parity", "used_fallback": True,
                                   "fallback_reason": "zero_vol"})
        return out
    clusters = sorted({cluster_map.get(i, i) for i in ids})
    inner: dict = {}
    cluster_series: dict = {}
    for c in clusters:
        members = [i for i in ids if cluster_map.get(i, i) == c]
        inv = [1.0 / vols[i] for i in members]
        tot = sum(inv)
        for i, x in zip(members, inv):
            inner[i] = x / tot
        m = len(windows[members[0]])
        cluster_series[c] = [sum(inner[i] * windows[i][k] for i in members) for k in range(m)]
    cbudget_list = rolling_inverse_vol_weights([cluster_series[c] for c in clusters],
                                               len(cluster_series[clusters[0]]),
                                               lookback, min_history)
    cbudget = dict(zip(clusters, cbudget_list))
    cand = {i: cbudget[cluster_map.get(i, i)] * inner[i] * constraints.gross for i in ids}
    w, diag = apply_constraints(cand, cluster_map, constraints)
    return _result(w, {**diag, "allocator": "cluster_risk_parity", "used_fallback": False,
                       "lookback": lookback, "cluster_budgets": cbudget,
                       "n_clusters": len(clusters), "lookback_calibrated": UNCALIBRATED})


# ---------------------------------------------------------------------------
# 4) 约束最大夏普(均值收缩 + 确定性网格)
# ---------------------------------------------------------------------------

def _simplex_grid(n: int, units: int, cap_units: int) -> np.ndarray:
    """n 维、总量 units 份、每份 ≤ cap_units 的整数 compositions → (m, n) 数组。
    字典序枚举,确定性(ties 由 argmax 取首个 = 字典序最小)。"""
    rows = []

    def rec(k: int, remaining: int, prefix: list) -> None:
        if k == n - 1:
            if remaining <= cap_units:
                rows.append(prefix + [remaining])
            return
        for v in range(min(cap_units, remaining) + 1):
            rec(k + 1, remaining - v, prefix + [v])

    rec(0, units, [])
    return np.asarray(rows, dtype=float)


def constrained_max_sharpe(returns_by_id: Mapping[str, Sequence[float]], end_idx: int,
                           *, lookback: int = 52, min_history: int = 30,
                           mean_shrink: float = 0.5, grid_step: float = 0.02,
                           constraints: AllocationConstraints = AllocationConstraints(),
                           cluster_map: Optional[Mapping[str, str]] = None) -> dict:
    """约束最大夏普:trailing [end_idx-lookback, end_idx) 的均值(向 0 收缩 mean_shrink)与
    协方差,在 单策略帽/簇帽/非负/Σw=gross 约束下最大化 (w·μ̂)/√(w'Σw)。

    求解 = 确定性网格(grid_step 粒度单形枚举 + 可行掩码 + argmax 取字典序首个),无 RNG/无迭代
    初值依赖。均值向 0 收缩:long-only 资本配置下,不确定的边按 0 计 = 保守(不过度配置)。
    历史不足 → 等权回退(标注)。"""
    ids = sorted(returns_by_id)
    _check_aligned(returns_by_id, end_idx)
    lo = max(0, end_idx - lookback)
    windows = {i: list(returns_by_id[i][lo:end_idx]) for i in ids}
    if min(len(windows[i]) for i in ids) < min_history:
        out = equal_weight(ids, constraints, cluster_map)
        out["diagnostics"].update({"allocator": "constrained_max_sharpe", "used_fallback": True,
                                   "fallback_reason": "insufficient_history"})
        return out
    X = np.asarray([windows[i] for i in ids], dtype=float)          # (n, T)
    mu = X.mean(axis=1) * (1.0 - mean_shrink)
    cov = np.cov(X, ddof=1)
    units = int(round(constraints.gross / grid_step))
    cap_units = int(round(constraints.strategy_cap / grid_step))
    grid = _simplex_grid(len(ids), units, cap_units)
    if grid.size == 0:
        out = equal_weight(ids, constraints, cluster_map)
        out["diagnostics"].update({"allocator": "constrained_max_sharpe", "used_fallback": True,
                                   "fallback_reason": "infeasible_grid"})
        return out
    ret = grid @ mu
    var = np.einsum("ij,jk,ik->i", grid, cov, grid)
    score = ret / np.sqrt(np.maximum(var, 0.0) + 1e-12)
    best = grid[int(np.argmax(score))] * grid_step
    cmap = cluster_map or {i: i for i in ids}
    w, diag = apply_constraints(dict(zip(ids, best.tolist())), cmap, constraints)
    return _result(w, {**diag, "allocator": "constrained_max_sharpe", "used_fallback": False,
                       "lookback": lookback, "mean_shrink": mean_shrink,
                       "shrunk_means_weekly": dict(zip(ids, mu.tolist())),
                       "grid_step": grid_step, "n_grid_feasible": int(grid.shape[0]),
                       "params_calibrated": UNCALIBRATED})


# ---------------------------------------------------------------------------
# 5) 换手惩罚(死区部分调整;λ 单调)
# ---------------------------------------------------------------------------

def turnover_penalized(base_weights: Mapping[str, float], prev_weights: Mapping[str, float],
                       lambda_turnover: float) -> dict:
    """对 base 配置器输出做换手死区:S = Σ|base−prev|;S ≤ 2λ → 原样持有 prev;否则
    w = prev + (1 − 2λ/S)·(base − prev)(uniform 部分调整,实现换手 = max(0, S−2λ))。

    性质(测试钉死):① 实现换手关于 λ 精确非增;② Σbase=Σprev ⇒ Σw 不变(无需再归一,
    单调性不被归一化破坏);③ prev/base 都在凸可行域内 ⇒ w 也在(凸组合),约束保持;
    ④ 初始建仓(prev 全零)不罚——入场全额取 base(否则死区会永久留下 2λ 的现金拖尾)。
    λ 单位 = 权重空间死区(如 0.02 = 每周 2% 总调仓免罚区),UNCALIBRATED。"""
    if lambda_turnover < 0:
        raise ValueError("lambda_turnover must be >= 0")
    ids = sorted(base_weights)
    prev = {i: float(prev_weights.get(i, 0.0)) for i in ids}
    base = {i: float(base_weights[i]) for i in ids}
    delta = {i: base[i] - prev[i] for i in ids}
    gross_move = sum(abs(d) for d in delta.values())
    if sum(prev.values()) <= _TOL:                          # 初始建仓:不罚(性质④)
        return _result(base, {"allocator": "turnover_penalized", "lambda_turnover": lambda_turnover,
                              "gross_move_before": gross_move, "realized_turnover": gross_move,
                              "adjustment_fraction": 1.0, "initial_entry": True,
                              "lambda_calibrated": UNCALIBRATED})
    dead = 2.0 * lambda_turnover
    if gross_move <= dead + _TOL:
        w = prev
        beta = 0.0
    else:
        beta = 1.0 - dead / gross_move
        w = {i: prev[i] + beta * delta[i] for i in ids}
    realized = sum(abs(w[i] - prev[i]) for i in ids)
    return _result(w, {"allocator": "turnover_penalized", "lambda_turnover": lambda_turnover,
                       "gross_move_before": gross_move, "realized_turnover": realized,
                       "adjustment_fraction": beta, "lambda_calibrated": UNCALIBRATED})


# ---------------------------------------------------------------------------
# 6) regime 条件化(只调候选权重;低置信 → 精确回退)
# ---------------------------------------------------------------------------

def regime_conditioned(base_weights: Mapping[str, float], stress_score: Optional[float],
                       confidence: float, fallback_weights: Mapping[str, float],
                       *, confidence_threshold: float = 0.6, max_tilt: float = 0.5) -> dict:
    """regime 条件化配置:stress ∈ [0,1] 越高越向 fallback(稳健基线)倾斜:
    τ = clip((stress−0.5)·2, 0, 1)·max_tilt,w = (1−τ)·base + τ·fallback。

    硬性质(测试钉死):confidence < confidence_threshold 或 stress 不可知 → 返回**精确的**
    fallback_weights(逐位相等),绝不掺 base。§9 纪律:regime 模型只调候选权重/风险预算,
    绝不产生无约束指令——输出仍是 base 与 fallback 的凸组合(两者可行 ⇒ 输出可行),
    且 τ 只对 stress>0.5 倾斜(不对称保守:calm 不加注)。"""
    fb = {k: float(v) for k, v in sorted(fallback_weights.items())}
    if stress_score is None or confidence < confidence_threshold:
        return _result(fb, {"allocator": "regime_conditioned", "used_fallback": True,
                            "fallback_reason": ("stress_unknown" if stress_score is None
                                                else "low_confidence"),
                            "confidence": confidence,
                            "confidence_threshold": confidence_threshold,
                            "params_calibrated": UNCALIBRATED})
    tilt = min(max((float(stress_score) - 0.5) * 2.0, 0.0), 1.0) * max_tilt
    w = {i: (1.0 - tilt) * float(base_weights.get(i, 0.0)) + tilt * fb.get(i, 0.0)
         for i in sorted(set(base_weights) | set(fb))}
    return _result(w, {"allocator": "regime_conditioned", "used_fallback": tilt >= 1.0 - _TOL,
                       "tilt": tilt, "stress_score": float(stress_score),
                       "confidence": confidence, "confidence_threshold": confidence_threshold,
                       "max_tilt": max_tilt, "params_calibrated": UNCALIBRATED})


# ---------------------------------------------------------------------------
# regime 输入原语(全 PIT / 截断不变)
# ---------------------------------------------------------------------------

def trailing_zscore(values: Sequence[Optional[float]], end_idx: int,
                    *, lookback: int = 52, min_history: int = 26) -> Optional[float]:
    """values[end_idx−1] 相对 trailing [end_idx−lookback, end_idx) 的 z。样本不足/零方差 → None。
    只用 ≤ end_idx−1 的数据 → 对追加未来数据不变。"""
    lo = max(0, end_idx - lookback)
    window = [v for v in values[lo:end_idx] if v is not None]
    if len(window) < min_history:
        return None
    cur = values[end_idx - 1] if end_idx - 1 >= 0 else None
    if cur is None:
        return None
    sd = statistics.pstdev(window)
    if sd <= 0:
        return None
    return (cur - statistics.fmean(window)) / sd


def z_to_stress(z: Optional[float], *, hi: float = 1.0, lo: float = -1.0) -> Optional[float]:
    """z → stress 等级:z ≥ hi → 1.0(压力);z ≤ lo → 0.0(平静);中间 0.5。None → None。
    阈值 UNCALIBRATED。资金费维语义:高 z = 拥挤多头脆弱 = 压力;低 z = 已出清 = 平静。"""
    if z is None:
        return None
    if z >= hi:
        return 1.0
    if z <= lo:
        return 0.0
    return 0.5


def tercile_to_stress(label: Optional[str]) -> Optional[float]:
    """expanding tercile 标签(PIT, macro_regime.expanding_tercile_labels)→ stress:
    high → 1.0 / mid → 0.5 / low → 0.0(VIX/DVOL 类:高=压力)。None → None。"""
    if label is None:
        return None
    return {"high": 1.0, "mid": 0.5, "low": 0.0}.get(label)


def pit_correlation_shock(returns_by_id: Mapping[str, Sequence[float]], end_idx: int,
                          *, window: int = 26, margin: float = 0.25,
                          min_obs: int = 8) -> Optional[bool]:
    """PIT 相关冲击(W3 信号的截断不变变体):rolling `window` 周 mean|ρ| 在 end_idx−1 的值,
    阈值 = 该 rolling 序列**到 end_idx−2 为止**的扩展均值 + margin(绝对边际,同 W3 口径;
    W3 用全样本均值是回顾性描述,这里必须 PIT)。rolling 观测 < min_obs → None(不可知)。"""
    ids = sorted(returns_by_id)
    series = {i: list(returns_by_id[i][:end_idx]) for i in ids}
    rolling = fd.rolling_mean_abs_corr(series, window)
    cur = rolling[end_idx - 1] if end_idx - 1 >= 0 else None
    if cur is None:
        return None
    hist = [v for v in rolling[:end_idx - 1] if v is not None]
    if len(hist) < min_obs:
        return None
    return cur > statistics.fmean(hist) + margin


def combine_stress(levels: Mapping[str, Optional[float]]) -> dict:
    """逐维 stress 等级(0/0.5/1 或 None)→ {stress_score, confidence, available}。
    stress_score = 可用维均值(全缺 → None);confidence = 可用维数/总维数(机械口径,
    不含强度信息 —— 文档化选择:强度已通过 stress_score 进入 tilt,confidence 只表达
    「证据在不在」)。"""
    avail = {k: v for k, v in levels.items() if v is not None}
    total = len(levels)
    score = statistics.fmean(avail.values()) if avail else None
    return {"stress_score": score, "confidence": (len(avail) / total) if total else 0.0,
            "available": sorted(avail)}


def build_regime_series(returns_by_id: Mapping[str, Sequence[float]],
                        dims: Sequence[tuple], *, corr_window: int = 26,
                        corr_margin: float = 0.25, corr_min_obs: int = 8,
                        z_lookback: int = 52, z_min_history: int = 26,
                        tercile_min_history: int = 10) -> list:
    """逐周决策用 regime 状态(全 PIT):**t 的决策只用 ≤ t−1 已完结周的信息**(shift 内置)。

    dims: [(name, kind, values)] —— kind='tercile'(expanding 三分位,VIX/DVOL 类)或
    'zscore'(trailing z,资金费/已实现波动类);values 与收益同网格(None 允许)。
    相关冲击维由策略收益自身内生构造(PIT 变体,见 pit_correlation_shock)。
    输出[t] = {stress_score, confidence, dims:{name: level}, label}。"""
    ids = sorted(returns_by_id)
    n = len(returns_by_id[ids[0]]) if ids else 0
    prepared = {}
    for name, kind, values in dims:
        if len(values) != n:
            raise ValueError(f"regime dim {name!r} length != strategy grid")
        if kind == "tercile":
            labels = expanding_tercile_labels(list(values), min_history=tercile_min_history)
            prepared[name] = [tercile_to_stress(lb) for lb in labels]
        elif kind == "zscore":
            prepared[name] = [z_to_stress(trailing_zscore(values, k, lookback=z_lookback,
                                                          min_history=z_min_history))
                              for k in range(n + 1)]
            prepared[name] = prepared[name][1:]                    # level at k uses ≤ k−1
        else:
            raise ValueError(f"unknown regime dim kind: {kind!r}")
    shock_levels = []
    for k in range(n):
        # shock_levels[k] = 用 ≤ k 的收益可知的冲击状态(pit_correlation_shock(end_idx=k+1)
        # 读 rolling[k],阈值取 rolling[:k] 扩展均值 + margin)——与 zscore/tercile 维同口径。
        s = pit_correlation_shock(returns_by_id, k + 1, window=corr_window,
                                  margin=corr_margin, min_obs=corr_min_obs)
        shock_levels.append(None if s is None else (1.0 if s else 0.0))
    out = []
    for t in range(n):
        src = t - 1                                                # 决策只用 ≤ t−1
        if src < 0:
            levels = {name: None for name, _, _ in dims}
            levels["correlation_shock"] = None
        else:
            levels = {name: prepared[name][src] for name, _, _ in dims}
            levels["correlation_shock"] = shock_levels[src]
        agg = combine_stress(levels)
        score = agg["stress_score"]
        label = None if score is None else ("stress" if score >= 0.6
                                            else "calm" if score <= 0.4 else "mixed")
        out.append({"stress_score": score, "confidence": agg["confidence"],
                    "label": label, "dims": levels})
    return out


# ---------------------------------------------------------------------------
# 走前评估引擎(纯函数;allocator 可调用对象由驱动器/测试绑定)
# ---------------------------------------------------------------------------

def max_drawdown(returns: Sequence[float]) -> float:
    """窗口内净值(起 1.0)最大回撤(正数)。"""
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in returns:
        eq *= 1.0 + r
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, 1.0 - eq / peak)
    return mdd


def cluster_hhi(weights: Mapping[str, float], cluster_map: Mapping[str, str]) -> float:
    """簇集中度 HHI = Σ_c W_c²(簇权重 = 簇内策略权重和)。"""
    csums: dict = {}
    for i, w in weights.items():
        c = cluster_map.get(i, i)
        csums[c] = csums.get(c, 0.0) + w
    return sum(v * v for v in csums.values())


def window_metrics(path: Sequence[dict], indices: Sequence[int], *, ppy: int = _PERIODS_PER_YEAR) -> dict:
    """path(evaluate_walk_forward 的逐周记录)在 indices 上的指标:净/毛夏普(年化)、净总收益、
    窗口内最大回撤、换手(周均/年化)、成本合计、簇 HHI 均值、回退率。空窗口 → 全 None 诚实。"""
    pts = [path[t] for t in indices]
    if not pts:
        return {"n_weeks": 0, "net_sharpe": None, "gross_sharpe": None, "net_total_return": None,
                "max_drawdown": None, "mean_weekly_turnover": None, "ann_turnover": None,
                "total_cost": None, "cluster_hhi_mean": None, "fallback_rate": None}
    net = [p["net"] for p in pts]
    gross = [p["gross"] for p in pts]
    cum = 1.0
    for r in net:
        cum *= 1.0 + r
    return {"n_weeks": len(pts),
            "net_sharpe": fd.ann_sharpe(net),
            "gross_sharpe": fd.ann_sharpe(gross),
            "net_total_return": cum - 1.0,
            "max_drawdown": max_drawdown(net),
            "mean_weekly_turnover": statistics.fmean(p["turnover"] for p in pts),
            "ann_turnover": statistics.fmean(p["turnover"] for p in pts) * ppy,
            "total_cost": sum(p["cost"] for p in pts),
            "cluster_hhi_mean": statistics.fmean(p["hhi"] for p in pts),
            "fallback_rate": sum(1 for p in pts if p["used_fallback"]) / len(pts)}


AllocatorFn = Callable[[int, Mapping[str, float]], tuple]


def evaluate_walk_forward(returns_by_id: Mapping[str, Sequence[float]], weeks: Sequence[str],
                          allocators: Mapping[str, AllocatorFn], *, lock_week: str,
                          start_idx: int, cost_bps: float = 10.0,
                          cluster_map: Optional[Mapping[str, str]] = None,
                          constraints: AllocationConstraints = AllocationConstraints()) -> dict:
    """走前评估:逐周 t ∈ [start_idx, n) 调 allocators[name](t, prev_weights) → (weights, diag),
    组合毛收益 = Σ w·r(第 t 周),成本 = cost_bps/1e4 × Σ|w − prev|,净 = 毛 − 成本。

    PIT 纪律由 allocator fn 保证(trailing-only);引擎**承重校验**输出可行性(非负/超 gross/
    超帽 → ValueError fail-closed,配置器违规绝不静默通过)。指标分 locked(≥ lock_week)与
    fit(< lock_week)两窗;权重路径连续穿过边界(锁定窗首周成本用真实上周权重,无虚构初始成本)。
    """
    ids = sorted(returns_by_id)
    n = len(weeks)
    _check_aligned(returns_by_id, n)
    cmap = cluster_map or {i: i for i in ids}
    rate = cost_bps / 1e4
    locked_idx = [t for t in range(start_idx, n) if weeks[t] >= lock_week]
    fit_idx = [t for t in range(start_idx, n) if weeks[t] < lock_week]
    out = {}
    for name in sorted(allocators):
        fn = allocators[name]
        prev = {i: 0.0 for i in ids}
        path: list = []
        for t in range(start_idx, n):
            w, diag = fn(t, prev)
            _assert_feasible(name, t, w, cmap, constraints)
            gross_ret = sum(w.get(i, 0.0) * returns_by_id[i][t] for i in ids)
            turnover = sum(abs(w.get(i, 0.0) - prev.get(i, 0.0)) for i in ids)
            cost = rate * turnover
            path.append({"t": t, "week": weeks[t], "weights": dict(sorted(w.items())),
                         "gross": gross_ret, "cost": cost, "net": gross_ret - cost,
                         "turnover": turnover, "hhi": cluster_hhi(w, cmap),
                         "used_fallback": bool(diag.get("used_fallback"))})
            prev = dict(w)
        off = start_idx
        rel_locked = [t - off for t in locked_idx]
        rel_fit = [t - off for t in fit_idx]
        out[name] = {"path": path, "locked": window_metrics(path, rel_locked),
                     "fit": window_metrics(path, rel_fit),
                     "locked_weeks": [weeks[t] for t in locked_idx]}
    return out


def apply_vol_target(path: Sequence[dict], *, target_ann: float = 0.10, lookback: int = 26,
                     lev_cap: float = 2.0, min_history: int = 26, cost_bps: float = 10.0,
                     ppy: int = _PERIODS_PER_YEAR) -> list:
    """vol-target 后处理(纯函数):scale_t = target_weekly / trailing_vol(path 毛收益, ≤ t−1),
    钳 [0, lev_cap];w'_t = scale·w_t;成本按**缩放后**权重差计。历史不足 → scale=1(不缩放,
    诚实标注 diagnostics 由驱动器记)。杠杆诊断口径(gross 可 >1),与 unlevered 主矩阵分开报告。"""
    rate = cost_bps / 1e4
    target_weekly = target_ann / math.sqrt(ppy)
    out = []
    prev: dict = {}
    gross_hist: list = []
    for k, p in enumerate(path):
        w = p["weights"]
        if len(gross_hist) >= min_history:
            window = gross_hist[max(0, len(gross_hist) - lookback):]
            vol = statistics.pstdev(window)
            scale = min(lev_cap, target_weekly / vol) if vol > 0 else lev_cap
        else:
            scale = 1.0
        sw = {i: v * scale for i, v in w.items()}
        turnover = sum(abs(sw.get(i, 0.0) - prev.get(i, 0.0)) for i in set(sw) | set(prev))
        cost = rate * turnover
        out.append({**p, "weights": sw, "gross": p["gross"] * scale, "cost": cost,
                    "net": p["gross"] * scale - cost, "turnover": turnover,
                    "vol_target_scale": scale})
        prev = sw
        gross_hist.append(p["gross"])
    return out


def verdict(locked_by_allocator: Mapping[str, dict], *, ew_name: str = "equal_weight",
            sharpe_margin: float = 0.1, maxdd_mult: float = 1.5) -> dict:
    """预注册判定(exit_lab_selection 同法:规则先于结果写死):最强非等权配置器在锁定窗净夏普
    超等权 > sharpe_margin 且 最大回撤 ≤ maxdd_mult × 等权回撤 → ADOPTED-AS-RESEARCH-FINDING;
    否则 EW REMAINS(诚实)。"""
    ew = locked_by_allocator[ew_name]
    cands = [(m["net_sharpe"], name, m) for name, m in locked_by_allocator.items()
             if name != ew_name and m.get("net_sharpe") is not None]
    if not cands or ew.get("net_sharpe") is None:
        return {"verdict": "INSUFFICIENT_DATA", "rule": _verdict_rule(sharpe_margin, maxdd_mult)}
    cands.sort(key=lambda x: (-x[0], x[1]))
    best_sharpe, best_name, best = cands[0]
    delta = best_sharpe - ew["net_sharpe"]
    dd_ok = (best["max_drawdown"] is not None and ew["max_drawdown"] is not None
             and best["max_drawdown"] <= maxdd_mult * ew["max_drawdown"])
    adopted = delta > sharpe_margin and dd_ok
    return {"verdict": "ADOPTED-AS-RESEARCH-FINDING" if adopted else "EW_REMAINS",
            "best_non_ew": best_name, "best_net_sharpe": best_sharpe,
            "ew_net_sharpe": ew["net_sharpe"], "delta_sharpe": delta,
            "sharpe_margin": sharpe_margin, "best_maxdd": best["max_drawdown"],
            "ew_maxdd": ew["max_drawdown"], "maxdd_constraint_ok": dd_ok,
            "rule": _verdict_rule(sharpe_margin, maxdd_mult)}


def _verdict_rule(sharpe_margin: float, maxdd_mult: float) -> str:
    return (f"best non-EW net Sharpe > EW + {sharpe_margin} on LOCKED window AND "
            f"maxDD <= {maxdd_mult}x EW maxDD -> ADOPTED-AS-RESEARCH-FINDING; else EW_REMAINS")


# ---------------------------------------------------------------------------
# 内部校验
# ---------------------------------------------------------------------------

def _check_aligned(returns_by_id: Mapping[str, Sequence[float]], n: int) -> None:
    ids = sorted(returns_by_id)
    if not ids:
        raise ValueError("no strategies")
    for i in ids:
        if len(returns_by_id[i]) < n:
            raise ValueError(f"series {i!r} shorter than required grid ({n})")


def _assert_feasible(name: str, t: int, w: Mapping[str, float],
                     cluster_map: Mapping[str, str], constraints: AllocationConstraints) -> None:
    for i, v in w.items():
        if not math.isfinite(v) or v < -1e-9:
            raise ValueError(f"allocator {name!r} week {t}: invalid weight {i}={v}")
        if v > constraints.strategy_cap + 1e-6:
            raise ValueError(f"allocator {name!r} week {t}: strategy cap violated {i}={v}")
    if sum(w.values()) > constraints.gross + 1e-6:
        raise ValueError(f"allocator {name!r} week {t}: gross violated {sum(w.values())}")
    csums: dict = {}
    for i, v in w.items():
        c = cluster_map.get(i, i)
        csums[c] = csums.get(c, 0.0) + v
    for c, s in csums.items():
        if s > constraints.cluster_cap + 1e-6:
            raise ValueError(f"allocator {name!r} week {t}: cluster cap violated {c}={s}")
