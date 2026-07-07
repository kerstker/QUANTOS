"""Firm-level exposure hard gate (审计 P1-6)。

`firm_aggregation.aggregate_firm_books` 把所有 book/交易所的仓位加总成真实的 firm 敞口视图,但它
**只诊断不阻断**——同一 symbol 被多个所同向持有形成的隐藏拥挤(2007-08 quant 踩踏),单所的
per-venue mandate 看不见,只会 emit 一条需要人读的告警。本门把那个诊断视图 + 由**人签署**的 firm
上限,升级成一个可阻断的硬门。

上限含义(与 CLAUDE.md / 用户 2026-07-07 问答一致):
- `max_gross`:所有所仓位**名义额绝对值之和**的上限(总押注量,防杠杆/保证金总量失控)。
- `max_net`:所有所仓位**带方向求和**的上限(净方向敞口,防自以为中性实际单边裸奔)。
- `max_single_name_weight`:同一 symbol **跨所加总**占 firm_gross 的比例上限(隐藏单名集中/拥挤)。

fail-closed:aggregate 本身 BLOCKED → 传播 BLOCK;任何**已配置**的上限是非有限值(NaN/inf)→ BLOCK
(NaN 参与 `>` 恒 False 会静默放行,镜像 mandate 的 NaN 修复);**没配任何上限 → UNCONFIGURED**
(不静默 PASS——调用方决定:多所自动执行时把 UNCONFIGURED 当 fail-closed 阻断,单所时 firm=per-venue
可忽略)。纯函数、无 IO/墙钟。诚实边界:数字必须由真人签署,本门只在数字在案时机械执行。
"""
from __future__ import annotations

import math
from typing import Optional

_EPS = 1e-12


def _finite(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def evaluate_firm_gate(firm_view: dict, *, max_gross: Optional[float] = None,
                       max_net: Optional[float] = None,
                       max_single_name_weight: Optional[float] = None) -> dict:
    """见模块 docstring。返回 {"status": BLOCK|PASS|UNCONFIGURED, "reasons": (...)}。"""
    if not isinstance(firm_view, dict) or firm_view.get("status") == "BLOCKED":
        reasons = firm_view.get("reasons", ("aggregate_blocked",)) if isinstance(firm_view, dict) \
            else ("invalid_firm_view",)
        return {"status": "BLOCK", "reasons": tuple(reasons)}

    configured = [("max_gross", max_gross), ("max_net", max_net),
                  ("max_single_name_weight", max_single_name_weight)]
    active = [(name, val) for name, val in configured if val is not None]
    if not active:
        return {"status": "UNCONFIGURED", "reasons": ("no_firm_limits_configured",)}

    # fail-closed:任何已配置上限非有限 -> BLOCK(NaN/inf 不能被当成"无限宽松"静默放行)
    for name, val in active:
        if not _finite(val):
            return {"status": "BLOCK", "reasons": (f"non_finite_firm_limit:{name}",)}

    reasons = []
    firm_gross = float(firm_view.get("firm_gross", 0.0))
    firm_net = float(firm_view.get("firm_net", 0.0))
    if max_gross is not None and firm_gross > float(max_gross):
        reasons.append(f"firm_gross_exceeded:{firm_gross:.2f}>{float(max_gross):.2f}")
    if max_net is not None and abs(firm_net) > float(max_net):
        reasons.append(f"firm_net_exceeded:{firm_net:.2f}>{float(max_net):.2f}")
    if max_single_name_weight is not None and firm_gross > _EPS:
        # top_firm_positions 是按 |position| 排序的最大若干个——最大集中度必在其中,逐一核 firm_weight
        for tp in firm_view.get("top_firm_positions", ()):
            w = abs(float(tp["firm_position"])) / firm_gross
            if w > float(max_single_name_weight):
                reasons.append(
                    f"firm_single_name_exceeded:{tp['symbol']}:{w:.4f}>{float(max_single_name_weight)}")

    if reasons:
        return {"status": "BLOCK", "reasons": tuple(reasons)}
    return {"status": "PASS", "reasons": ()}
