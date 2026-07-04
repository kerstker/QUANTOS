"""管线编排(Stage 1)。设计:docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.7。

决策(权重)→ 缩放整数 intents → `PreTradeRiskGate`(复用既有 9 引擎风控,零重造)→ 交易所过滤器 →
mandate 分级 → AUTO 提交 venue / NEEDS_HUMAN_REVIEW 留存不提交 → ledger 记录 → 审计
(`GlobalAuditLog`,复用)。墙钟(now/decided_ts)由调用方边界注入,本文件无内部墙钟/RNG——与仓库
既有编排器(`services/execution/dry_run_pipeline.py`)的惯例一致。

**缩放整数,为何:** `OrderIntent.quantity` 是 `int`(风控门货币,见 `live/contracts.py` 顶部
说明);小资金真实分数仓位(如 0.015 BTC)必须是浮点。本文件用 `UNIT_SCALE`(1 unit = 1e-6 coin)
把真实浮点权重换算成缩放整数喂 gate,gate 批准后再换算回真实浮点、经交易所过滤器取整、才提交给
venue——缩放只存在于 gate 出入这一段,`live/execution/*` 全程浮点、不知道缩放这回事。
`ctx_factory` 由调用方装配(镜像既有编排器"核心不硬编码账户/安全装配"的原则),调用方须保证
`MarketContext.reference_price = 真实价格 / unit_scale`,使 `intent.quantity × reference_price`
落在真实美元名义额上——这是本文件唯一要求调用方遵守的约定,已在 spec 与本文件测试里钉死。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Dict, Mapping, Optional, Tuple

from live.exchange.base import VenueAdapter
from live.execution.filters import apply_exchange_filters
from live.execution.position_ledger import LiveLedger
from live.governance.mandate import AUTO, Mandate, evaluate_mandate
from services.execution.dry_run_pipeline import screen_intents
from services.execution.intent_builder import build_intents
from services.monitoring.audit_log import GlobalAuditLog

UNIT_SCALE = 1_000_000     # 1 unit = 1e-6 coin;仅 gate 出入段使用,见模块 docstring


def _det_id(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def plan_scaled_targets(weights: Mapping[str, float], prices: Mapping[str, float], aum: float,
                        *, unit_scale: int = UNIT_SCALE) -> Dict[str, int]:
    """真实权重 -> 缩放整数目标(1 unit = 1/unit_scale 个币),供 gate/build_intents 使用。
    价缺失/≤0 跳过(fail-soft);0 units 不下意图。镜像
    services.execution.dry_run_pipeline.plan_target_quantities,只多一层缩放。"""
    out: Dict[str, int] = {}
    for inst, w in weights.items():
        px = prices.get(inst)
        if px is None or px <= 0:
            continue
        units = int(round(w * aum / px * unit_scale))
        if units != 0:
            out[inst] = units
    return out


@dataclass(frozen=True)
class LiveBatchResult:
    tier: str
    submitted: Tuple = ()
    pending_review: Tuple = ()
    rejected_by_gate: Tuple = ()
    dropped_by_filters: Tuple = ()
    submission_errors: Tuple = ()
    mandate_reasons: Tuple = field(default_factory=tuple)


def submit_live_batch(*, weights: Mapping[str, float], prices: Mapping[str, float], aum: float,
                      current_positions: Mapping[str, float], gate, ctx_factory: Callable,
                      venue: VenueAdapter, mandate: Optional[Mandate], ledger: LiveLedger,
                      audit: GlobalAuditLog, strategy_id: str, decided_ts: int, now: int,
                      unit_scale: int = UNIT_SCALE) -> LiveBatchResult:
    run_id = _det_id(strategy_id, decided_ts, tuple(sorted(weights.items())))
    audit.append(event_id=_det_id("live_start", run_id), actor=f"live:{strategy_id}",
                action="LIVE_BATCH_START", timestamp=now, target=run_id,
                metadata={"aum": str(aum), "n_weights": str(len(weights))})

    scaled_targets = plan_scaled_targets(weights, prices, aum, unit_scale=unit_scale)
    scaled_current = {inst: int(round(qty * unit_scale)) for inst, qty in current_positions.items()}
    intents = build_intents(scaled_targets, scaled_current, strategy_id=strategy_id)
    screened = screen_intents(intents, gate=gate, ctx_factory=ctx_factory)
    for intent, reasons in screened["rejected"]:
        audit.append(event_id=_det_id("live_rej", run_id, intent.instrument_id),
                     actor=f"live:{strategy_id}", action="LIVE_INTENT_REJECTED", timestamp=now,
                     target=intent.instrument_id, metadata={"reasons": ",".join(reasons)})

    desired = [(intent.instrument_id, intent.side, intent.quantity / unit_scale)
              for intent, _ref in screened["approved"]]
    filters = venue.exchange_filters()
    adjusted, dropped = apply_exchange_filters(desired, filters, prices,
                                               strategy_id=strategy_id, decided_ts=decided_ts)
    for instrument_id, reason in dropped:
        audit.append(event_id=_det_id("live_drop", run_id, instrument_id), actor=f"live:{strategy_id}",
                     action="LIVE_ORDER_DROPPED_BY_FILTER", timestamp=now, target=instrument_id,
                     metadata={"reason": reason})

    decision = evaluate_mandate(adjusted, prices, mandate, venue=venue.venue_mode, now=now,
                               current_positions=current_positions)
    audit.append(event_id=_det_id("live_mandate", run_id), actor=f"live:{strategy_id}",
                action="LIVE_MANDATE_DECISION", timestamp=now, target=run_id,
                metadata={"tier": decision.tier, "reasons": ",".join(decision.reasons)})

    if decision.tier != AUTO:
        for o in adjusted:
            audit.append(event_id=_det_id("live_pending", run_id, o.instrument_id),
                         actor=f"live:{strategy_id}", action="LIVE_ORDER_PENDING_HUMAN_REVIEW",
                         timestamp=now, target=o.instrument_id,
                         metadata={"quantity": str(o.quantity), "side": o.side.value})
        return LiveBatchResult(
            tier=decision.tier, pending_review=tuple(adjusted),
            rejected_by_gate=tuple(screened["rejected"]), dropped_by_filters=tuple(dropped),
            mandate_reasons=decision.reasons)

    submitted, errors = [], []
    for o in adjusted:
        try:
            result = venue.place_order(o)
            ledger.record_submission(o.client_order_id, o.instrument_id, o.side, o.quantity,
                                     submitted_at=now, venue_order_id=result.venue_order_id,
                                     status=result.status)
            if result.filled_qty > 0:
                ledger.record_fill(_det_id("live_fill", o.client_order_id), o.client_order_id,
                                   o.instrument_id, o.side, result.filled_qty, result.avg_price,
                                   filled_at=now)
            audit.append(event_id=_det_id("live_submit", run_id, o.instrument_id),
                         actor=f"live:{strategy_id}", action="LIVE_ORDER_SUBMITTED", timestamp=now,
                         target=o.instrument_id,
                         metadata={"quantity": str(o.quantity), "side": o.side.value,
                                   "venue_order_id": result.venue_order_id, "status": result.status})
            submitted.append(result)
        except Exception as exc:
            errors.append((o, f"{type(exc).__name__}: {exc}"))
            audit.append(event_id=_det_id("live_submit_err", run_id, o.instrument_id),
                         actor=f"live:{strategy_id}", action="LIVE_ORDER_SUBMIT_FAILED", timestamp=now,
                         target=o.instrument_id, metadata={"error": type(exc).__name__})

    return LiveBatchResult(
        tier=AUTO, submitted=tuple(submitted), rejected_by_gate=tuple(screened["rejected"]),
        dropped_by_filters=tuple(dropped), submission_errors=tuple(errors))
