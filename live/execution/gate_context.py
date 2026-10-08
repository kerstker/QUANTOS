"""Gate 账户上下文构造(2026-07-05,机构审计 P1)。设计:
docs/engineering/specs/2026-07-05-institutional-live-stack-design.md 补充。

纯函数、无网络/无 IO。把真实账本仓位折算成 `services/risk/pre_trade/context.py::
AccountRiskState.positions` 认识的 `Position` 序列——修复"gate 账户级检查(仓位限额/
集中度/敞口/杠杆)此前对着硬编码的 positions=() 评估,结构性对真实持仓视而不见"这个缺口。

缩放单位与 `live/pipeline.py::UNIT_SCALE` 同一约定:`Position.quantity`(int)是 gate 的
风控门货币,与 `OrderIntent.quantity` 同一缩放,使 `position_qty()`/`projected_position()`
的加总在正确的单位系里进行。`market_value` 是真实美元名义额(不缩放)——与
`RiskContext.effective_price`(= `reference_price` = 真实价格/unit_scale)相乘缩放整数
后得到的名义额是同一美元口径,两边可以直接相加比较。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence, Tuple

from quantos.contracts.portfolio import Position
from services.risk.pre_trade.context import order_fingerprint


@dataclass(frozen=True)
class RecentOrderGateInputs:
    timestamps: Tuple[int, ...]
    fingerprints: Tuple[str, ...]
    statuses: Tuple[str, ...]


def build_gate_positions(current_positions: Mapping[str, float], prices: Mapping[str, float],
                         *, unit_scale: int) -> Tuple[Position, ...]:
    """真实仓位(真实币数量)+ 真实价格 -> gate 认识的 Position 序列。零仓位标的不生成条目
    (与"没有这个仓位"语义一致)。缺价的持仓仍计入 quantity(gate 的仓位数量检查不应因为
    这一刻拿不到价就假装没有仓位),market_value 诚实置 0(无法估值,不是"值 0 美元"的
    断言)。"""
    out = []
    for inst, qty in current_positions.items():
        if qty == 0.0:
            continue
        px = prices.get(inst)
        market_value = qty * px if (px is not None and px > 0) else 0.0
        out.append(Position(instrument_id=inst, quantity=int(round(qty * unit_scale)),
                            avg_cost=0.0, market_value=market_value))
    return tuple(out)


def build_recent_order_gate_inputs(
    orders: Sequence[Mapping[str, object]],
    *,
    now: int,
    rate_window_ms: int,
    duplicate_window_ms: int,
    unit_scale: int,
) -> RecentOrderGateInputs:
    """Build rate, duplicate, and cancel-rate inputs from one durable ledger snapshot."""
    if rate_window_ms <= 0:
        raise ValueError("rate_window_ms must be positive")
    if duplicate_window_ms <= 0 or duplicate_window_ms > rate_window_ms:
        raise ValueError("duplicate_window_ms must be positive and no larger than rate_window_ms")
    if unit_scale <= 0:
        raise ValueError("unit_scale must be positive")

    rate_cutoff = now - rate_window_ms
    duplicate_cutoff = now - duplicate_window_ms
    timestamps = []
    statuses = []
    fingerprints = []

    for order in orders:
        submitted_at = int(order["submitted_at"])
        if submitted_at > now:
            raise ValueError("future submitted_at in live order ledger")
        if submitted_at < rate_cutoff:
            continue

        timestamps.append(submitted_at)
        statuses.append(str(order["status"]).upper())
        if submitted_at < duplicate_cutoff:
            continue

        quantity = int(round(float(order["quantity"]) * unit_scale))
        if quantity <= 0:
            raise ValueError("non-positive quantity in live order ledger")
        stored_limit = order.get("limit_price")
        limit_prices = [None]
        if stored_limit is not None:
            limit_prices.append(float(stored_limit))
        for limit_price in limit_prices:
            fingerprints.append(order_fingerprint(
                instrument_id=str(order["instrument_id"]),
                side=str(order["side"]),
                quantity=quantity,
                limit_price=limit_price,
                strategy_id=str(order["strategy_id"]),
            ))

    return RecentOrderGateInputs(
        timestamps=tuple(timestamps),
        fingerprints=tuple(dict.fromkeys(fingerprints)),
        statuses=tuple(statuses),
    )
