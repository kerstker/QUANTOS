"""拥挤度统计量诊断(2026-07-07,回答"平均分是错的,正确统计量该是什么")。

**发现(233 周真实 global_ls 全历史)**:crowd_contra 拥挤门现用的 rank-based 统计量**全部结构性恒定**——
gross 加权平均拥挤分恒 = 0.500(对称 L/S book 的数学恒等式,零方差)、max 单名拥挤恒 = 1.000(截面 rank
天然有 top 名)、短腿加权拥挤 ≈0.77(总是 short 最拥挤的,结构性)、max 单名权重/gross ≈0.07(book 极分散,
**集中度判据从不触发**)。**任何 rank 阈值都不是有效的绝对拥挤门**——cross-sectional rank 天然 [0,1],只测
"本周相对排位",不测"绝对拥挤程度"。

**正确统计量 = 绝对/时序度量**:被 short 的(拥挤多头)名字的 raw trailing global_ls(实测 1.99–6.58,变化
3.3×)与其**时序 z-score**(当前拥挤 vs 该名自己历史;短腿 MAX z 实测 -0.5 到 5.87,p90≈2.9)。z-score 真的
区分"正常周"与"这个名字现在异常拥挤=挤压风险"周,是可被有意义地设阈值的量。

诊断非门禁:本工具只算+落快照,不改 crowding_gate 行为。是否把 rank 平均路径改 WARN-only、是否加一个绝对
z-score BLOCK 门(用户签 z 阈值)= 有意行为变更,待用户拍板。纯离线(build_panel 读 crypto_daily.db),无网络。
"""
from __future__ import annotations

import json
import os
import statistics as st

from services.research import alpha_expression as ax
from tools.crypto_oi_research import QUANTILE, _legs, build_panel
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.dry_run_crowd_contra_multi import _WEEK_MS, _crowding_rank

_OUT = os.path.join("data", "cache", "crowding_metric_snapshot.json")


def _trailing_means(gmat, r, idx, lookback=4):
    out = {}
    for i in idx:
        w = [gmat[rr][i] for rr in range(max(0, r - lookback + 1), r + 1) if gmat[rr][i] is not None]
        if w:
            out[i] = sum(w) / len(w)
    return out


def _zscore(gmat, r, i, min_hist=12):
    hist = [gmat[rr][i] for rr in range(0, r) if gmat[rr][i] is not None]
    if len(hist) < min_hist:
        return None
    s = st.pstdev(hist)
    cur = gmat[r][i]
    return None if (s <= 1e-12 or cur is None) else (cur - st.fmean(hist)) / s


def _dist(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    q = lambda p: xs[min(len(xs) - 1, int(p * len(xs)))]
    return {"n": len(xs), "mean": round(st.fmean(xs), 4), "min": round(xs[0], 4),
            "p10": round(q(.1), 4), "p50": round(q(.5), 4), "p90": round(q(.9), 4),
            "max": round(xs[-1], 4)}


def scan() -> dict:
    panel, _ = build_panel()
    gmat = panel.matrices["global_ls"]
    ev = panel.event_times
    sig = ax.evaluate(CROWD_CONTRA, panel)
    rows, last = [], None
    for r in range(len(ev)):
        if last is None or ev[r] - last >= _WEEK_MS:
            rows.append(r); last = ev[r]

    acc = {k: [] for k in ("avg_rank", "max_rank", "short_rank", "long_rank", "max_weight",
                           "short_raw_gls", "short_mean_z", "short_max_z")}
    for r in rows:
        legs = _legs(sig[r], QUANTILE)
        if not legs:
            continue
        crowd = _crowding_rank(gmat, r, list(legs))
        if not crowd:
            continue
        gross = sum(abs(w) for w in legs.values())
        shorts = [i for i in legs if legs[i] < 0 and i in crowd]
        longs = [i for i in legs if legs[i] > 0 and i in crowd]
        acc["avg_rank"].append(sum(abs(legs[i]) * crowd[i] for i in crowd) / gross)
        acc["max_rank"].append(max(crowd.values()))
        acc["max_weight"].append(max(abs(w) for w in legs.values()) / gross)
        if shorts:
            sw = sum(abs(legs[i]) for i in shorts)
            acc["short_rank"].append(sum(abs(legs[i]) * crowd[i] for i in shorts) / sw)
            tm = _trailing_means(gmat, r, list(legs))
            raw = [tm[i] for i in shorts if i in tm]
            if raw:
                acc["short_raw_gls"].append(st.fmean(raw))
            zs = [z for i in shorts if (z := _zscore(gmat, r, i)) is not None]
            if zs:
                acc["short_mean_z"].append(st.fmean(zs)); acc["short_max_z"].append(max(zs))
        if longs:
            lw = sum(abs(legs[i]) for i in longs)
            acc["long_rank"].append(sum(abs(legs[i]) * crowd[i] for i in longs) / lw)
    return {
        "schema": "crowding_metric.v1",
        "n_weeks": len(acc["avg_rank"]),
        "rank_based_structural": {k: _dist(acc[k]) for k in
                                  ("avg_rank", "max_rank", "short_rank", "long_rank", "max_weight")},
        "absolute_informative": {k: _dist(acc[k]) for k in
                                 ("short_raw_gls", "short_mean_z", "short_max_z")},
        "finding": ("rank-based avg/max/short-leg/weight 全部结构性恒定(平均恒=0.500,集中度从不触发);"
                    "正确统计量=被 short 名字的绝对 global_ls z-score(short_max_z 实测 -0.5..5.87,p90≈2.9)。"),
        "provenance": "REAL_CROWDING_METRIC",
    }


def main() -> None:
    snap = scan()
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    json.dump(snap, open(_OUT, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"-> {_OUT}  ({snap['n_weeks']} weeks)")
    for grp in ("rank_based_structural", "absolute_informative"):
        print(f"[{grp}]")
        for k, d in snap[grp].items():
            print(f"  {k:16s} {d}")


if __name__ == "__main__":
    main()
