"""因子卫生(factor hygiene)—— 横截面 winsorize + train 相关性去冗余。纯函数、无 IO/RNG/墙钟。

- cs_winsorize_row: 单日横截面 1%/99% 分位截尾(线性插值分位),None 透传,有效值 < min_n
  时整行 None(fail-closed,与 cs_zscore_row 同一闸门)。winsorize 是逐行单期操作 —— 只用 t 时刻
  可知的数据,不前视。
- cs_winsorized_zscore_row: 先截尾再 z-score。截尾后尾部被钳制,复合分不再被单日单票的极端
  因子值主导(微盘反转/振幅类因子右尾极肥,未截尾时 z 分数对离群值敏感)。
- factor_correlation_matrix_train: 仅用 TRAIN 日期(event_time <= train_end)的横截面
  *rank* 值,把所有 (t, i) 有效对池化后算两两 Pearson 相关。用 rank 而非 raw:对单调节拍
  不变、对肥尾稳健。train-only —— 选择规则绝不读 test(与 combine.fit_combo_weights 的
  no-peek 契约同族,由测试钉死)。
- greedy_decorrelate: 与 services/research/factor_set.py 的协同去冗余同一规则族(增量价值
  vs 已有基的相关性闸门),这里用的是更朴素的贪心变体:按 train 强度(|t_stat| 降序,名字
  字典序破平局)遍历通过 |t| 初筛的因子,若与任何已保留因子的 |corr| > threshold 则丢弃
  (保留更强者)。返回 (kept, dropped) —— dropped 带 {name, corr_with, corr} 诚实记账。
"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence

CORR_THRESHOLD = 0.7
WINSOR_LO = 0.01
WINSOR_HI = 0.99


def _quantile(sorted_vals: Sequence[float], q: float) -> float:
    """线性插值分位(numpy 'linear' 同义),sorted_vals 非空。"""
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_vals[lo]) * (1.0 - frac) + float(sorted_vals[hi]) * frac


def cs_winsorize_row(row: list, lo: float = WINSOR_LO, hi: float = WINSOR_HI,
                     min_n: int = 3) -> list:
    """横截面截尾:有效值钳到 [q_lo, q_hi];None 透传;有效值 < min_n 整行 None。"""
    valid = [v for v in row if v is not None]
    if len(valid) < min_n:
        return [None] * len(row)
    s = sorted(valid)
    q_lo, q_hi = _quantile(s, lo), _quantile(s, hi)
    return [min(max(v, q_lo), q_hi) if v is not None else None for v in row]


def cs_winsorized_zscore_row(row: list, lo: float = WINSOR_LO, hi: float = WINSOR_HI,
                             min_n: int = 3) -> list:
    """先 1%/99% 截尾再 z-score(均值/标准差在截尾后的值上计算)。"""
    w = cs_winsorize_row(row, lo=lo, hi=hi, min_n=min_n)
    valid = [v for v in w if v is not None]
    if len(valid) < min_n:
        return [None] * len(row)
    mu, sd = statistics.fmean(valid), statistics.pstdev(valid)
    if sd <= 0:
        return [None] * len(row)
    return [(v - mu) / sd if v is not None else None for v in w]


def factor_correlation_matrix_train(rank_mats: dict, event_times: Sequence[int],
                                    train_end: int) -> dict:
    """{name: {name: corr}} —— 仅用 train 日期的横截面 rank 值池化 Pearson 相关。

    对每个因子对,收集所有 (t in train, i) 上两者都非 None 的 (x, y) 对;对数 < 10
    或任一方差为 0 -> None(诚实,不硬凑)。对角线恒 1.0。
    """
    names = sorted(rank_mats)
    out: dict = {a: {b: (1.0 if a == b else None) for b in names} for a in names}
    for x_i, a in enumerate(names):
        for b in names[x_i + 1:]:
            xs, ys = [], []
            ma, mb = rank_mats[a], rank_mats[b]
            for t in range(len(event_times)):
                if event_times[t] > train_end:
                    continue
                for i in range(len(ma[t])):
                    va, vb = ma[t][i], mb[t][i]
                    if va is not None and vb is not None:
                        xs.append(va)
                        ys.append(vb)
            n = len(xs)
            if n < 10:
                continue
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            vx = sum((v - mx) ** 2 for v in xs)
            vy = sum((v - my) ** 2 for v in ys)
            if vx <= 0 or vy <= 0:
                continue
            cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            corr = cov / math.sqrt(vx * vy)
            out[a][b] = round(corr, 6)
            out[b][a] = round(corr, 6)
    return out


def greedy_decorrelate(train_ic_stats: dict, corr: dict, horizon_key: str = "h1",
                       t_threshold: float = 2.0,
                       corr_threshold: float = CORR_THRESHOLD) -> tuple:
    """贪心去冗余选择。只读 train 聚合(no-peek)。

    返回 (kept, dropped, screened_out):
      kept         —— 按强度降序保留的因子名列表;
      dropped      —— [{name, corr_with, corr}] 因与更强已选因子 |corr|>threshold 被丢;
      screened_out —— 未过 |t|>t_threshold 初筛的因子名(与去冗余分开记账)。
    强度 = |train t_stat|(None -> 0,排最后),字典序破平局(确定性)。
    """
    cands = []
    screened_out = []
    for name in sorted(train_ic_stats):
        agg = train_ic_stats[name][horizon_key]["train"]
        t = agg.get("t_stat")
        if t is None or abs(t) <= t_threshold:
            screened_out.append(name)
        else:
            cands.append(name)
    cands.sort(key=lambda nm: (-abs(train_ic_stats[nm][horizon_key]["train"]["t_stat"]), nm))
    kept: list = []
    dropped: list = []
    for name in cands:
        worst: Optional[tuple] = None  # (|corr|, other, corr)
        for other in kept:
            c = corr.get(name, {}).get(other)
            if c is None:
                continue
            if worst is None or abs(c) > worst[0]:
                worst = (abs(c), other, c)
        if worst is not None and worst[0] > corr_threshold:
            dropped.append({"name": name, "corr_with": worst[1], "corr": round(worst[2], 6)})
        else:
            kept.append(name)
    return kept, dropped, screened_out
