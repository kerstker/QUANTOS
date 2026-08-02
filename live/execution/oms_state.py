"""live OMS 的 venue 状态 →  canonical 状态机适配层(Phase 3 gap,纯函数、无 IO)。

canonical 状态机的唯一真源是 `quantos/contracts/order.py`(blueprint §7);本模块只做两件事:

1. `canonical_for_venue_status` —— 把 `live/` 里实际落进 `LiveLedger` 的每一个 venue 原始
   状态字符串显式映射到 canonical `OrderState`。**未知字符串 raise ValueError(fail-closed,
   绝不猜)**——venue 新增一个我们不认识的状态时,宁可让记账失败、走对账/人工,也不静默记一个
   编造的 canonical 状态。

2. `assert_snapshot_transition` —— 账本快照语义下的 canonical 转换校验。**账本是粗粒度
   快照,venue 才是订单状态的权威**:一次 `update_status` 可能把上次快照之后的好几个
   canonical 中间态一次跳过(例:pre-journal 的 PENDING_NEW→RISK_APPROVED 之后,venue ACK
   直接回 FILLED——订单物理上经过了 SUBMITTED,只是账本从没记过那一行)。因此本校验要求的是
   **可达性**(新状态能从旧状态经 ALLOWED_TRANSITIONS 零跳或多跳到达),而不是单跳相邻;
   同状态重写(幂等重放)是 no-op。**不可达的转换照样拒绝**(FILLED→NEW、任何终态改写、
   已知部分成交倒退成 UNKNOWN)——`quantos/contracts/order.py` 的 ALLOWED_TRANSITIONS
   本身一字未动。

3. `ReconciliationStatus` / `reconciliation_status` —— 把 blueprint §16 的
   "RECONCILIATION_REQUIRED" 状态显式化:`reconcile_positions` 的 report dict → 枚举。
    remediation 维持 report-only / preflight hard-stop,**本模块不做任何自动修正**(对账漂移
   是留给人/ops 的信号,见 reconciliation.py 模块 docstring)。

映射表(每一个字符串都 grep 自 `live/` + 驱动器 `tools/` 的真实写入点):

| venue 字符串            | canonical        | 理由 |
|-------------------------|------------------|------|
| PENDING_NEW             | RISK_APPROVED    | pre-journal:已过 gate+过滤器+mandate AUTO,**尚未提交 venue**。
|                         |                  | 绝不映射 SUBMITTED——pre-journal 与 place_order 之间崩溃留下的行
|                         |                  | 必须不谎称 venue 见过这笔单(这正是 pre-journal 存在的意义,
|                         |                  | live/pipeline.py §420 注释 / emergency.py §151)。 |
| NEW                     | SUBMITTED        | venue 已受理、挂单工作中。 |
| PARTIALLY_FILLED        | PARTIALLY_FILLED | 1:1。 |
| FILLED                  | FILLED           | 1:1,终态。 |
| CANCELED                | CANCELLED        | venue 美式拼写 → canonical 英式拼写,终态。 |
| REJECTED                | REJECTED         | **venue 侧**拒绝(发生在提交之后),终态;区别于 canonical
|                         |                  | RISK_REJECTED(本地风控门拒绝,从未提交)——两个权威不同。 |
| EXPIRED                 | EXPIRED          | 1:1,终态。 |
| PREVIEW_NOT_SUBMITTED   | FAILED           | 干跑 preview 的终态标记(tools/live_track_advance.py 的
|                         |                  | PreviewVenue):订单**从未到达任何 venue、永不执行**。
|                         |                  | CANCELLED 会谎称"一个真实挂单被撤销",REJECTED 会谎称
|                         |                  | "venue 拒绝了它"——FAILED 是诚实的"从未执行"终态。 |
| UNKNOWN                 | SUBMITTED        | 适配器自造的诚实非终态回退(bybit cancel_order 只回
|                         |                  | {orderId, orderLinkId};各适配器 `data.get("status","UNKNOWN")`)——
|                         |                  | 订单已提交、当前状态确实未知。从 RISK_APPROVED 直达合法,
|                         |                  | 从 SUBMITTED 是同态 no-op;从 PARTIALLY_FILLED 倒退则
|                         |                  | 正确地 fail-closed(账本拒绝把已知部分成交倒退成"未知")。 |
"""
from __future__ import annotations

from enum import Enum

from quantos.contracts.order import (ALLOWED_TRANSITIONS, InvalidOrderTransition,
                                     OrderState)

_VENUE_STATUS_TO_CANONICAL = {
    "PENDING_NEW": OrderState.RISK_APPROVED,
    "NEW": OrderState.SUBMITTED,
    "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED,
    "FILLED": OrderState.FILLED,
    "CANCELED": OrderState.CANCELLED,
    "REJECTED": OrderState.REJECTED,
    "EXPIRED": OrderState.EXPIRED,
    "PREVIEW_NOT_SUBMITTED": OrderState.FAILED,
    "UNKNOWN": OrderState.SUBMITTED,
}


def canonical_for_venue_status(status: str) -> OrderState:
    """venue 原始状态字符串 → canonical `OrderState`。未知字符串 raise ValueError
    (fail-closed,绝不猜)——见模块 docstring 的映射表与逐条理由。"""
    try:
        return _VENUE_STATUS_TO_CANONICAL[status]
    except KeyError:
        raise ValueError(f"unknown venue order status: {status!r}") from None


def _reachable(src: OrderState, dst: OrderState) -> bool:
    """dst 能否从 src 经 ALLOWED_TRANSITIONS 一跳或多跳到达(广度优先,终态天然无出边)。"""
    seen, frontier = {src}, [src]
    while frontier:
        nxt = []
        for state in frontier:
            for target in ALLOWED_TRANSITIONS.get(state, ()):
                if target == dst:
                    return True
                if target not in seen:
                    seen.add(target)
                    nxt.append(target)
        frontier = nxt
    return False


def assert_snapshot_transition(prev: OrderState, new: OrderState) -> None:
    """账本快照转换校验:同状态重写(幂等重放)是 no-op;否则要求 new 从 prev **可达**
    (快照可以跳过中间态——venue 是权威,账本只是粗粒度记录——但绝不许倒退或改写终态)。
    不可达 -> raise `InvalidOrderTransition`。"""
    if prev == new:
        return
    if not _reachable(prev, new):
        raise InvalidOrderTransition(prev, new)


class ReconciliationStatus(str, Enum):
    """blueprint §16 的对账状态,显式化。remediation 维持 report-only/hard-stop,
    本枚举不附带任何自动修正语义。"""

    IN_SYNC = "IN_SYNC"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


def reconciliation_status(report: dict) -> ReconciliationStatus:
    """`reconcile_positions`/`preflight_check` 的 report dict → `ReconciliationStatus`。
    fail-closed:report 缺 `in_sync` 键 -> KeyError(畸形的对账报告不许被读成"一致")。"""
    if report["in_sync"]:
        return ReconciliationStatus.IN_SYNC
    return ReconciliationStatus.RECONCILIATION_REQUIRED
