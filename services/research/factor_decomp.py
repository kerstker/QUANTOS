"""策略公共风险分解(W3,spec §7)—— 识别「名字不同、底层敞口相同」的策略,防止假分散。

纯函数、确定性、无 IO/RNG/墙钟/网络。numpy 确定性用法同 services/research/ml 惯例
(线程在 import numpy 前钉死为 1;OLS 走 LAPACK lstsq 闭式解,无 RNG)。

因子库(§7.1,全部由既有本地数据构造 —— 数据组装在 tools/common_risk_research.py,
本模块只做纯数学;每个因子一个纯函数,输入为 {week: value} 或 {coin: {week: value}}):

  btc_market / eth_market     周收益(周末收盘价环比;crypto_funding.db 的 close=现货 klines)
  market_trend_4w             BTC 4 周动量 c_t/c_{t-4}-1(时序趋势因子)
  short_term_reversal_1w      −1 × BTC 前一周收益(短期反转因子,f_t = −r_{t-1})
  realized_vol_change         BTC 周度已实现波动(周内日收益 pstdev×√365)的周环比 Δ(波动创新)
  liquidity_volume_change     主流币美元成交量总额周环比 %(流动性代理;crypto_funding.db 的
                              volume 是 base 单位 → 美元量 = close×volume,与
                              capacity_research._carry_panel 同口径;注意 crypto_daily.db 的
                              CoinGecko volume 已是 USD 名义,两个库口径不同,不可混用)
  size_oi_change              主流币 OI 名义(oi_value)总额周环比 %(规模代理)
  funding_weekly              主流币周累计资金费的截面均值(永续空头侧现金流;正费率=空头收)
  basis_change                主流币基差截面均值的周环比 Δ;perp_mark = oi_value/oi(OI 名义
                              ÷ OI 张数),basis = perp/spot − 1,同 crypto_oi_research.build_panel
                              的 add_basis 口径
  oi_crowding_change          主流币 global_ls 截面均值的周环比 Δ(拥挤度创新)

MISSING(本地无数据,诚实标注,由驱动器写进证据):
  清算/强平瀑布因子(无 liquidation 数据)、Amihud 非流动性(以成交量变化代理)、
  options/skew 因子(Deribit skew 快照 2026-07-24 起向前积累,历史太短)、链上因子、
  跨资产因子(股票/黄金;本环境股票免费源被墙)。

分解(§7.2):per 策略 OLS(截距 + k 因子,numpy lstsq)→ ResidualReturn = r − Xβ;
残差均值被截距吸收恒≈0,故「carry 的边到底是资金费还是伪装的 beta」用两个严格量回答:
alpha = 截距(附 OLS t 统计)+ mean_attribution(mean(y) = alpha + Σ β_j·mean(X_j) 的逐项归因)。

聚类(§7.2):`agglomerative_cluster` 复用 services/alpha/cluster.py 的 complete-linkage
凝聚聚类(|ρ| ≥ rho_cut 才合并,反链式;ties 按排序 id 确定)——输入残差收益序列,
即「在残差相关矩阵上聚类」。同时给原始收益聚类作对照(假分散检查:原始不相关 ≠ 残差不相关)。

effective_breadth = 1 / Σ_c W_c²,W_c = 簇内策略(逆波动)权重和(§7.2,权重口径在驱动器文档化)。

相关冲击(§7.3):26 周滚动 mean|ρ| > 全样本 mean|ρ| + 0.25(UNCALIBRATED 工程默认,
绝对边际而非倍数——残差的全样本 mean|ρ| 可接近 0,倍数会爆炸)。
"""
from __future__ import annotations

import os as _os

# 线程必须在 numpy 初始化 OpenMP/BLAS 之前钉死,确保 lstsq 确定性(同 services/research/ml 惯例)。
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    _os.environ.setdefault(_v, "1")

import math
import statistics
import time
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from quantos.contracts.alpha import AlphaCluster
# 2026-09-20 审计 P1-9:聚类原语已下沉为中立的 quantos.analytics.cluster,
# research 层不再需要从 alpha 包导入它(消除了基线里的一条 research→alpha 违规)。
from quantos.analytics.cluster import cluster_alphas

_PERIODS_PER_YEAR = 52          # 策略收益为 ISO 周频
_TRADING_DAYS = 365             # crypto 7×24,已实现波动年化基数


# ---------------------------------------------------------------------------
# ISO 周对齐(镜像 services/research/book_combine._iso_week / align_on_common_weeks 的 N 路推广)
# ---------------------------------------------------------------------------

def iso_week(ms: int) -> str:
    """Unix ms → ISO 年-周(与 book_combine._iso_week 同一约定,UTC)。"""
    return time.strftime("%G-W%V", time.gmtime(ms / 1000))


def align_weekly(series: dict) -> dict:
    """N 路周对齐:{sid: (returns, ts_ms)} → {weeks, data:{sid:[...]}}。
    同周多值取首个(同 book_combine.align_on_common_weeks);仅保留所有序列都有的周,按周排序。"""
    per_sid = {}
    week_sets = []
    for sid in sorted(series):
        rets, ts = series[sid]
        m = {}
        for r, t in zip(rets, ts):
            w = iso_week(t)
            if w not in m:
                m[w] = r
        per_sid[sid] = m
        week_sets.append(set(m))
    if not week_sets:
        return {"weeks": [], "data": {}}
    weeks = sorted(set.intersection(*week_sets))
    return {"weeks": weeks, "data": {sid: [per_sid[sid][w] for w in weeks] for sid in per_sid}}


# ---------------------------------------------------------------------------
# 因子构造原语(全部纯函数;{week: float} 周映射进,{week: float} 出)
# ---------------------------------------------------------------------------

def weekly_returns(closes_by_week: dict, weeks: Sequence[str]) -> dict:
    """周末收盘序列 → 周收益 {week: r};仅当相邻两个 master 周都有收盘才定义(缺周不跨周补)。"""
    out = {}
    for i in range(1, len(weeks)):
        c0, c1 = closes_by_week.get(weeks[i - 1]), closes_by_week.get(weeks[i])
        if c0 is not None and c1 is not None and c0 > 0:
            out[weeks[i]] = c1 / c0 - 1.0
    return out


def trailing_return(closes_by_week: dict, weeks: Sequence[str], lookback: int) -> dict:
    """时序趋势因子:{week: c_t/c_{t-lookback} − 1};lookback 个相邻 master 周都在场才定义。"""
    out = {}
    for i in range(lookback, len(weeks)):
        c0, c1 = closes_by_week.get(weeks[i - lookback]), closes_by_week.get(weeks[i])
        if c0 is not None and c1 is not None and c0 > 0:
            out[weeks[i]] = c1 / c0 - 1.0
    return out


def reversal_lag(closes_by_week: dict, weeks: Sequence[str]) -> dict:
    """短期反转因子:f_t = −r_{t−1}(负的前一周收益)。需要 t−1 与 t−2 两周收盘。"""
    rets = weekly_returns(closes_by_week, weeks)
    out = {}
    for i in range(1, len(weeks)):
        prev = rets.get(weeks[i - 1])
        if prev is not None:
            out[weeks[i]] = -prev
    return out


def pct_change_map(values_by_week: dict, weeks: Sequence[str]) -> dict:
    """任意正水平序列的周环比 %(成交量/OI 规模类因子);相邻两周都在场且前值 > 0 才定义。"""
    out = {}
    for i in range(1, len(weeks)):
        v0, v1 = values_by_week.get(weeks[i - 1]), values_by_week.get(weeks[i])
        if v0 is not None and v1 is not None and v0 > 0:
            out[weeks[i]] = v1 / v0 - 1.0
    return out


def diff_map(values_by_week: dict, weeks: Sequence[str]) -> dict:
    """任意水平序列的周环比 Δ(基差/拥挤度/已实现波动类因子);相邻两周都在场才定义。"""
    out = {}
    for i in range(1, len(weeks)):
        v0, v1 = values_by_week.get(weeks[i - 1]), values_by_week.get(weeks[i])
        if v0 is not None and v1 is not None:
            out[weeks[i]] = v1 - v0
    return out


def annualized_vol(daily_returns: Sequence[float], *, days_per_year: int = _TRADING_DAYS) -> Optional[float]:
    """周内日收益序列 → 年化已实现波动(pstdev×√365);n<2 → None。"""
    vals = [r for r in daily_returns if r is not None and math.isfinite(r)]
    if len(vals) < 2:
        return None
    return statistics.pstdev(vals) * math.sqrt(days_per_year)


def cross_sectional_mean(per_coin: dict, weeks: Sequence[str]) -> dict:
    """{coin: {week: x}} → {week: 截面均值}(在场的币取均值;全缺席 → 该周缺)。"""
    out = {}
    for w in weeks:
        vals = [m[w] for m in per_coin.values() if w in m and m[w] is not None and math.isfinite(m[w])]
        if vals:
            out[w] = statistics.fmean(vals)
    return out


def cross_sectional_sum(per_coin: dict, weeks: Sequence[str]) -> dict:
    """{coin: {week: x}} → {week: 截面和}(在场的币求和;全缺席 → 该周缺)。"""
    out = {}
    for w in weeks:
        vals = [m[w] for m in per_coin.values() if w in m and m[w] is not None and math.isfinite(m[w])]
        if vals:
            out[w] = sum(vals)
    return out


# ---------------------------------------------------------------------------
# 因子模型(§7.2):OLS(截距 + k 因子),确定性 lstsq
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FactorModel:
    """已拟合的线性因子模型。betas 与 factor_names 对齐(不含截距);tstats = [截距_t, β1_t, …]。
    残差均值被截距吸收恒≈0(by construction)——「边是否被因子解释」必须看截距(alpha)的
    t 统计与 mean_attribution,不能看残差均值。"""
    factor_names: tuple
    intercept: float
    betas: tuple
    tstats: tuple
    r2: Optional[float]          # y 零方差 → None
    n: int
    residual: tuple              # y − Xβ(含截距)
    fitted: tuple


def _check_finite(name: str, vals: Sequence[float]) -> None:
    if any(not math.isfinite(v) for v in vals):
        raise ValueError(f"{name} contains non-finite values (fail-closed: no silent NaN propagation)")


def fit_factor_model(y: Sequence[float], X: Sequence[Sequence[float]],
                     factor_names: Sequence[str]) -> FactorModel:
    """y ≈ intercept + Σ β_j X_j,OLS(numpy.linalg.lstsq 闭式,min-norm 处理秩亏)。

    系数 t 统计用常规 OLS 标准误:σ²=ss_res/(n−k−1),se_j=√(σ²·(XᵀX)⁺_jj)(SVD 伪逆,与
    lstsq 同族确定性)。iid 假设,无 Newey-West —— 小样本诚实注脚由调用方写。

    fail-closed:n ≤ k+1(自由度不够)→ ValueError;任何非有限输入 → ValueError。
    全样本 in-sample 拟合 = 回顾性敞口描述,不是 PIT 可交易择时(调用方须标注)。"""
    n = len(y)
    k = len(factor_names)
    if n == 0 or any(len(row) != n for row in X) or len(X) != k:
        raise ValueError("y/X/factor_names shape mismatch")
    if n <= k + 1:
        raise ValueError(f"need n > k+1 observations for OLS (n={n}, k={k})")
    _check_finite("y", y)
    for nm, col in zip(factor_names, X):
        _check_finite(f"factor {nm}", col)

    a = np.column_stack([np.ones(n)] + [np.asarray(col, dtype=float) for col in X])
    b = np.asarray(list(y), dtype=float)
    coef, _res, _rank, _sv = np.linalg.lstsq(a, b, rcond=None)
    fitted = a @ coef
    residual = b - fitted

    ss_res = float(residual @ residual)
    ybar = float(b.mean())
    ss_tot = float(((b - ybar) ** 2).sum())
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else None

    dof = n - k - 1
    sigma2 = ss_res / dof
    xtx_inv = np.linalg.pinv(a.T @ a)
    se = np.sqrt(np.maximum(np.diag(xtx_inv) * sigma2, 0.0))
    tstats = tuple(float(c / s) if s > 0 else None for c, s in zip(coef, se))

    return FactorModel(factor_names=tuple(factor_names), intercept=float(coef[0]),
                       betas=tuple(float(c) for c in coef[1:]), tstats=tstats, r2=r2, n=n,
                       residual=tuple(float(v) for v in residual),
                       fitted=tuple(float(v) for v in fitted))


def standardized_betas(model: FactorModel, X: Sequence[Sequence[float]],
                       y: Sequence[float]) -> dict:
    """标准化 beta_j = β_j·sd(X_j)/sd(y) —— 跨因子可比的敞口(因子量纲不同,裸 beta 不可比)。
    sd 为 pstdev;y 或因子零方差 → 该因子 None。"""
    sd_y = statistics.pstdev(y) if len(y) > 1 else 0.0
    out = {}
    for nm, b, col in zip(model.factor_names, model.betas, X):
        sd_x = statistics.pstdev(col) if len(col) > 1 else 0.0
        out[nm] = (b * sd_x / sd_y) if sd_y > 0 and sd_x > 0 else None
    return out


def mean_attribution(model: FactorModel, X: Sequence[Sequence[float]],
                     y: Sequence[float]) -> dict:
    """均值归因:mean(y) = alpha + Σ_j β_j·mean(X_j) —— 直接回答「这条边是资金费还是伪装的
    beta」:各因子对**均值收益**的贡献 vs 不可解释 alpha(=截距)。周频口径。"""
    contrib = {nm: b * statistics.fmean(col) for nm, b, col in
               zip(model.factor_names, model.betas, X)}
    return {"alpha": model.intercept, "factors": contrib,
            "raw_mean": statistics.fmean(y) if len(y) else None}


def mean_tstat(series: Sequence[float]) -> dict:
    """iid 均值 t 统计(无 Newey-White 修正 —— 小样本诚实注脚由调用方写)。
    返回 {n, mean_weekly, annualized, tstat};n<2 或零方差 → tstat None。"""
    n = len(series)
    if n == 0:
        return {"n": 0, "mean_weekly": None, "annualized": None, "tstat": None}
    mu = statistics.fmean(series)
    sd = statistics.pstdev(series) if n > 1 else 0.0
    t = (mu / (sd / math.sqrt(n))) if n > 1 and sd > 0 else None
    return {"n": n, "mean_weekly": mu, "annualized": mu * _PERIODS_PER_YEAR, "tstat": t}


def ann_sharpe(series: Sequence[float]) -> Optional[float]:
    """周频年化夏普(pstdev 口径,与 book_combine 工具一致);n<2 或零方差 → None。"""
    if len(series) < 2:
        return None
    sd = statistics.pstdev(series)
    if sd <= 0:
        return None
    return statistics.fmean(series) / sd * math.sqrt(_PERIODS_PER_YEAR)


# ---------------------------------------------------------------------------
# 相关矩阵 + 聚类(§7.2/§7.3)
# ---------------------------------------------------------------------------

def pearson(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Pearson 相关(镜像 services/alpha/correlation._pearson 与 vol_factors._pearson 的既有模式);
    n<2 或任一侧零方差 → None(fail-closed,不编造)。"""
    n = len(a)
    if n < 2 or len(b) != n:
        return None
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else None


def correlation_matrix(series: dict) -> dict:
    """{sid: returns} → {ids(排序), matrix(对称,对角 1.0;零方差对 → None)}。"""
    ids = sorted(series)
    mat = []
    for i, a in enumerate(ids):
        row = []
        for j, b in enumerate(ids):
            if i == j:
                row.append(1.0)
            elif j < i:
                row.append(mat[j][i])
            else:
                row.append(pearson(series[a], series[b]))
        mat.append(row)
    return {"ids": ids, "matrix": mat}


def agglomerative_cluster(series: dict, *, rho_cut: float = 0.5, min_overlap: int = 30) -> list:
    """在（残差）收益序列上做 complete-linkage 凝聚聚类 —— 复用 services/alpha/cluster.py
    ::cluster_alphas(经过测试的既有实现:|ρ|≥rho_cut 才合并、min-cross-pair 反链式、
    candidates 按 id 排序、ties 取严格更大 link、cluster_id=stable_hash(sorted members))。
    输入序列等长时,其内部 Pearson 与本模块 correlation_matrix 逐值相同(测试钉死)。
    rho_cut=0.5 为 UNCALIBRATED 工程默认(|ρ|≥0.5 才视为同一底层敞口),驱动器给敏感性。"""
    if not series:
        return []
    return cluster_alphas({sid: tuple(rets) for sid, rets in series.items()},
                          rho_cut=rho_cut, min_overlap=min_overlap)


def effective_breadth(weights: dict, clusters: Sequence) -> dict:
    """effective_breadth = 1 / Σ_c W_c²,W_c = 簇内策略权重和(§7.2)。

    口径:策略权重为(逆波动)风险权重、和=1;簇内策略视为同一个 bet(完全冗余),
    簇间相关≈0 → 1/ΣW_c² 即「有效独立赌注数」(ENB 风格近似,文档化口径)。
    clusters 为 member-id 序列的序列(AlphaCluster.member_ids 或 plain list)。"""
    cw = []
    for members in clusters:
        ids = members.member_ids if isinstance(members, AlphaCluster) else tuple(members)
        cw.append(sum(weights.get(m, 0.0) for m in ids))
    breadth = 1.0 / sum(w * w for w in cw) if cw and sum(w * w for w in cw) > 0 else None
    return {"cluster_weights": cw, "effective_breadth": breadth}


# ---------------------------------------------------------------------------
# 滚动相关冲击(§7.3)
# ---------------------------------------------------------------------------

def rolling_mean_abs_corr(series: dict, window: int) -> list:
    """逐 end-index 的 trailing `window` 期 mean |pairwise ρ|(全体两两对;某对窗内零方差则跳过该对,
    该窗无任何可对 → None)。返回长度 = len(任一序列),前 window−1 个为 None。"""
    ids = sorted(series)
    n = len(series[ids[0]]) if ids else 0
    out = [None] * n
    if len(ids) < 2 or window < 2 or n < window:
        return out
    for end in range(window - 1, n):
        lo = end - window + 1
        vals = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                c = pearson(series[ids[i]][lo:end + 1], series[ids[j]][lo:end + 1])
                if c is not None:
                    vals.append(abs(c))
        out[end] = statistics.fmean(vals) if vals else None
    return out


def correlation_shock(series: dict, weeks: Sequence[str], *, window: int = 26,
                      margin: float = 0.25) -> dict:
    """相关冲击信号(§7.3):rolling mean|ρ| > 全样本 mean|ρ| + margin。

    默认 window=26 周(半年)、margin=+0.25 绝对边际 —— UNCALIBRATED 工程默认(证据里标注)。
    用绝对边际而非倍数:残差相关的全样本均值可接近 0,倍数阈值会无意义爆炸。
    返回滚动序列(带周标签)、阈值、当前/历史冲击状态;样本不足 window → status=INSUFFICIENT_DATA。"""
    ids = sorted(series)
    n = len(series[ids[0]]) if ids else 0
    full_vals = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            c = pearson(series[ids[i]], series[ids[j]])
            if c is not None:
                full_vals.append(abs(c))
    full_mean = statistics.fmean(full_vals) if full_vals else None
    rolling = rolling_mean_abs_corr(series, window)
    threshold = (full_mean + margin) if full_mean is not None else None
    points = [{"week": weeks[k], "mean_abs_corr": rolling[k]}
              for k in range(n) if rolling[k] is not None]
    shocked = [p["week"] for p in points if threshold is not None and p["mean_abs_corr"] > threshold]
    current = points[-1] if points else None
    return {"window": window, "margin": margin, "threshold_calibrated": "UNCALIBRATED_ENGINEERING_DEFAULT",
            "n_pairs": len(full_vals), "full_sample_mean_abs_corr": full_mean,
            "threshold": threshold, "rolling": points,
            "max_rolling": max((p["mean_abs_corr"] for p in points), default=None),
            "current": current,
            "in_shock_now": bool(current and threshold is not None
                                 and current["mean_abs_corr"] > threshold),
            "n_shock_weeks": len(shocked), "shock_weeks": shocked,
            "status": "OK" if points else "INSUFFICIENT_DATA"}


# ---------------------------------------------------------------------------
# 一键分解(纯函数;驱动器/测试共用同一入口)
# ---------------------------------------------------------------------------

def decompose_strategies(returns_by_sid: dict, factors: dict, *,
                         weeks: Optional[Sequence[str]] = None,
                         rho_cut: float = 0.5, shock_window: int = 26,
                         shock_margin: float = 0.25, min_overlap: int = 30) -> dict:
    """对一组等长周收益序列做 §7 全分解:因子模型 + 残差 + 原始/残差相关矩阵 + 双口径聚类
    + 相关冲击。returns_by_sid / factors 的所有序列必须等长、全有限(fail-closed ValueError)。
    weeks 可选的真周标签(等长),用于相关冲击输出的可读标签;缺省用占位 w0..w{n-1}。

    返回纯数据(dict),不含任何 IO/ provenance —— 证据元数据由驱动器追加。"""
    ids = sorted(returns_by_sid)
    if not ids:
        raise ValueError("no strategies")
    n = len(returns_by_sid[ids[0]])
    if any(len(returns_by_sid[s]) != n for s in ids):
        raise ValueError("strategy series must be equal-length (align on common weeks first)")
    fnames = sorted(factors)
    if any(len(factors[f]) != n for f in fnames):
        raise ValueError("factor series must match strategy length (same common-week grid)")
    for s in ids:
        _check_finite(f"strategy {s}", returns_by_sid[s])
    for f in fnames:
        _check_finite(f"factor {f}", factors[f])

    X = [list(factors[f]) for f in fnames]
    per_strategy = {}
    residuals = {}
    for s in ids:
        y = list(returns_by_sid[s])
        model = fit_factor_model(y, X, fnames)
        residuals[s] = list(model.residual)
        sd_y = statistics.pstdev(y) if n > 1 else 0.0
        sd_res = statistics.pstdev(residuals[s]) if n > 1 else 0.0
        per_strategy[s] = {
            "n": model.n, "r2": model.r2,
            # 残差均值恒≈0(截距吸收):「边」看 alpha(截距)及其 t,不看残差均值
            "alpha_weekly": model.intercept,
            "alpha_annualized": model.intercept * _PERIODS_PER_YEAR,
            "alpha_tstat": model.tstats[0],
            "betas": dict(zip(model.factor_names, model.betas)),
            "beta_tstats": dict(zip(model.factor_names, model.tstats[1:])),
            "standardized_betas": standardized_betas(model, X, y),
            "mean_attribution": mean_attribution(model, X, y),
            "raw": mean_tstat(y),
            "raw_ann_sharpe": ann_sharpe(y),
            "residual_ann_vol": sd_res * math.sqrt(_PERIODS_PER_YEAR),
            "residual_vol_ratio": (sd_res / sd_y) if sd_y > 0 else None,
        }

    if weeks is None:
        week_labels = [f"w{i}" for i in range(n)]
    else:
        if len(weeks) != n:
            raise ValueError("weeks labels must match series length")
        week_labels = list(weeks)
    return {"n_weeks": n, "factor_names": fnames, "strategies": per_strategy,
            "raw_corr": correlation_matrix(returns_by_sid),
            "residual_corr": correlation_matrix(residuals),
            "clusters_raw": agglomerative_cluster(returns_by_sid, rho_cut=rho_cut,
                                                  min_overlap=min_overlap),
            "clusters_residual": agglomerative_cluster(residuals, rho_cut=rho_cut,
                                                       min_overlap=min_overlap),
            "correlation_shock_raw": correlation_shock(returns_by_sid, week_labels,
                                                       window=shock_window, margin=shock_margin),
            "correlation_shock_residual": correlation_shock(residuals, week_labels,
                                                            window=shock_window, margin=shock_margin),
            "residuals": residuals}
