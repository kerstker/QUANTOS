"""Gate 账户上下文构造(2026-07-05,机构审计 P1)。设计:
docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md 补充。

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

from typing import Mapping, Tuple

from quantos.contracts.portfolio import Position


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
