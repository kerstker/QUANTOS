"""截面拥挤度分数(2026-07-05,机构审计 P1 quick win)。设计:
docs/engineering/specs/2026-07-05-institutional-live-stack-design.md 补充。

`services/portfolio/crowding_gate.py` 已建成测试却从未接入真实下单路径——审计发现缺的是
"这条腿到底有多拥挤"的证据源。crowd_contra 本身就是 `CS_RANK(NEG(global_ls))`(反着做拥挤
的散户多空仓位),这里量化它交易的对象:global_ls 偏离 1.0(多空平衡)越远(无论哪个方向)
= 越拥挤,截面分位排名到 [0,1](1=当期最拥挤)。纯函数、无 IO。
"""
from __future__ import annotations

from typing import Dict, Mapping, Optional


def compute_crowding_scores(global_ls_by_instrument: Mapping[str, Optional[float]]) -> Dict[str, float]:
    extremity = {k: abs(v - 1.0) for k, v in global_ls_by_instrument.items() if v is not None}
    if not extremity:
        return {}
    if len(extremity) == 1:
        return {next(iter(extremity)): 0.5}          # 单标的无法排名,给中性分,不制造假信号
    sorted_vals = sorted(extremity.values())
    n = len(sorted_vals)

    def _avg_rank(value: float) -> float:
        idxs = [i for i, sv in enumerate(sorted_vals) if sv == value]
        return sum(idxs) / len(idxs)

    return {k: _avg_rank(v) / (n - 1) for k, v in extremity.items()}


def crowding_zscore(gmat, r: int, i: int, min_hist: int = 12) -> Optional[float]:
    """**绝对**拥挤度(正确统计量,校准见 docs/engineering/specs/2026-07-07-crowding-gate-calibration.md):
    标的 i 在行 r 的 global_ls 当前值 vs **它自己的时序历史**(gmat[0..r) 的非 None 值,≥min_hist 期)的
    z-score。区分"本周相对排位"(rank,结构性)与"这个名字**现在**绝对拥挤程度"(z,真有信息)。历史不足 /
    零方差 / 当前缺值 -> None(fail-closed:该名不评 z 维)。纯函数、无 IO(与 dry-run 驱动器共用同一实现)。"""
    hist = [gmat[rr][i] for rr in range(0, r) if gmat[rr][i] is not None]
    if len(hist) < min_hist:
        return None
    m = sum(hist) / len(hist)
    var = sum((x - m) ** 2 for x in hist) / len(hist)
    cur = gmat[r][i]
    if var <= 1e-24 or cur is None:
        return None
    return (cur - m) / (var ** 0.5)
