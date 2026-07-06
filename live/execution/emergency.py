"""紧急减仓独立通道(2026-07-05,用户拍板"独立紧急通道"方案)。设计:
docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md §②。

**为什么独立:** kill switch 一旦触发,共用 `PreTradeRiskGate.check_kill_switch` 会挡下包括
reduce-only 在内的所有意图——越紧急越送不出去。机构标准是"紧急去风险不能被'禁止开新仓'的
开关挡住"。本模块因此**刻意不 import、不咨询** PreTradeRiskGate/KillSwitch(治理测试钉死),
用一组结构性窄校验替代:

1. 方向必须与当前持仓相反(多仓只能卖、空仓只能买);
2. 数量 ≤ |当前持仓|(减到零可以,穿过零点反向开仓绝对不行);
3. 订单强制 `reduce_only=True`——适配器把交易所原生 reduceOnly 参数一并传出,交易所侧
   第二重保证;
4. 无持仓/数量非正 -> 拒绝。
任何一条不满足即拒绝该笔(拒绝也审计)。这组校验保证:**这条通道在结构上只可能减少敞口**。

**过滤器 fail-open(刻意与加仓路径相反的不对称):** `exchange_filters()` 拿不到时,加仓
路径 fail-closed(不下单最安全);这里相反——对减仓单来说"不提交"才是危险动作(敞口继续
暴露),所以提交原始数量,交易所自己是最终校验者(被拒也只是保持现状,不比不试更糟)。

诚实边界(与全库同一威胁模型注脚):这些是运行时结构约束 + 审计,单进程内挡不住受信代码
绕过本模块直接调 venue.place_order——那是 Phase 14 进程隔离的范畴。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Mapping, Optional, Sequence, Tuple

from live.execution.filters import apply_exchange_filters
from live.execution.position_ledger import LiveLedger
from live.execution.risk_monitor import RiskBreach
from live.exchange.base import VenueAdapter
from live.pipeline import (DEFAULT_MAX_CONFIRM_POLLS, _cancel_and_requery, _confirm_fill,
                           _det_id, _record_tca_failsoft)
from quantos.contracts.trading import OrderSide
from services.monitoring.audit_log import GlobalAuditLog

_EXPOSURE_BREACH_MARGIN = 0.9      # 减回到限额的 90%,留余量防止下一轮立刻再次越限
_QTY_EPS = 1e-12                   # 浮点表示噪声容差(不改变"绝不超量"的方向)
# 紧急减仓的保留账本(2026-07-06 战线③):与既有 audit actor "live:emergency_flatten" 同词,
# 不造新词。紧急减仓作用于**账户级** venue 仓位,成交无法按策略归属(也不该在紧急时刻做归属
# 决策)——记入保留账本后:账户级 `all_positions()` 立即正确(含减仓效果),各策略分账仍显示
# "该策略以为自己持有的仓位",差额全部落在本账本里——分区恒等式不破、对账不破。事后重归属
# (按比例/人工判断)是事故复盘的人工决定,不自动做(设计文档 §9 留白)。
EMERGENCY_STRATEGY_ID = "emergency_flatten"


def validate_flatten_order(side: OrderSide, qty: float, *, position: float) -> Optional[str]:
    """结构性"只能减仓"校验(纯函数)。放行(返回 None)当且仅当:数量为正、当前有持仓、
    方向与持仓相反、且数量不超过 |持仓|(减到零允许,穿过零点反向开仓禁止)。
    否则返回拒绝原因字符串。"""
    if qty <= 0:
        return f"non_positive_qty:{qty}"
    if position == 0.0:
        return "no_position_to_reduce"
    reduces_long = position > 0 and side == OrderSide.SELL
    reduces_short = position < 0 and side == OrderSide.BUY
    if not (reduces_long or reduces_short):
        return f"side_does_not_oppose_position:side={side.value},position={position}"
    if qty > abs(position) + _QTY_EPS:
        return f"qty_exceeds_position:{qty}>{abs(position)}"
    return None


def flatten_target_for_breach(breach: RiskBreach, *, max_gross_exposure: float,
                              max_net_exposure: float) -> Optional[float]:
    """越限类型 -> 自动减仓的目标 gross 敞口(纯函数策略)。None = 本次不自动减仓。

    - 回撤越限:全部平掉(target 0)——权益保护是最高优先级,留着仓位只会继续放大回撤。
    - gross/净敞口越限:等比缩减到限额的 90%(等比缩减下 net 与 gross 同因子缩,取两个
      约束里更紧的那个因子)。
    - 只有数据质量问题(缺价/峰值未知):**不**自动减仓——在不可信的数据上自动交易可能是
      错误方向的动作;保守停机 + 告警,交给人。
    """
    if not breach.breached:
        return None
    has_drawdown = any(r.startswith("drawdown_exceeded") for r in breach.reasons)
    has_gross = any(r.startswith("gross_exposure_exceeded") for r in breach.reasons)
    has_net = any(r.startswith("net_exposure_exceeded") for r in breach.reasons)
    if has_drawdown:
        return 0.0
    if not (has_gross or has_net):
        return None                                    # 纯数据质量问题,不自动交易
    factor = 1.0
    if has_gross and breach.gross_exposure > 0:
        factor = min(factor, _EXPOSURE_BREACH_MARGIN * max_gross_exposure / breach.gross_exposure)
    if has_net and abs(breach.net_exposure) > 0:
        factor = min(factor, _EXPOSURE_BREACH_MARGIN * max_net_exposure / abs(breach.net_exposure))
    return breach.gross_exposure * factor


@dataclass(frozen=True)
class EmergencyFlattenResult:
    submitted: Tuple = ()
    rejected: Tuple = ()              # ((instrument_id, side, qty, reason), ...)
    errors: Tuple = ()
    ledger_write_errors: Tuple = ()


def submit_emergency_flatten(*, flatten_orders: Sequence[Tuple[str, OrderSide, float]],
                             positions: Mapping[str, float], prices: Mapping[str, float],
                             venue: VenueAdapter, ledger: LiveLedger, audit: GlobalAuditLog,
                             now: int, max_confirm_polls: int = DEFAULT_MAX_CONFIRM_POLLS,
                             poll_sleep=lambda: None) -> EmergencyFlattenResult:
    """把 `compute_flatten_orders` 的输出真正提交出去(经窄校验 + 交易所过滤器 + reduce-only
    强制 + 轮询确认/超时撤单 + 记账 + 审计)。`positions` 必须是**权威当前持仓**(来自
    venue.positions()),校验以它为准。"""
    run_id = _det_id("emergency_flatten", now, tuple(sorted(positions.items())))
    audit.append(event_id=_det_id("emg_start", run_id), actor="live:emergency_flatten",
                action="EMERGENCY_FLATTEN_START", timestamp=now, target=run_id,
                metadata={"n_orders": str(len(flatten_orders))})

    # 1) 结构性窄校验(fail-closed:任何一条不满足即拒绝该笔,拒绝也审计)
    validated: List[Tuple[str, OrderSide, float]] = []
    rejected: List[Tuple[str, str, float, str]] = []
    for instrument_id, side, qty in flatten_orders:
        reason = validate_flatten_order(side, qty, position=positions.get(instrument_id, 0.0))
        if reason is not None:
            rejected.append((instrument_id, side.value, qty, reason))
            audit.append(event_id=_det_id("emg_rej", run_id, instrument_id),
                         actor="live:emergency_flatten", action="EMERGENCY_ORDER_REJECTED",
                         timestamp=now, target=instrument_id, metadata={"reason": reason})
            continue
        validated.append((instrument_id, side, qty))

    if not validated:
        return EmergencyFlattenResult(rejected=tuple(rejected))

    # 2) 交易所过滤器(fail-open,见模块 docstring 的刻意不对称)
    try:
        filters = venue.exchange_filters()
        adjusted, _dropped = apply_exchange_filters(validated, filters, prices,
                                                    strategy_id=EMERGENCY_STRATEGY_ID,
                                                    decided_ts=now)
    except Exception as exc:
        audit.append(event_id=_det_id("emg_filters_err", run_id), actor="live:emergency_flatten",
                     action="EMERGENCY_FILTERS_UNAVAILABLE", timestamp=now, target=run_id,
                     metadata={"error": type(exc).__name__})
        from live.execution.filters import client_order_id as _cid
        adjusted = [replace(
            __req, reduce_only=True) for __req in (
            # 过滤器不可用 -> 原始数量直接构造请求,交易所是最终校验者
            _raw_request(inst, side, qty, _cid(EMERGENCY_STRATEGY_ID, now, inst, side, qty))
            for inst, side, qty in validated)]
    else:
        adjusted = [replace(o, reduce_only=True) for o in adjusted]    # 强制 reduce-only

    # 3) 提交 + 确认(镜像主流水线的全部教训:异常回查、轮询终态、超时撤单、记账失败单独追踪)
    submitted, errors, ledger_errors = [], [], []
    for o in adjusted:
        result = None
        try:
            result = venue.place_order(o)
        except Exception as exc:
            try:
                result = venue.get_order(o.instrument_id, o.client_order_id)
            except Exception:
                result = None
            if result is None:
                errors.append((o, f"{type(exc).__name__}: {exc}"))
                audit.append(event_id=_det_id("emg_submit_err", run_id, o.instrument_id),
                             actor="live:emergency_flatten", action="EMERGENCY_ORDER_SUBMIT_FAILED",
                             timestamp=now, target=o.instrument_id,
                             metadata={"error": type(exc).__name__})
                continue

        confirmed, is_terminal = _confirm_fill(venue, o, result, max_polls=max_confirm_polls,
                                               poll_sleep=poll_sleep)
        if not is_terminal:
            confirmed, is_terminal, outcome = _cancel_and_requery(
                venue, o.instrument_id, o.client_order_id, confirmed)
            audit.append(event_id=_det_id("emg_timeout_cancel", run_id, o.instrument_id),
                         actor="live:emergency_flatten", action="EMERGENCY_ORDER_TIMEOUT_CANCEL",
                         timestamp=now, target=o.instrument_id,
                         metadata={"outcome": outcome, "status": confirmed.status})

        try:
            ledger.record_submission(o.client_order_id, o.instrument_id, o.side, o.quantity,
                                     strategy_id=EMERGENCY_STRATEGY_ID, submitted_at=now,
                                     venue_order_id=confirmed.venue_order_id,
                                     status=confirmed.status)
            if confirmed.filled_qty > 0:
                ledger.record_fill(_det_id("emg_fill", o.client_order_id), o.client_order_id,
                                   o.instrument_id, o.side, confirmed.filled_qty,
                                   confirmed.avg_price, strategy_id=EMERGENCY_STRATEGY_ID,
                                   filled_at=now)
                # 紧急减仓的滑点恰恰是最值得度量的执行成本(压力时刻的市场冲击)
                _record_tca_failsoft(ledger, audit, o, confirmed,
                                     decision_price=prices.get(o.instrument_id), run_id=run_id,
                                     strategy_id=EMERGENCY_STRATEGY_ID, now=now)
        except Exception as exc:
            ledger_errors.append((o, f"{type(exc).__name__}: {exc}"))
            audit.append(event_id=_det_id("emg_ledger_err", run_id, o.instrument_id),
                         actor="live:emergency_flatten", action="EMERGENCY_LEDGER_WRITE_FAILED",
                         timestamp=now, target=o.instrument_id,
                         metadata={"error": type(exc).__name__,
                                   "venue_order_id": confirmed.venue_order_id})

        audit.append(event_id=_det_id("emg_submit", run_id, o.instrument_id),
                     actor="live:emergency_flatten", action="EMERGENCY_ORDER_SUBMITTED",
                     timestamp=now, target=o.instrument_id,
                     metadata={"quantity": str(o.quantity), "side": o.side.value,
                               "status": confirmed.status, "reduce_only": "True"})
        submitted.append(confirmed)

    return EmergencyFlattenResult(submitted=tuple(submitted), rejected=tuple(rejected),
                                  errors=tuple(errors), ledger_write_errors=tuple(ledger_errors))


def _raw_request(instrument_id: str, side: OrderSide, qty: float, cid: str):
    from live.contracts import LiveOrderRequest
    return LiveOrderRequest(instrument_id=instrument_id, side=side, quantity=qty,
                            client_order_id=cid)
