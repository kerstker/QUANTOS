"""截面拥挤度分数(2026-07-05,机构审计 P1 quick win)。设计:
docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md 补充。

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
