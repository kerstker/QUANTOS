"""宏观 regime 分类器(数据方向)—— 把宏观序列切成 regime,把一条边的逐周收益按 regime 分桶算表现。纯函数、
确定性、无 IO/RNG/墙钟/网络。

两类分类器:
- `tercile_labels`(全样本分位)= **回顾性描述**,非 PIT 可交易择时(调用方须标注)。
- `expanding_tercile_labels`(扩展窗分位)/ `sign_regime`(符号)/ `fixed_threshold_labels`(固定阈值)
  = **PIT 可交易**:每个位置只用**过去+当前**信息定标签,故对未来数据追加**不变(截断不变)**——门/优化器可合法据以行动(Rank #14)。

ALFRED vintage 的 PIT 求值(2026-07-06):`pit_latest_asof` / `pit_yoy_asof`(逐点,语义规范)+
`pit_series`(批量,单调游标预索引,O(R log R + W) —— CPIAUCSL 全 vintage >10 万行时逐周全表扫
是分钟级,批量版是调用侧的确定性能策略)。输入 rows = (event_yyyymmdd, knowledge_yyyymmdd, value),
输出值真 PIT(只用 as_of 时点可知的 vintage);分位标签是否 PIT 由上面选哪个分类器决定。"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence


def expanding_tercile_labels(values: Sequence[Optional[float]], min_history: int = 10) -> list:
    """PIT 三分位(Rank #14):位置 i 的三分位阈值只用 `values[:i+1]` 的有效值(仅过去+当前),因此对未来
    数据追加**不变**(截断不变:`labels(values[:k])[i] == labels(values)[i]`,i<k)。有效值 < `min_history`
    → 'mid'(暖启,样本不足以定分位);None → None。这才是可交易的 regime 分类(非全样本回顾)。"""
    out: list = []
    seen: list = []                                   # 到 i 为止(含)的有效值
    for v in values:
        if v is None:
            out.append(None)
            continue
        seen.append(v)
        if len(seen) < min_history:
            out.append("mid")
            continue
        s = sorted(seen)
        lo, hi = s[len(s) // 3], s[2 * len(s) // 3]
        out.append("low" if v <= lo else "high" if v >= hi else "mid")
    return out


def fixed_threshold_labels(values: Sequence[Optional[float]], low: float, high: float,
                           labels: tuple = ("calm", "mid", "stress")) -> list:
    """免估计固定阈值分类(如 VIX 20/30):零估计 → 天然 PIT、天然截断不变。None → None。"""
    lo_l, mid_l, hi_l = labels
    return [None if v is None else (lo_l if v < low else hi_l if v >= high else mid_l) for v in values]


def tercile_labels(values: Sequence[float]) -> list:
    """全样本三分位 → ['low'|'mid'|'high']。None 保留为 None;<3 个有效值 → 全 'mid'。"""
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return ["mid"] * len(values)
    s = sorted(vals)
    lo, hi = s[len(s) // 3], s[2 * len(s) // 3]
    out = []
    for v in values:
        if v is None:
            out.append(None)
        elif v <= lo:
            out.append("low")
        elif v >= hi:
            out.append("high")
        else:
            out.append("mid")
    return out


def sign_regime(value: Optional[float], neg_label: str = "inverted", pos_label: str = "normal") -> Optional[str]:
    if value is None:
        return None
    return neg_label if value < 0 else pos_label


# --- ALFRED vintage 的 PIT 求值(rows = (event_yyyymmdd, knowledge_yyyymmdd, value),任意序,纯函数无 IO) ---
#
# 语义:as_of 时点"研究员真实拥有的最佳已知值"。knowledge<=as_of 的行才可见;同一 event 的
# 历次修正取 knowledge 最新的那次(跨 vintage 混用是特性不是 bug——m 月 CPI 在 ~m+1 月中旬发布前
# 对任何 as_of 都不可见,修正值只在其 realtime_start 之后可见)。


def _ym(yyyymmdd: int) -> int:
    """yyyymmdd -> 连续月序号(年*12+月-1),用于跨年月减。"""
    return (yyyymmdd // 10000) * 12 + (yyyymmdd // 100 % 100) - 1


def _last_by_key(items, key):
    """"max by key, 平局时保留 items 里**最后**出现的那个"(2026-07-06 对抗式复审 CONFIRMED
    修复:Python 内置 `max()` 平局保留*第一个*遇到的,与 `pit_series` 的稳定排序+覆盖写
    (逐条 `latest_val[e]=v` 覆盖,平局时**最后**处理的行胜出)不一致——同一个 (event,
    knowledge) 出现两次但 value 不同时,两套实现会给出不同答案,打破"语义与逐点函数完全
    一致"的等价性声明。改用显式 `>=` 比较,让平局统一由"更晚出现"的行胜出,两套实现在
    平局上也保持一致。"""
    best, best_key = None, None
    for item in items:
        k = key(item)
        if best is None or k >= best_key:
            best, best_key = item, k
    return best


def pit_latest_asof(rows: Sequence[tuple], as_of: int) -> Optional[float]:
    """as_of 时点可知的**最新 event**(不是最新 knowledge)的最新可知值;无可知行 → None。"""
    best = None                                        # (event, knowledge, value)
    for e, k, v in rows:
        if k > as_of or v is None:
            continue
        if best is None or (e, k) >= (best[0], best[1]):
            best = (e, k, v)
    return best[2] if best else None


def pit_yoy_asof(rows: Sequence[tuple], as_of: int, months_back: int = 12) -> Optional[float]:
    """PIT YoY:v0 = 最新 event 的最新可知值;v1 = event 月份恰为其 months_back 个月前的最新可知值
    (按 yyyymm 做月减,跨年安全;季频序列如 GDPC1 传 12 同样落在去年同季——期首月相减恰 12,
    有测试钉死)。缺任一(或 v1==0)→ None。"""
    knowable = [(e, k, v) for e, k, v in rows if k <= as_of and v is not None]
    if not knowable:
        return None
    e0 = max(e for e, _, _ in knowable)
    v0 = _last_by_key([r for r in knowable if r[0] == e0], key=lambda r: r[1])[2]
    target = _ym(e0) - months_back
    base = [r for r in knowable if _ym(r[0]) == target]
    if not base:
        return None
    v1 = _last_by_key(base, key=lambda r: (r[0], r[1]))[2]
    return None if v1 == 0 else v0 / v1 - 1.0


def pit_series(rows: Sequence[tuple], as_ofs: Sequence[int],
               months_back: Optional[int] = None) -> list:
    """批量 PIT 求值(调用侧确定性能策略,评审 required_change):as_ofs 必须**升序**,对 rows 只做
    一次 (knowledge, event) 排序 + 单调游标增量索引 —— O(R log R + R + W),而不是每个 as_of 全表扫
    O(W×R)(CPIAUCSL 全 vintage >10 万行 × ~230-300 周会是分钟级)。语义与逐点函数完全一致
    (months_back=None ≡ pit_latest_asof;否则 ≡ pit_yoy_asof;等价性有测试钉死,含平局情形——
    2026-07-06 对抗式复审 CONFIRMED 修复:排序键刻意只取 (knowledge, event),不把 value 混进
    比较——`sorted()` 是稳定排序,(knowledge, event) 相同的行按原始 `rows` 顺序保留相对次序,
    循环覆盖写让**最后**一行胜出,与 `pit_latest_asof`/`pit_yoy_asof` 现在统一的"平局由更晚
    出现的行胜出"规则一致;此前把 value 当第三顺位排序键会让平局胜负偶然取决于数值大小,
    与逐点函数的平局语义各自独立、可能给出不同答案)。确定性:
    排序键完全有序,无 RNG/墙钟。as_ofs 非升序 → ValueError(fail-closed,静默错序会给错答案)。"""
    order = sorted(((k, e, v) for e, k, v in rows if v is not None), key=lambda row: (row[0], row[1]))
    latest_val: dict = {}                              # event -> 最新可知值(knowledge 升序后写覆盖)
    month_event: dict = {}                             # 月序号 -> 该月最大 event
    max_event: Optional[int] = None
    out: list = []
    i, n = 0, len(order)
    prev = None
    for a in as_ofs:
        if prev is not None and a < prev:
            raise ValueError("pit_series: as_ofs must be ascending")
        prev = a
        while i < n and order[i][0] <= a:
            k, e, v = order[i]
            i += 1
            latest_val[e] = v
            ym = _ym(e)
            if ym not in month_event or e > month_event[ym]:
                month_event[ym] = e
            if max_event is None or e > max_event:
                max_event = e
        if max_event is None:
            out.append(None)
            continue
        if months_back is None:
            out.append(latest_val[max_event])
            continue
        base_e = month_event.get(_ym(max_event) - months_back)
        if base_e is None:
            out.append(None)
            continue
        v1 = latest_val[base_e]
        out.append(None if v1 == 0 else latest_val[max_event] / v1 - 1.0)
    return out


def _ann_sharpe(rs: Sequence[float], ppy: int = 52) -> float:
    if len(rs) < 2:
        return 0.0
    sd = statistics.pstdev(rs)
    return 0.0 if sd == 0 else statistics.fmean(rs) / sd * math.sqrt(ppy)


def bucket_returns(returns: Sequence[float], labels: Sequence, ppy: int = 52) -> dict:
    """按 label 分桶 → {label: {n, ann_sharpe, mean_return, total_return}}。label=None 的期跳过。"""
    buckets = {}
    for r, lb in zip(returns, labels):
        if lb is None:
            continue
        buckets.setdefault(lb, []).append(r)
    out = {}
    for lb, rs in buckets.items():
        cum = 1.0
        for r in rs:
            cum *= (1.0 + r)
        out[lb] = {"n": len(rs), "ann_sharpe": round(_ann_sharpe(rs, ppy), 3),
                   "mean_return": round(statistics.fmean(rs), 6), "total_return": round(cum - 1.0, 6)}
    return out
