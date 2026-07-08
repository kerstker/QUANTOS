"""Bridge: alpha 维护 report -> 每策略 live 准入状态(Phase 5 item 1,P1-7)。

维护 sweep(`maintenance.run_maintenance`)把 research catalog 里衰退/失效的 alpha 降级/退役——
非人 actor、审计、只到 DEGRADED/PAUSED/RETIRED,**绝不** APPROVED。本模块把那份 report 翻成
"每个 strategy_id 的 live 准入状态",写进一个 status 文件供 live 驱动器读:本策略已 RETIRED/PAUSED
就**停止新增敞口**(monotonic 加法,语义/签名镜像 `services/portfolio/firm_gate.py::firm_gate_skip_reason`)。

诚实边界(spec §9):本桥只做"降级 -> 停止加仓",**绝不**自动把已有 live 仓位缩回去——那需要用户签的
de-risk 目标数字,且 RETIRED->flat 是 full-flatten 必须走人工确认。那部分不在本 clean 加法里。

纯函数、无 IO/墙钟。strategy_id 与 alpha_id 用同一字符串标签桥接——paper_track.db / forward ledger 已
如此(crowd_contra 既是 alpha_id 又是 strategy_id),不引入新的映射真源。
"""
from __future__ import annotations

from typing import Optional

# 会让驱动器"停止新增敞口"的降级态。DEGRADED 刻意不进——它是"变弱但仍合规在跑"的观察态,
# 自动停掉一个仍合规的策略是过度反应;留给人读告警(保守加法:少停不是漏停,是不越权自动停)。
_SKIP_STATES = ("RETIRED", "PAUSED")
_DOWNGRADE_STATES = ("RETIRED", "PAUSED", "DEGRADED")


def strategy_status_from_report(report: object) -> dict:
    """`run_maintenance` 的 report {alpha_id: {action?, reasons?/alerts?, error?}} ->
    {alpha_id: {"state": RETIRED|PAUSED|DEGRADED|OK, "reasons": [...]}}。

    空 / 非 dict report -> {}(fail-closed:没有证据 = 不知道有降级 = 不产生任何 skip)。
    error 条目:评估该 alpha 时抛异常不是"降级证据",state 记 OK(不停 live),但错误诚实进 reasons 可见。
    """
    out: dict = {}
    if not isinstance(report, dict):
        return out
    for aid, entry in report.items():
        if not isinstance(entry, dict):
            continue
        action = entry.get("action")
        reasons = list(entry.get("reasons") or entry.get("alerts") or [])
        err = entry.get("error")
        if err:
            reasons.append(f"error:{err}")
        state = action if action in _DOWNGRADE_STATES else "OK"
        out[aid] = {"state": state, "reasons": reasons}
    return out


def maintenance_skip_reason(status_json: Optional[dict], strategy_id: str) -> Optional[str]:
    """给驱动器读的桥(镜像 `firm_gate_skip_reason`):status 文件里本策略处于 RETIRED/PAUSED ->
    返回一条 skip 原因(驱动器据此**停止新增**、现有仓位原样不动;下轮 sweep 若不再降级则自然恢复)。

    fail-safe:None / 非 dict / strategies 字段缺失或非 dict / 本策略不在案 / 状态非降级 -> None
    (无维护数据时**不伪造** block——mandate/attestation/firm 门仍各自兜底,与 firm_gate 一致)。
    """
    if not isinstance(status_json, dict):
        return None
    strategies = status_json.get("strategies")
    if not isinstance(strategies, dict):
        return None
    entry = strategies.get(strategy_id)
    if not isinstance(entry, dict):
        return None
    state = entry.get("state")
    if state in _SKIP_STATES:
        reasons = ",".join(str(r) for r in (entry.get("reasons") or [])) or state
        return f"maintenance_{state.lower()}:{reasons}"
    return None
