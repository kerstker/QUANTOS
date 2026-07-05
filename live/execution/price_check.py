"""跨源核价(2026-07-05,机构审计 P0)。设计:
docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md 补充。

纯函数、无网络/无 IO。回答的问题:主决策源(交易所自己的价格)一次静默滞后/错误,
会不会同时污染下单和风控评估?——用独立第二源交叉核价,单标的偏离超阈值或无法验证
一律标记(调用方据此从本轮批次剔除该标的,fail-soft per-instrument,不因一个标的的
核价失败拖垮整批)。
"""
from __future__ import annotations

from typing import Dict, Mapping


def flag_divergent_instruments(primary: Mapping[str, float], secondary: Mapping[str, float],
                               *, max_divergence_pct: float,
                               secondary_unavailable: bool = False) -> Dict[str, str]:
    """primary/secondary = {instrument_id: price}(两个独立来源)。返回
    {instrument_id: 剔除原因} ——只列出**需要剔除**的标的,未出现的标的视为核价通过。

    secondary_unavailable=True(第二源整体拿不到,如网络故障):fail-soft 返回空——不能把
    "我们没查到第二源"误判成"每个标的都可疑",那是两种不同性质的故障,调用方应对
    "整体不可用"单独告警,而不是让所有标的一起被剔除。
    """
    if secondary_unavailable:
        return {}
    out: Dict[str, str] = {}
    for inst, px in primary.items():
        if px is None or px <= 0:
            out[inst] = f"unverifiable: primary price non-positive ({px})"
            continue
        sec = secondary.get(inst)
        if sec is None or sec <= 0:
            out[inst] = "unverifiable: no secondary-source price available"
            continue
        divergence = abs(px - sec) / px
        if divergence > max_divergence_pct:
            out[inst] = (f"diverges {divergence:.2%} from secondary source "
                        f"(primary={px}, secondary={sec}, threshold={max_divergence_pct:.0%})")
    return out
