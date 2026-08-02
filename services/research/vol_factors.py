"""波动率/衍生品因子核心(第四数据类:options/vol)—— 纯函数,无 IO、无 RNG、无墙钟、无网络。

数据源是 Deribit 公共接口(tools/deribit_fetch.py 抓取落库;网络只在门外)。本模块只做数学:

- DVOL(Deribit 波动率指数)因子:`dvol_level` / `dvol_change` / `iv_rv_spread` / `zscore`。
  **单位纪律:DVOL 是年化波动率的百分数(54.21 = 54.21%,不是 0.5421)**;realized vol 用
  日对数收益的截面标准差 × sqrt(365) 年化(crypto 365 天交易),同样以百分数表示,二者相减
  才是同单位的 IV-RV 价差。
- 期限结构:`annualized_basis` / `term_structure_slope`(perp vs 到期期货,日历天年化 365)。
- 期权快照:`bs_delta`(Black-76,以同到期期货 mark 价为 forward,免利率)+ `skew_25d`
  (25Δ risk-reversal = IV(25Δ put) − IV(25Δ call),取 delta 最接近 ±0.25 的行,不做插值——
  诚实:这是 nearest-listed-strike 近似,非连续 skew)。
- `ts_ic_stats`:时序因子筛选统计(Pearson/Spearman/t-stat/分年),n<10 全 None,
  与 services/research/ic_panel.py 的小样本诚实口径一致。

所有序列函数对缺口诚实:输入 None/NaN 传播为 None,不补值、不用陈旧值。滚动窗口只用
过去+当前(截断不变,PIT 安全)。年化常数 365(crypto 无节假日)。
"""
from __future__ import annotations

import math
import statistics
import time
from typing import Optional, Sequence

DAYS_PER_YEAR = 365            # crypto 连续交易,年化用 365(非 252)
_MIN_N = 10                    # 与 ic_panel._MIN_PERIODS_FOR_STATS 一致的小样本门槛


def _finite(value) -> Optional[float]:
    """float 化 + 有限性守卫;None/NaN/Inf/非数值 → None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def dvol_level(dvol: Sequence) -> list:
    """DVOL 百分数 -> 小数(54.21 -> 0.5421)。因子即水平值本身;此函数只做单位纪律转换。
    None/NaN → None。"""
    out = []
    for v in dvol:
        f = _finite(v)
        out.append(f / 100.0 if f is not None else None)
    return out


def dvol_change(dvol: Sequence, n: int) -> list:
    """n 日 DVOL 变化(波动率点,百分数单位:dvol[t] - dvol[t-n])。t<n 或任一端缺失 → None。
    用差不用比值:vol 变化的习惯口径是 vol points。"""
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    vals = [_finite(v) for v in dvol]
    out = [None] * len(vals)
    for t in range(n, len(vals)):
        if vals[t] is not None and vals[t - n] is not None:
            out[t] = vals[t] - vals[t - n]
    return out


def realized_vol(closes: Sequence, window: int, *, days_per_year: int = DAYS_PER_YEAR) -> list:
    """尾随 window 日实现波动率(百分数):日对数收益的 pstdev × sqrt(365) × 100。
    rv[t] 用 closes[t-window..t] 共 window+1 个收盘价 -> window 个收益(只用过去+当前)。
    有效收益 < window(缺口或暖机)→ None(不硬凑短窗)。"""
    if window < 2:
        raise ValueError(f"window must be >= 2, got {window}")
    px = [_finite(c) for c in closes]
    out = [None] * len(px)
    for t in range(window, len(px)):
        rets = []
        ok = True
        for k in range(t - window, t):
            p0, p1 = px[k], px[k + 1]
            if p0 is None or p1 is None or p0 <= 0 or p1 <= 0:
                ok = False
                break
            rets.append(math.log(p1 / p0))
        if not ok:
            continue
        sd = statistics.pstdev(rets)
        out[t] = sd * math.sqrt(days_per_year) * 100.0
    return out


def iv_rv_spread(dvol: Sequence, closes: Sequence, window: int) -> list:
    """IV−RV 价差(百分数单位):dvol[t] − realized_vol(closes, window)[t]。
    两个序列按下标对齐(调用方保证同一日网格);任一侧缺失 → None。"""
    rv = realized_vol(closes, window)
    if len(dvol) != len(rv):
        raise ValueError(f"dvol/closes length mismatch: {len(dvol)} vs {len(rv)}")
    out = []
    for dv, r in zip(dvol, rv):
        f = _finite(dv)
        out.append(f - r if (f is not None and r is not None) else None)
    return out


def zscore(series: Sequence, window: int) -> list:
    """尾随时序 z-score:z[t] = (x[t] − mean) / pstdev,窗口 = series[t-window+1..t] 的**有效值**。
    只用过去+当前(截断不变,PIT 安全)。有效值 < window → None;pstdev == 0 → None;NaN → None。
    services/research 里既有的 z-score 都是截面算子(ic_panel/signals 内联),没有可复用的
    时序版,故在此提供。"""
    if window < 2:
        raise ValueError(f"window must be >= 2, got {window}")
    vals = [_finite(v) for v in series]
    out = [None] * len(vals)
    for t in range(len(vals)):
        w = [v for v in vals[max(0, t - window + 1):t + 1] if v is not None]
        if vals[t] is None or len(w) < window:
            continue
        sd = statistics.pstdev(w)
        if sd > 0:
            out[t] = (vals[t] - statistics.fmean(w)) / sd
    return out


def annualized_basis(perp_close, dated_close, days_to_expiry) -> Optional[float]:
    """单点年化基差:(dated/perp − 1) × 365/dte。正=升水(contango)。
    守卫:dte<=0、价格非正、非有限 → None(到期日当天基差无意义,不外推)。"""
    p, d = _finite(perp_close), _finite(dated_close)
    dte = _finite(days_to_expiry)
    if p is None or d is None or dte is None or p <= 0 or d <= 0 or dte <= 0:
        return None
    return (d / p - 1.0) * DAYS_PER_YEAR / dte


def term_structure_slope(perp_close, near_close, far_close,
                         dte_near, dte_far) -> Optional[float]:
    """期限结构斜率 = 年化基差(远端) − 年化基差(近端)(均对 perp)。
    正=向上倾斜(contango 加深)。守卫:0 < dte_near < dte_far,价格非正 → None。"""
    dn, df = _finite(dte_near), _finite(dte_far)
    if dn is None or df is None or dn <= 0 or df <= dn:
        return None
    near = annualized_basis(perp_close, near_close, dn)
    far = annualized_basis(perp_close, far_close, df)
    if near is None or far is None:
        return None
    return far - near


# --- 期权快照:Black-76 delta + 25Δ skew(纯数学,无利率:forward 用同到期期货 mark 价) ---

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(forward, strike, dte_years, iv, is_call: bool) -> Optional[float]:
    """Black-76 delta(未贴现):d1 = (ln(F/K) + ½σ²T) / (σ√T);call = N(d1),put = N(d1) − 1。
    `iv` 是**小数**(0.3949,非 39.49)。守卫:F/K/iv 非正、T<=0、非有限 → None。
    Deribit book summary 不含 greeks,故 delta 由 mark_iv + 同到期 forward 自算(诚实:模型
    依赖,非交易所官方 greeks;用于选 25Δ 行足够)。"""
    f, k = _finite(forward), _finite(strike)
    t, s = _finite(dte_years), _finite(iv)
    if f is None or k is None or t is None or s is None:
        return None
    if f <= 0 or k <= 0 or t <= 0 or s <= 0:
        return None
    d1 = (math.log(f / k) + 0.5 * s * s * t) / (s * math.sqrt(t))
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


def skew_25d(entries: Sequence[dict], *, target: float = 0.25,
             tolerance: float = 0.20) -> dict:
    """一个到期的期权行集 -> 25Δ skew = IV(25Δ put) − IV(25Δ call)(IV 单位跟随输入 mark_iv)。

    entries: dicts with keys is_call(bool), delta(float), mark_iv(float, 交易所口径=百分数),
    strike(仅回报用)。取 delta 最接近 ±target 的 put/call;最近距离 > tolerance → 该侧记缺
    (诚实:附近没有可用的 25Δ 行,不硬凑)。任一侧缺 → skew=None 但仍回传单侧(描述用)。"""
    best_put = None        # (|delta+target|, entry)
    best_call = None       # (|delta-target|, entry)
    for e in entries:
        iv = _finite(e.get("mark_iv"))
        dl = _finite(e.get("delta"))
        if iv is None or dl is None or iv <= 0:
            continue
        if e.get("is_call"):
            dist = abs(dl - target)
            if dl > 0 and (best_call is None or dist < best_call[0]):
                best_call = (dist, e)
        else:
            dist = abs(dl + target)
            if dl < 0 and (best_put is None or dist < best_put[0]):
                best_put = (dist, e)
    put = best_put[1] if best_put is not None and best_put[0] <= tolerance else None
    call = best_call[1] if best_call is not None and best_call[0] <= tolerance else None
    put_iv = _finite(put["mark_iv"]) if put else None
    call_iv = _finite(call["mark_iv"]) if call else None
    return {
        "skew": (put_iv - call_iv) if (put_iv is not None and call_iv is not None) else None,
        "put_iv": put_iv,
        "call_iv": call_iv,
        "put_strike": put.get("strike") if put else None,
        "call_strike": call.get("strike") if call else None,
        "put_delta": _finite(put["delta"]) if put else None,
        "call_delta": _finite(call["delta"]) if call else None,
        "n_entries": sum(1 for e in entries if _finite(e.get("mark_iv")) is not None),
    }


# --- 时序因子筛选统计(单序列因子 vs 前向收益;n<10 全 None,与 ic_panel 同口径) ---

def _pearson(xs: list, ys: list) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx > 0 and dy > 0 else None


def _rank_map(values: list) -> dict:
    """sorted-value -> rank(与 ic_panel._rank 同一平局简化:重复值取最后出现的秩)。"""
    return {v: i for i, v in enumerate(sorted(values))}


def ts_ic_stats(pairs: Sequence[tuple], *, min_n: int = _MIN_N) -> dict:
    """(factor, forward_return) 对序列 -> {pearson, spearman, t_stat, n}。

    Spearman 为主口径(与全库 rank-IC 偏好一致);t_stat 由 Spearman 经
    t = r·sqrt((n−2)/(1−r²)) 导出(大样本近似,重叠收益会虚高 t——调用方负责
    非重叠抽样,见 tools/vol_research.py 的 horizon 口径)。有效对 < min_n → 统计全 None,
    n 恒是真实计数。None/NaN 对在函数内剔除。"""
    clean = []
    for f, r in pairs:
        fv, rv = _finite(f), _finite(r)
        if fv is not None and rv is not None:
            clean.append((fv, rv))
    n = len(clean)
    if n < min_n:
        return {"pearson": None, "spearman": None, "t_stat": None, "n": n}
    xs = [p[0] for p in clean]
    ys = [p[1] for p in clean]
    pear = _pearson(xs, ys)
    rx, ry = _rank_map(xs), _rank_map(ys)
    spear = _pearson([rx[x] for x in xs], [ry[y] for y in ys])
    t_stat = None
    if spear is not None and abs(spear) < 1.0 and n > 2:
        t_stat = spear * math.sqrt((n - 2) / (1.0 - spear * spear))
    return {
        "pearson": round(pear, 4) if pear is not None else None,
        "spearman": round(spear, 4) if spear is not None else None,
        "t_stat": round(t_stat, 4) if t_stat is not None else None,
        "n": n,
    }


def per_year_ics(pairs: Sequence[tuple], event_times: Sequence[int], *,
                 min_n: int = _MIN_N) -> dict:
    """按 UTC 年分桶的 Spearman IC:{year: ic|None}(年样本 < min_n → None)。
    pairs 与 event_times(ms)按下标对齐;无效对被剔除(不计入任何年)。"""
    if len(pairs) != len(event_times):
        raise ValueError(f"pairs/event_times length mismatch: {len(pairs)} vs {len(event_times)}")
    by_year: dict = {}
    for (f, r), t in zip(pairs, event_times):
        fv, rv = _finite(f), _finite(r)
        if fv is None or rv is None:
            continue
        by_year.setdefault(time.gmtime(int(t) / 1000).tm_year, []).append((fv, rv))
    out = {}
    for year, rows in sorted(by_year.items()):
        if len(rows) < min_n:
            out[str(year)] = None
            continue
        xs = [p[0] for p in rows]
        ys = [p[1] for p in rows]
        rx, ry = _rank_map(xs), _rank_map(ys)
        ic = _pearson([rx[x] for x in xs], [ry[y] for y in ys])
        out[str(year)] = round(ic, 4) if ic is not None else None
    return out
