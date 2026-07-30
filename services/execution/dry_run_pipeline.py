"""端到端干跑流水线编排器(薄、确定性)。

数据流:权重 → 目标整数手数 → build_intents → 逐意图过预交易门(运行时 chokepoint,fail-closed)+
kill_switch → 可选模拟 SOR 分片 → 干跑适配器受理(不真发)→ 纸面成交 → reconcile/PnL
→ 哈希链审计 → DryRunRun。

容器化(L2,不可让):五个 LIVE 标志恒 false、门 fail-closed、kill_switch 先查、干跑适配器无券商 SDK/
不真发、无网络 import、编排器绝不进 research_runner、核心无 RNG/无墙钟(timestamp/seed 由调用方边界注入)。
RiskContext 由注入的 ctx_factory 装配(核心不硬编码账户/安全装配),保持可测、可复用。
实盘 = 同接口换真券商适配器 + 人工批准 = 显式非目标。
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from decimal import Decimal
from typing import Callable, Dict, Optional, Sequence, Tuple

from quantos.contracts.risk import RiskDecisionType
from services.execution.dry_run_store import DryRunRun
from services.execution.events import (
    dry_run_completed_event,
    dry_run_started_event,
    pretrade_decision_event,
)
from services.execution.intent_builder import build_intents
from services.execution.paper_reconciliation import reconcile
from services.execution.quantization import meets_min_notional, round_to_step
from services.execution.simulated_sor import (
    RouteStatus,
    SimulatedRoutePlan,
    validate_simulated_route_plan,
)
from services.execution.simulated_tca import RouteExecution, build_route_tca
from services.monitoring.audit_log import GlobalAuditLog
from quantos.contracts.paper import FillMethod, OrderSide as PaperSide, PaperFill
from services.trading.broker_adapter import assert_dry_run_adapter
from services.trading.dry_run_adapters.domain.models import DryRunCommand
from services.risk.research_risk.dry_run_guard import (
    DryRunResearchRiskInputs,
    evaluate_dry_run_risk,
    full_research_risk_approved,
)
from services.risk.research_risk.domain.models import RiskConfig
from services.risk.research_risk.events import research_risk_evaluated_event


def _det_id(*parts) -> str:
    """Deterministic short id from parts (no RNG/clock) — keeps the whole pipeline reproducible."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _research_risk_identity(
    inputs: Optional[DryRunResearchRiskInputs],
    config: Optional[RiskConfig],
    require_full: bool,
) -> str:
    """Return a deterministic run-id suffix for risk evidence.

    A run snapshot includes its research-risk decision.  If callers submit a
    different evidence bundle for the same strategy/period, that must produce a
    different immutable ``DryRunRun`` instead of colliding in the append-only
    store.  The default legacy subset path intentionally has an empty suffix so
    existing dry-run identifiers remain stable.
    """
    # The historical subset path without an explicit config keeps its stable
    # identifiers.  An explicit config (for example weekly ppy=52) is part of
    # the evidence identity even when the seam still has only its honest
    # four-engine inputs; otherwise a cadence correction would collide with
    # the prior immutable run and its risk event.
    if inputs is None and not require_full and config is None:
        return ""
    payload = {
        "require_full": bool(require_full),
        "config": (config or RiskConfig()).compute_hash(),
    }
    if isinstance(inputs, DryRunResearchRiskInputs):
        try:
            payload["inputs"] = {
                "capacity_cny": dict(sorted(inputs.capacity_cny.items(), key=lambda item: str(item[0]))),
                "daily_volume_cny": inputs.daily_volume_cny,
                "total_aum": inputs.total_aum,
                "alpha_returns": {
                    str(key): list(value)
                    for key, value in sorted(inputs.alpha_returns.items(), key=lambda item: str(item[0]))
                },
                "regime_labels": list(inputs.regime_labels),
                "alpha_metadata": [dict(item) for item in inputs.alpha_metadata],
                "timestamps": (
                    list(inputs.timestamps) if inputs.timestamps is not None else None
                ),
            }
        except Exception:
            # The evaluator will return a strict PENDING result for malformed
            # evidence; identity generation must not turn that into an exception.
            payload["inputs_type"] = "INVALID_DRY_RUN_RESEARCH_RISK_INPUTS"
    elif inputs is not None:
        payload["inputs_type"] = type(inputs).__name__
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:16]


def _evaluate_research_risk(
    *,
    returns: Sequence[float],
    weights: Dict[str, float],
    equity_curve: Sequence[float],
    timestamps: Optional[Sequence[int]],
    inputs: Optional[DryRunResearchRiskInputs],
    config: Optional[RiskConfig],
    require_full: bool,
    series: str,
) -> dict:
    """Evaluate and normalize the research-risk snapshot consumed by a dry run.

    Providing a full input bundle always selects the strict nine-engine path;
    callers cannot accidentally downgrade it with ``require_full=False``.  A
    missing bundle under explicit strict mode remains a pending/rejected result.
    Any unexpected evaluator failure is converted to a deterministic pending
    result so execution never treats an unavailable risk decision as approval.
    The result is explicitly post-run portfolio evidence; it does not replace
    the per-intent ``PreTradeRiskGate``.
    """
    strict = bool(require_full or inputs is not None)
    effective_config = config or RiskConfig()
    try:
        risk = evaluate_dry_run_risk(
            list(returns),
            dict(weights),
            list(equity_curve),
            timestamps=list(timestamps) if timestamps is not None else None,
            config=effective_config,
            full_inputs=inputs,
            require_full_inputs=strict,
        )
    except Exception as exc:  # fail-closed: no risk result is ever an approval
        risk = {
            "decision": "RISK_REJECTED",
            "status": "PENDING",
            "gates_passed": 0,
            "gates_run": 0,
            "engines_run": {},
            "engines_skipped": {},
            "engines_pending": {},
            "reports": {},
            "reasons": [f"research_risk_error:{type(exc).__name__}"],
            "provenance": "REAL_RESEARCH_RISK_FULL_PENDING" if strict else "REAL_RESEARCH_RISK_SUBSET",
            "config_provenance": effective_config.compute_hash(),
        }
    snapshot = dict(risk)
    snapshot["series"] = str(series)
    snapshot["input_mode"] = "FULL" if strict else "SUBSET"
    # This is a portfolio-level, post-run evidence gate.  The per-intent
    # PreTradeRiskGate remains the execution chokepoint and is intentionally
    # separate; consumers must not interpret this snapshot as order admission.
    snapshot["scope"] = "POST_RUN_PORTFOLIO_EVIDENCE"
    return snapshot


def plan_target_quantities(weights: Dict[str, float], prices: Dict[str, float], aum: float,
                           filters: Optional[Dict[str, Tuple[float, float, float]]] = None
                           ) -> Dict[str, int]:
    """权重 → 目标整数手数:round(w * aum / price)。价缺失/≤0 跳过(fail-soft);0 手不下意图。

    `filters`(可选,F10 审计——小资金规模下交易所步长/最小名义额不可忽略近似):
    `{instrument_id: (step_size, min_qty, min_notional)}`。传入且该标的有对应条目时,目标数量先
    向下取整到 `step_size` 网格(`round_to_step`,绝不超发),再过 `min_qty`/`min_notional` 门槛;
    不达标(含取整到 0)的标的这一期被诚实排除(不进 `out`)——与既有"价格缺失/≤0 -> 跳过"同一
    fail-soft 语义,不因单一标的数据不全/量太小而中断整批。`filters` 里没有该标的的条目时直通、
    不做任何量化(数据不全不阻塞)。`filters=None`(默认)-> 输出与改动前逐字节相同。"""
    out: Dict[str, int] = {}
    for inst, w in weights.items():
        px = prices.get(inst)
        if px is None or px <= 0:
            continue
        raw = w * aum / px
        filt = filters.get(inst) if filters else None
        if filt is None:
            q = int(round(raw))
        else:
            step_size, min_qty, min_notional = filt
            sign = -1.0 if raw < 0 else 1.0
            rounded_abs = round_to_step(abs(raw), step_size)
            if rounded_abs <= 0 or rounded_abs < min_qty or not meets_min_notional(rounded_abs, px, min_notional):
                continue
            q = int(round(sign * rounded_abs))
        if q != 0:
            out[inst] = q
    return out


def _reason_str(r) -> str:
    return getattr(r, "value", None) or str(r)


def screen_intents(intents: Sequence, *, gate, ctx_factory: Callable) -> dict:
    """运行时 chokepoint:逐意图装配 RiskContext → gate.evaluate → fail-closed 分流。

    仅门 APPROVED 的意图进 approved(带其 intent-bound risk ref);任何非批准 / ctx 装配异常 → rejected
    (附原因)。门本身已 fail-closed(内部异常恒返回 REJECTED);外层 try 再兜 ctx_factory 异常。返回
    {"approved": [(intent, ref), ...], "rejected": [(intent, [reason, ...]), ...]}。
    """
    approved, rejected = [], []
    for intent in intents:
        try:
            ctx = ctx_factory(intent)
            decision = gate.evaluate(intent, ctx)
            if getattr(decision, "decision", None) == RiskDecisionType.APPROVED:
                approved.append((intent, gate.issue_ref(decision, intent, ctx)))
            else:
                reasons = [_reason_str(r) for r in (getattr(decision, "reasons", ()) or ())]
                rejected.append((intent, reasons or ["REJECTED"]))
        except Exception as exc:  # fail-closed:ctx 装配 / 评估异常 -> 拒绝,绝不放行
            rejected.append((intent, [f"GATE_ERROR:{type(exc).__name__}"]))
    return {"approved": approved, "rejected": rejected}


def _publish_screening_events(
    *, event_bus, screened: dict, run_id: str, strategy_id: str,
    timestamp: int, scope: str, causation_id: str,
) -> None:
    for index, (intent, _) in enumerate(screened["approved"]):
        event = pretrade_decision_event(
            run_id=run_id,
            strategy_id=strategy_id,
            timestamp_ms=int(timestamp),
            scope=f"{scope}:approved:{index}",
            instrument_id=intent.instrument_id,
            side=intent.side.value,
            quantity=int(intent.quantity),
            approved=True,
        )
        event_bus.publish_once(
            event, correlation_id=run_id, causation_id=causation_id
        )
    for index, (intent, reasons) in enumerate(screened["rejected"]):
        event = pretrade_decision_event(
            run_id=run_id,
            strategy_id=strategy_id,
            timestamp_ms=int(timestamp),
            scope=f"{scope}:rejected:{index}",
            instrument_id=intent.instrument_id,
            side=intent.side.value,
            quantity=int(intent.quantity),
            approved=False,
            reasons=reasons,
        )
        event_bus.publish_once(
            event, correlation_id=run_id, causation_id=causation_id
        )


def _to_command(intent, ref: str, price: float, *, idx, run_id: str) -> DryRunCommand:
    """OrderIntent + risk ref + price -> 干跑命令。恒 dry_run=True / live_trading_allowed=False;
    oms_state_ref 取适配器允许集中的 "VALIDATED";risk/pre_trade ref 非空(适配器二次校验要求)。"""
    cid = _det_id(run_id, idx, intent.instrument_id)
    return DryRunCommand(
        command_id=cid, adapter_type="DRY_RUN_GENERIC", ems_run_id=run_id, parent_order_id=cid,
        child_order_id=cid, instrument_id=intent.instrument_id, side=intent.side.value,
        order_type="MARKET", quantity=abs(int(intent.quantity)), price=float(price),
        risk_approval_ref=ref or cid, pre_trade_risk_ref=ref or cid, oms_state_ref="VALIDATED",
        ems_state_ref="", kill_switch_ref="", idempotency_key=cid, checksum=_det_id("ck", cid),
        dry_run=True, live_trading_allowed=False)


def _route_summary(plan: SimulatedRoutePlan) -> dict:
    return {
        "instrument_id": plan.instrument_id,
        "side": plan.side.value,
        "status": plan.status.value,
        "requested_quantity": plan.requested_quantity,
        "routed_quantity": plan.routed_quantity,
        "residual_quantity": plan.residual_quantity,
        "expected_raw_notional": str(plan.expected_raw_notional),
        "expected_all_in_notional": str(plan.expected_all_in_notional),
        "expected_fees": str(plan.expected_fees),
        "expected_latency_penalty": str(plan.expected_latency_penalty),
        "slices": [
            {
                "venue_id": item.venue_id,
                "quantity": item.quantity,
                "raw_price": str(item.raw_price),
                "all_in_price": str(item.all_in_price),
                "fee_amount": str(item.fee_amount),
                "latency_penalty_amount": str(item.latency_penalty_amount),
                "quote_age_ms": item.quote_age_ms,
            }
            for item in plan.slices
        ],
    }


def simulate_fills(approved: Sequence, prices: Dict[str, float], *, adapter, run_id: str,
                   timestamp: int, cost_bps: float = 0.0, impact=None,
                   adv: Dict[str, float] = None, route_planner: Callable | None = None) -> dict:
    """已批准意图 → 干跑适配器受理(process→simulate_fill,绝不真发)→ 纸面成交 PaperFill。
    适配器是第二道闸:若它拒绝(rejection_reasons 非空)则不成交。

    成本(Rank #11):默认平 `cost_bps`。若传入 `impact`(AlmgrenChrissImpact)+ `adv`(instrument→ADV 名义),
    则该笔 commission = 参与率驱动的 Almgren-Chriss 冲击成本(临时平方根 + 永久线性);某标的缺 ADV 时**逐笔
    回退**平 bps。commission 在 reconcile 两侧同进同出,恒等式残差不受影响。确定性 id;不捕获适配器内部 uuid。"""
    assert_dry_run_adapter(adapter)  # fail-closed 接缝守卫:运行时只放行证明为干跑的适配器
    fills, reports, impact_trades = [], [], []
    routing, route_rejections, route_tca = [], [], []
    n_impact = n_flat = 0
    route_cost_total = 0.0
    n_adapter_rejected = 0
    for i, (intent, ref) in enumerate(approved):
        slices = None
        if route_planner is not None:
            try:
                plan = route_planner(intent)
            except Exception as exc:
                route_rejections.append({
                    "instrument_id": intent.instrument_id,
                    "reasons": [f"ROUTE_PLANNER_ERROR:{type(exc).__name__}"],
                })
                continue
            invalid = validate_simulated_route_plan(plan, intent)
            if invalid:
                route_rejections.append({
                    "instrument_id": intent.instrument_id,
                    "reasons": list(invalid),
                })
                continue
            routing.append(_route_summary(plan))
            if plan.status == RouteStatus.REJECTED:
                route_rejections.append({
                    "instrument_id": intent.instrument_id,
                    "reasons": ["ROUTE_REJECTED"],
                })
                continue
            reference_price = prices.get(intent.instrument_id)
            if reference_price is None or reference_price <= 0:
                route_rejections.append({
                    "instrument_id": intent.instrument_id,
                    "reasons": ["TCA_REFERENCE_PRICE_MISSING"],
                })
                continue
            slices = tuple(plan.slices)

        if slices is None:
            px = prices.get(intent.instrument_id)
            if px is None or px <= 0:
                route_rejections.append({
                    "instrument_id": intent.instrument_id,
                    "reasons": ["REFERENCE_PRICE_MISSING"],
                })
                continue
            execution_slices = ((None, int(intent.quantity), float(px), 0.0),)
        else:
            execution_slices = tuple(
                (
                    item.venue_id,
                    item.quantity,
                    float(item.raw_price),
                    float(item.fee_amount + item.latency_penalty_amount),
                )
                for item in slices
            )

        route_executions = []
        for j, (venue_id, qty, px, route_cost) in enumerate(execution_slices):
            child_intent = intent.model_copy(update={"quantity": int(qty)})
            child_index = i if venue_id is None else f"{i}:{j}:{venue_id}"
            run = adapter.process(
                _to_command(
                    child_intent,
                    ref,
                    px,
                    idx=child_index,
                    run_id=run_id,
                )
            )
            if run.rejection_reasons:  # 适配器二次校验拒 -> 不成交
                n_adapter_rejected += 1
                if run.normalized_report is not None:
                    reports.append(run.normalized_report)
                continue
            rep = adapter.simulate_fill(run.run_id)
            if rep is None:
                n_adapter_rejected += 1
                continue
            reports.append(rep)
            fp = float(rep.fill_price or px)
            notional = fp * qty
            adv_i = (
                adv.get(intent.instrument_id)
                if (impact is not None and adv is not None)
                else None
            )
            if adv_i is not None and adv_i > 0:
                base_cost = impact.total_cost_notional(notional, adv_i)
                impact_trades.append((notional, float(adv_i)))
                n_impact += 1
            else:
                base_cost = notional * cost_bps / 1e4
                n_flat += 1
            fills.append(PaperFill(
                fill_id=_det_id("f", run_id, child_index),
                order_id=_det_id("o", run_id, child_index),
                run_id=run_id,
                instrument_id=intent.instrument_id,
                side=PaperSide(intent.side.value),
                quantity=qty,
                price=fp,
                fill_method=FillMethod.NEXT_OPEN,
                timestamp=timestamp,
                commission=base_cost + route_cost,
                slippage=0.0,
            ))
            if slices is not None:
                route_executions.append(
                    RouteExecution(
                        slice_index=j,
                        venue_id=str(venue_id),
                        quantity=qty,
                        fill_price=Decimal(str(fp)),
                    )
                )
            route_cost_total += route_cost
        if slices is not None:
            route_tca.append(
                build_route_tca(
                    plan,
                    reference_price=Decimal(str(reference_price)),
                    executions=route_executions,
                )
            )
    return {"fills": fills, "reports": reports, "impact_trades": impact_trades,
            "n_impact": n_impact, "n_flat": n_flat, "routing": routing,
            "route_rejections": route_rejections, "route_tca": route_tca,
            "n_adapter_rejected": n_adapter_rejected,
            "route_cost_total": route_cost_total}


def reconcile_run(fills, marks: Dict[str, float], initial_capital: float) -> dict:
    """纸面成交 → 真实平均成本对账 PnL 摘要(realized+unrealized−成本,恒等式残差≈0)。"""
    res = reconcile(fills, marks=marks, initial_capital=initial_capital)
    return {
        "total_pnl": round(res.total_pnl, 6), "net_pnl": round(res.total_net_pnl, 6),
        "gross_pnl": round(res.gross_trading_pnl, 6), "costs": round(res.total_costs, 6),
        "equity": round(res.equity, 6), "turnover": round(res.turnover, 6),
        "residual": round(res.reconciliation_residual(), 9),
        "positions": {k: int(v) for k, v in res.positions.items()},
    }


def run_dry_run_pipeline(*, weights: Dict[str, float], prices: Dict[str, float],
                         current_positions: Dict[str, int], aum: float, gate, ctx_factory: Callable,
                         adapter, strategy_id: str, timestamp: int, marks: Dict[str, float] = None,
                         initial_capital: float = 1e7, cost_bps: float = 0.0,
                         filters: Optional[Dict[str, tuple[float, float, float]]] = None,
                         route_planner: Callable | None = None,
                         routing_snapshot_id: str = "",
                         event_bus=None, publish_completion_event: bool = True,
                         research_risk_inputs: Optional[DryRunResearchRiskInputs] = None,
                         research_risk_config: Optional[RiskConfig] = None,
                         require_full_research_risk: bool = False,
                         research_risk_returns: Optional[Sequence[float]] = None,
                         research_risk_equity_curve: Optional[Sequence[float]] = None,
                         research_risk_timestamps: Optional[Sequence[int]] = None,
                         publish_research_risk_event: Optional[bool] = None) -> DryRunRun:
    """端到端干跑编排器(薄、确定性):权重→目标手数→意图→门 chokepoint(fail-closed)→干跑受理(不真发)
    →纸面成交→对账→哈希链审计→DryRunRun。墙钟/seed 在调用方边界(timestamp/adapter)注入;此函数无 RNG/墙钟。
    kill_switch 由 gate 持有并在 evaluate 时绑定(latched→全意图被门拒、零成交)。
    route_planner 为可选研究/干跑 SOR；启用时必须提供内容绑定的 routing_snapshot_id。"""
    strict_research_risk = bool(require_full_research_risk or research_risk_inputs is not None)
    if (research_risk_returns is None) != (research_risk_equity_curve is None):
        raise ValueError(
            "research_risk_returns and research_risk_equity_curve must be provided together"
        )
    if research_risk_timestamps is not None and research_risk_returns is None:
        raise ValueError("research_risk_timestamps requires research_risk_returns")
    risk_identity = _research_risk_identity(
        research_risk_inputs, research_risk_config, strict_research_risk
    )
    if route_planner is not None and (
        not isinstance(routing_snapshot_id, str) or not routing_snapshot_id.strip()
    ):
        raise ValueError("routing_snapshot_id is required when route_planner is provided")
    if route_planner is None and routing_snapshot_id:
        raise ValueError("routing_snapshot_id requires route_planner")
    run_id_parts = [strategy_id, timestamp, tuple(sorted(weights.items()))]
    if filters is not None:
        run_id_parts.append(("exchange_filters", tuple(sorted(filters.items()))))
    if risk_identity:
        run_id_parts.append(risk_identity)
    if research_risk_returns is not None:
        run_id_parts.append(
            (
                "research_risk_series",
                tuple(float(value) for value in research_risk_returns),
                tuple(float(value) for value in research_risk_equity_curve or ()),
                (
                    tuple(int(value) for value in research_risk_timestamps)
                    if research_risk_timestamps is not None
                    else None
                ),
            )
        )
    if routing_snapshot_id:
        run_id_parts.append(("sor", routing_snapshot_id))
    run_id = _det_id(*run_id_parts)
    start_event_id = ""
    if event_bus is not None:
        start_event = dry_run_started_event(
            run_id=run_id,
            strategy_id=strategy_id,
            timestamp_ms=int(timestamp),
            mode="single_period",
        )
        event_bus.publish_once(start_event, correlation_id=run_id)
        start_event_id = start_event.event_id
    audit = GlobalAuditLog()
    # AuditEvent.metadata is dict[str,str] — every value MUST be a string.
    audit.append(event_id=_det_id("start", run_id), actor=f"dry_run:{strategy_id}", action="PIPELINE_START",
                 timestamp=timestamp, target=run_id,
                 metadata={"aum": str(aum), "n_weights": str(len(weights))})

    targets = plan_target_quantities(weights, prices, aum, filters=filters)
    intents = build_intents(targets, current_positions, strategy_id=strategy_id)
    screened = screen_intents(intents, gate=gate, ctx_factory=ctx_factory)
    if event_bus is not None:
        _publish_screening_events(
            event_bus=event_bus,
            screened=screened,
            run_id=run_id,
            strategy_id=strategy_id,
            timestamp=int(timestamp),
            scope="single",
            causation_id=start_event_id,
        )
    for intent, reasons in screened["rejected"]:
        audit.append(event_id=_det_id("rej", run_id, intent.instrument_id), actor=f"dry_run:{strategy_id}",
                     action="INTENT_REJECTED", timestamp=timestamp, target=intent.instrument_id,
                     metadata={"reasons": ",".join(str(r) for r in reasons)})

    pre_fill_research_risk = None
    if strict_research_risk and research_risk_returns is not None:
        pre_fill_research_risk = _evaluate_research_risk(
            returns=[float(value) for value in research_risk_returns],
            weights=weights,
            equity_curve=[
                float(value) for value in research_risk_equity_curve or ()
            ],
            timestamps=(
                [int(value) for value in research_risk_timestamps]
                if research_risk_timestamps is not None
                else None
            ),
            inputs=research_risk_inputs,
            config=research_risk_config,
            require_full=True,
            series="aligned_pit_history",
        )
        pre_fill_research_risk["scope"] = "PRE_FILL_RESEARCH_EVIDENCE"

    fill_admitted = (
        not strict_research_risk
        or (
            pre_fill_research_risk is not None
            and full_research_risk_approved(pre_fill_research_risk)
        )
    )
    if not fill_admitted:
        # Strict paper execution requires a complete approval computed from an
        # explicit aligned history before any simulated fill.  Missing,
        # post-fill-only, or rejected evidence is observable but cannot mutate
        # the paper position projection.
        filled = {
            "fills": [], "reports": [], "impact_trades": [],
            "n_impact": 0, "n_flat": 0, "routing": [],
            "route_rejections": [], "route_tca": [], "n_adapter_rejected": 0,
        }
    else:
        filled = simulate_fills(screened["approved"], prices, adapter=adapter, run_id=run_id,
                                timestamp=timestamp, cost_bps=cost_bps,
                                route_planner=route_planner)
    for f in filled["fills"]:
        audit.append(event_id=_det_id("fill", run_id, f.fill_id), actor=f"dry_run:{strategy_id}",
                     action="DRY_RUN_FILL", timestamp=timestamp, target=f.instrument_id,
                     metadata={"qty": str(f.quantity), "price": str(f.price), "side": f.side.value})
    pnl = reconcile_run(filled["fills"], marks or prices, initial_capital)
    single_return = (
        float(pnl["total_pnl"]) / float(initial_capital)
        if isinstance(initial_capital, (int, float)) and initial_capital > 0
        else float("nan")
    )
    risk_returns = (
        [float(value) for value in research_risk_returns]
        if research_risk_returns is not None
        else [single_return]
    )
    risk_equity_curve = (
        [float(value) for value in research_risk_equity_curve]
        if research_risk_equity_curve is not None
        else [float(pnl["equity"])]
    )
    risk_timestamps = (
        [int(value) for value in research_risk_timestamps]
        if research_risk_timestamps is not None
        else ([int(timestamp)] if research_risk_returns is None else None)
    )
    research_risk = pre_fill_research_risk or _evaluate_research_risk(
        returns=risk_returns,
        weights=weights,
        equity_curve=risk_equity_curve,
        timestamps=risk_timestamps,
        inputs=research_risk_inputs,
        config=research_risk_config,
        require_full=strict_research_risk,
        series=(
            "aligned_pit_history"
            if research_risk_returns is not None
            else "single_period"
        ),
    )
    audit_root = audit.seal().head_hash

    result = DryRunRun(
        run_id=run_id, strategy_id=strategy_id, timestamp=timestamp, n_intents=len(intents),
        n_approved=len(screened["approved"]), n_rejected=len(screened["rejected"]),
        rejected_reasons=[rr for _, rs in screened["rejected"] for rr in rs], pnl=pnl,
        audit_root=str(audit_root),
        detail={"n_fills": len(filled["fills"]), "targets": targets,
                "n_adapter_rejected": filled.get("n_adapter_rejected", 0),
                "routing_snapshot_id": routing_snapshot_id or None,
                "route_tca_schema_version": 1 if route_planner is not None else None,
                 "routing": filled.get("routing", []),
                 "route_rejections": filled.get("route_rejections", []),
                 "route_tca": filled.get("route_tca", []),
                 "research_risk_fill_admitted": fill_admitted,
                 "research_risk": research_risk})
    should_publish_risk = (
        bool(publish_research_risk_event)
        if publish_research_risk_event is not None
        else research_risk_inputs is not None
    )
    if (
        event_bus is not None
        and should_publish_risk
        and research_risk.get("engines_run")
    ):
        event_bus.publish_once(
            research_risk_evaluated_event(
                run_id=run_id,
                strategy_id=strategy_id,
                run_repro_hash=result.reproducibility_hash,
                timestamp_ms=int(timestamp),
                risk=research_risk,
            ),
            correlation_id=run_id,
            causation_id=start_event_id,
        )
    if event_bus is not None and publish_completion_event:
        event_bus.publish_once(
            dry_run_completed_event(
                run_id=run_id,
                strategy_id=strategy_id,
                timestamp_ms=int(timestamp),
                n_intents=result.n_intents,
                n_approved=result.n_approved,
                n_rejected=result.n_rejected,
                n_fills=len(filled["fills"]),
                audit_root=result.audit_root,
                run_repro_hash=result.reproducibility_hash,
            ),
            correlation_id=run_id,
            causation_id=start_event_id,
        )
    return result


# --- Multi-period rolling dry-run (P&L curve) --------------------------------

def _ann_sharpe(returns, ppy: int = 52) -> float:
    """年化夏普(周度→√52)。样本不足/零波动 → 0。"""
    if len(returns) < 2:
        return 0.0
    sd = statistics.pstdev(returns)
    return round(statistics.fmean(returns) / sd * math.sqrt(ppy), 4) if sd else 0.0


def _max_drawdown_from_returns(returns) -> float:
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in returns:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)
    return round(mdd, 4)


def _vol_target_returns(returns, *, target_ann: float = 0.10, lookback: int = 8, lev_cap: float = 3.0,
                        ppy: int = 52):
    """PIT 尾随实现波动杠杆标量(同 carry vol-target):lev_t = min(cap, 目标每期波动 / 尾随实现波动),
    用 t 之前的收益估计(前 lookback 期原样穿过)。重塑风险、不造 edge。"""
    target_per = target_ann / math.sqrt(ppy)
    out = []
    for i in range(len(returns)):
        if i < lookback:
            out.append(returns[i])
            continue
        sd = statistics.pstdev(returns[i - lookback:i]) or 1e-9
        out.append(min(lev_cap, target_per / sd) * returns[i])
    return out


def _tca_lite(impact, impact_trades) -> dict:
    """Rank #11 TCA-lite: recompute the impact cost across the calibration band (aggressive→lowest,
    conservative→highest) and check the charged total sits inside it. L2 has no real fills, so the
    calibration band is the only defensible 'assumed vs reference' cost check."""
    from services.execution.impact_calibration import (
        default_crypto_impact_calibration, scenario_impact)
    cal = default_crypto_impact_calibration()

    def total(imp):
        return sum(imp.total_cost_notional(n, a) for n, a in impact_trades)

    charged = total(impact)
    lo, hi = total(scenario_impact(cal.aggressive)), total(scenario_impact(cal.conservative))
    return {"assumed": round(charged, 2), "band_lo": round(lo, 2), "band_hi": round(hi, 2),
            "in_band": lo - 1e-6 <= charged <= hi + 1e-6, "provenance": cal.provenance}


def run_multi_period_dry_run(*, periods, weights_by_period, prices_by_period, gate, ctx_factory, adapter,
                             strategy_id: str, aum: float, cost_bps: float = 0.0, cash_flow_fn=None,
                             vol_target: dict = None, impact=None, adv_by_period: dict = None,
                             filters: Optional[Dict[str, Tuple[float, float, float]]] = None,
                             route_planner: Callable | None = None,
                             routing_snapshot_id: str = "",
                             event_bus=None, publish_completion_event: bool = True,
                             research_risk_inputs: Optional[DryRunResearchRiskInputs] = None,
                             research_risk_config: Optional[RiskConfig] = None,
                             require_full_research_risk: bool = False,
                             publish_research_risk_event: Optional[bool] = None) -> DryRunRun:
    """全历史周度滚动干跑(薄、确定性):逐期重平衡,每期增量调仓单过门(fail-closed)→纸面成交(不真发)→
    价格 MTM(reconcile)+ 资金费现金流(cash_flow_fn)→权益点→哈希链审计→raw/vol-target 两条净值曲线。
    ctx_factory(intent, prices, period);kill-switch 由 gate 持有(latched→全期被拒)。墙钟在驱动器边界。

    `filters`(可选,F10 审计,2026-07-08——小资金规模下交易所步长/最小名义额不是可忽略的近似误差):
    `{instrument_id: (step_size, min_qty, min_notional)}`,原样透传给每期的 `plan_target_quantities`
    (见其 docstring)。默认 None -> 每期目标手数计算与改动前逐字节相同(回归测试钉死)。这轮只交付
    管线本身有能力做量化——真实 Binance/Bybit exchangeInfo 数据接进具体驱动器是后续独立工作。"""
    strict_research_risk = bool(require_full_research_risk or research_risk_inputs is not None)
    risk_identity = _research_risk_identity(
        research_risk_inputs, research_risk_config, strict_research_risk
    )
    if route_planner is not None and (
        not isinstance(routing_snapshot_id, str) or not routing_snapshot_id.strip()
    ):
        raise ValueError("routing_snapshot_id is required when route_planner is provided")
    if route_planner is None and routing_snapshot_id:
        raise ValueError("routing_snapshot_id requires route_planner")
    run_id_parts = [strategy_id, "multi", len(periods), tuple(periods[:1]), tuple(periods[-1:])]
    if risk_identity:
        run_id_parts.append(risk_identity)
    if routing_snapshot_id:
        run_id_parts.append(("sor", routing_snapshot_id))
    run_id = _det_id(*run_id_parts)
    start_event_id = ""
    if event_bus is not None:
        start_event = dry_run_started_event(
            run_id=run_id,
            strategy_id=strategy_id,
            timestamp_ms=int(periods[0]) if periods else 0,
            mode="multi_period",
        )
        event_bus.publish_once(start_event, correlation_id=run_id)
        start_event_id = start_event.event_id
    audit = GlobalAuditLog()
    audit.append(event_id=_det_id("mstart", run_id), actor=f"dry_run:{strategy_id}", action="MULTI_START",
                 timestamp=int(periods[0]) if periods else 0, target=run_id,
                 metadata={"n_periods": str(len(periods)), "aum": str(aum)})

    held: dict = {}                       # instrument -> signed qty
    all_fills: list = []
    all_impact_trades: list = []          # (notional, adv) per impact-costed fill, for TCA-lite
    all_routing: list = []
    all_route_rejections: list = []
    all_route_tca: list = []
    tot_impact = tot_flat = 0
    route_cost_total = 0.0
    cum_cash = 0.0
    curve: list = []
    tot_appr = tot_rej = 0
    prev_equity = aum
    n_periods = len(periods)
    for i, t in enumerate(periods):
        prices_t = prices_by_period[t]
        adv_t = adv_by_period.get(t) if adv_by_period else None
        targets = plan_target_quantities(weights_by_period.get(t, {}), prices_t, aum, filters=filters)
        intents = build_intents(targets, held, strategy_id=strategy_id)
        screened = screen_intents(intents, gate=gate,
                                  ctx_factory=lambda i, _p=prices_t, _t=t: ctx_factory(i, _p, _t))
        if event_bus is not None:
            _publish_screening_events(
                event_bus=event_bus,
                screened=screened,
                run_id=run_id,
                strategy_id=strategy_id,
                timestamp=int(t),
                scope=f"period:{t}",
                causation_id=start_event_id,
            )
        tot_appr += len(screened["approved"])
        tot_rej += len(screened["rejected"])
        if strict_research_risk and research_risk_inputs is None:
            # Keep the strict contract fail-closed across every period.  We
            # still build the curve and persist the pending decision so the
            # missing evidence is observable and reproducible.
            filled = {
                "fills": [], "reports": [], "impact_trades": [],
                "n_impact": 0, "n_flat": 0, "routing": [],
                "route_rejections": [], "route_tca": [], "n_adapter_rejected": 0,
                "route_cost_total": 0.0,
            }
        else:
            period_route_planner = (
                (
                    lambda intent, _prices=prices_t, _period=t:
                    route_planner(intent, _prices, _period)
                )
                if route_planner is not None
                else None
            )
            filled = simulate_fills(screened["approved"], prices_t, adapter=adapter,
                                    run_id=f"{run_id}:{t}", timestamp=int(t), cost_bps=cost_bps,
                                    impact=impact, adv=adv_t,
                                    route_planner=period_route_planner)
        all_impact_trades.extend(filled.get("impact_trades", []))
        all_routing.extend(
            {"period": int(t), **item}
            for item in filled.get("routing", [])
        )
        all_route_rejections.extend(
            {"period": int(t), **item}
            for item in filled.get("route_rejections", [])
        )
        all_route_tca.extend(
            {"period": int(t), "tca": item}
            for item in filled.get("route_tca", [])
        )
        tot_impact += filled.get("n_impact", 0)
        tot_flat += filled.get("n_flat", 0)
        route_cost_total += float(filled.get("route_cost_total", 0.0))
        for f in filled["fills"]:
            all_fills.append(f)
            held[f.instrument_id] = held.get(f.instrument_id, 0) + (
                f.quantity if f.side == PaperSide.BUY else -f.quantity)
        if cash_flow_fn is not None:
            cum_cash += float(cash_flow_fn(held, prices_t, t))
        equity_t = reconcile(all_fills, marks=prices_t, initial_capital=aum).equity + cum_cash
        ret = (equity_t / prev_equity - 1.0) if prev_equity else 0.0
        prev_equity = equity_t
        # post-rebalance dollar exposure at this period's prices: net≈0 proves dollar-neutrality
        # (integer-lot rounding leaks net exposure; smaller |net| = cleaner neutrality).
        net_notional = sum(q * prices_t.get(i, 0.0) for i, q in held.items())
        gross_notional = sum(abs(q * prices_t.get(i, 0.0)) for i, q in held.items())
        curve.append({"t": int(t), "equity": round(equity_t, 6), "ret": round(ret, 8),
                      "approved": len(screened["approved"]), "rejected": len(screened["rejected"]),
                      "fills": len(filled["fills"]), "net": round(net_notional, 2),
                      "gross": round(gross_notional, 2)})
        audit.append(event_id=_det_id("per", run_id, t), actor=f"dry_run:{strategy_id}", action="PERIOD",
                     timestamp=int(t), target=str(t),
                     metadata={"equity": str(round(equity_t, 2)), "ret": str(round(ret, 6)),
                               "approved": str(len(screened["approved"])),
                               "rejected": str(len(screened["rejected"]))})
        # 每 20 周或最后一周打印进度
        if (i + 1) % 20 == 0 or i == n_periods - 1:
            print(f"  [{i+1}/{n_periods}] equity={equity_t:,.0f}  "
                  f"approved={len(screened['approved'])}  rejected={len(screened['rejected'])}", flush=True)

    returns = [p["ret"] for p in curve]
    vt_curve: list = []
    if vol_target is not None and curve:
        e = aum
        for p, r in zip(curve, _vol_target_returns(returns, **vol_target)):
            e *= (1.0 + r)
            vt_curve.append({"t": p["t"], "equity": round(e, 6), "ret": round(r, 8)})

    final_equity = curve[-1]["equity"] if curve else aum
    residual = (reconcile(all_fills, marks=prices_by_period[periods[-1]], initial_capital=aum)
                .reconciliation_residual()) if periods else 0.0
    # sharpe_vt/max_drawdown_vt (审计发现 #10): same pure helpers applied to the VT curve's OWN `ret`
    # series -- this is what makes the "RAW -> VT" figures quoted in docs reproducible from this function's
    # output alone. vol_target=None -> honestly None (not 0, not omitted): callers must be able to tell
    # "not computed" apart from "computed as zero".
    vt_returns = [p["ret"] for p in vt_curve]
    sharpe_vt = _ann_sharpe(vt_returns) if vol_target is not None else None
    max_drawdown_vt = _max_drawdown_from_returns(vt_returns) if vol_target is not None else None
    pnl = {"total_return": round(final_equity / aum - 1.0, 6), "sharpe": _ann_sharpe(returns),
           "max_drawdown": _max_drawdown_from_returns(returns), "sharpe_vt": sharpe_vt,
           "max_drawdown_vt": max_drawdown_vt, "final_equity": round(final_equity, 6),
           "total_funding": round(cum_cash, 6), "n_periods": len(periods), "total_rejected": tot_rej,
           "residual": round(residual, 9),
           "avg_net_exposure_pct": round(statistics.fmean([abs(p["net"]) / aum for p in curve]), 6)
           if curve else 0.0}
    # Rank #11: honest cost-model provenance + TCA-lite band, surfaced read-only on the console.
    used_impact = impact is not None and tot_impact > 0
    cost = {"cost_model": "IMPACT_AC" if used_impact else "FLAT_BPS",
            "cost_provenance": "UNCALIBRATED_PRINCIPLED" if used_impact else "FLAT_BPS",
            "cost_fills": {"impact": tot_impact, "flat": tot_flat}, "cost_bps": cost_bps,
            "sor_route_cost": round(route_cost_total, 6)}
    if used_impact:
        cost["tca"] = _tca_lite(impact, all_impact_trades)
    risk_weights = (
        dict(weights_by_period.get(periods[-1], {})) if periods else {}
    )
    research_risk = _evaluate_research_risk(
        returns=returns,
        weights=risk_weights,
        equity_curve=[p["equity"] for p in curve],
        timestamps=[int(period) for period in periods],
        inputs=research_risk_inputs,
        config=research_risk_config,
        require_full=strict_research_risk,
        series="multi_period",
    )
    result = DryRunRun(
        run_id=run_id, strategy_id=strategy_id, timestamp=int(periods[-1]) if periods else 0,
        n_intents=tot_appr + tot_rej, n_approved=tot_appr, n_rejected=tot_rej, rejected_reasons=[],
        pnl=pnl, audit_root=str(audit.seal().head_hash),
        detail={"mode": "multi_period", "n_periods": len(periods), "cadence": "weekly",
                "n_fills": len(all_fills), "equity_curve": curve, "equity_curve_vt": vt_curve,
                "routing_snapshot_id": routing_snapshot_id or None,
                "route_tca_schema_version": 1 if route_planner is not None else None,
                "routing": all_routing, "route_rejections": all_route_rejections,
                "route_tca": all_route_tca,
                "research_risk": research_risk, **cost})
    should_publish_risk = (
        bool(publish_research_risk_event)
        if publish_research_risk_event is not None
        else research_risk_inputs is not None
    )
    if (
        event_bus is not None
        and should_publish_risk
        and research_risk.get("engines_run")
    ):
        event_bus.publish_once(
            research_risk_evaluated_event(
                run_id=run_id,
                strategy_id=strategy_id,
                run_repro_hash=result.reproducibility_hash,
                timestamp_ms=result.timestamp,
                risk=research_risk,
            ),
            correlation_id=run_id,
            causation_id=start_event_id,
        )
    if event_bus is not None and publish_completion_event:
        event_bus.publish_once(
            dry_run_completed_event(
                run_id=run_id,
                strategy_id=strategy_id,
                timestamp_ms=result.timestamp,
                n_intents=result.n_intents,
                n_approved=result.n_approved,
                n_rejected=result.n_rejected,
                n_fills=len(all_fills),
                audit_root=result.audit_root,
                run_repro_hash=result.reproducibility_hash,
            ),
            correlation_id=run_id,
            causation_id=start_event_id,
        )
    return result
