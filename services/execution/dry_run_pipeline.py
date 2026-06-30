"""端到端干跑流水线编排器(薄、确定性)。

数据流:权重 → 目标整数手数 → build_intents → 逐意图过预交易门(运行时 chokepoint,fail-closed)+
kill_switch → 干跑适配器受理(不真发)→ 纸面成交 → reconcile/PnL → 哈希链审计 → DryRunRun。

容器化(L2,不可让):五个 LIVE 标志恒 false、门 fail-closed、kill_switch 先查、干跑适配器无券商 SDK/
不真发、无网络 import、编排器绝不进 research_runner、核心无 RNG/无墙钟(timestamp/seed 由调用方边界注入)。
RiskContext 由注入的 ctx_factory 装配(核心不硬编码账户/安全装配),保持可测、可复用。
实盘 = 同接口换真券商适配器 + 人工批准 = 显式非目标。
"""
from __future__ import annotations

import hashlib
from typing import Callable, Dict, Sequence

from quantos.contracts.risk import RiskDecisionType
from services.research.paper_trading.domain.models import FillMethod, OrderSide as PaperSide, PaperFill
from services.research.paper_trading.domain.reconciliation import reconcile
from services.trading.dry_run_adapters.domain.models import DryRunCommand


def _det_id(*parts) -> str:
    """Deterministic short id from parts (no RNG/clock) — keeps the whole pipeline reproducible."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def plan_target_quantities(weights: Dict[str, float], prices: Dict[str, float], aum: float) -> Dict[str, int]:
    """权重 → 目标整数手数:round(w * aum / price)。价缺失/≤0 跳过(fail-soft);0 手不下意图。"""
    out: Dict[str, int] = {}
    for inst, w in weights.items():
        px = prices.get(inst)
        if px is None or px <= 0:
            continue
        q = int(round(w * aum / px))
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


def _to_command(intent, ref: str, price: float, *, idx: int, run_id: str) -> DryRunCommand:
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


def simulate_fills(approved: Sequence, prices: Dict[str, float], *, adapter, run_id: str,
                   timestamp: int, cost_bps: float = 0.0) -> dict:
    """已批准意图 → 干跑适配器受理(process→simulate_fill,绝不真发)→ 纸面成交 PaperFill。
    适配器是第二道闸:若它拒绝(rejection_reasons 非空)则不成交。成本以 cost_bps 计入 commission(默认 0)。
    确定性 id;不捕获适配器内部 uuid/event_time(保 DryRunRun 可复现)。"""
    fills, reports = [], []
    for i, (intent, ref) in enumerate(approved):
        px = prices.get(intent.instrument_id)
        if px is None or px <= 0:
            continue
        run = adapter.process(_to_command(intent, ref, px, idx=i, run_id=run_id))
        if run.rejection_reasons:  # 适配器二次校验拒 -> 不成交
            if run.normalized_report is not None:
                reports.append(run.normalized_report)
            continue
        rep = adapter.simulate_fill(run.run_id)
        if rep is None:
            continue
        reports.append(rep)
        qty = abs(int(intent.quantity))
        fp = float(rep.fill_price or px)
        notional = fp * qty
        fills.append(PaperFill(
            fill_id=_det_id("f", run_id, i), order_id=_det_id("o", run_id, i), run_id=run_id,
            instrument_id=intent.instrument_id, side=PaperSide(intent.side.value), quantity=qty,
            price=fp, fill_method=FillMethod.NEXT_OPEN, timestamp=timestamp,
            commission=notional * cost_bps / 1e4, slippage=0.0))
    return {"fills": fills, "reports": reports}


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
