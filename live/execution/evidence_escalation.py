"""live-paper 偏差自动升级(2026-07-05,机构审计 P1 quick win)。设计:
docs/engineering/specs/2026-07-05-institutional-live-stack-design.md 补充。

纯函数、无 IO。此前 parity gap/TCA 滑点只是记录,从未按阈值区分"日常噪音"与"真正该有人
看一眼的偏差"——真实数据里出现过 gap≈7% AUM、滑点 942bps 都被淹没在普通 info alert 里。
本模块只做判断,不决定如何送达(那是调用方的事,如 emit 到独立通道/未来的死人开关)。
"""
from __future__ import annotations

from typing import List, Mapping


def check_evidence_deviation(parity: Mapping, tca: Mapping, *, aum: float,
                             max_gap_pct_of_aum: float,
                             max_slippage_over_assumed_bps: float) -> List[str]:
    """超阈值才返回非空——阈值内不产生噪音(与既有"只在真正需要时才说话"的告警哲学一致)。"""
    out: List[str] = []
    gap = parity.get("max_abs_gap_notional")
    if gap is not None and aum > 0:
        gap_pct = abs(gap) / aum
        if gap_pct > max_gap_pct_of_aum:
            out.append(f"ESCALATION: live-paper parity gap {gap_pct:.1%} of AUM "
                      f"(${gap:.2f}) exceeds {max_gap_pct_of_aum:.0%} threshold — "
                      f"execution has drifted materially from the paper-track decision")

    over = tca.get("weighted_minus_assumed_bps")
    if over is not None and over > max_slippage_over_assumed_bps:
        out.append(f"ESCALATION: TCA slippage exceeds cost assumption by {over:.1f}bps "
                  f"(threshold {max_slippage_over_assumed_bps:.0f}bps) — backtest cost model "
                  f"may be understating real execution cost")
    return out
