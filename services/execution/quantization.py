"""量化(quantization)——交易所步长/最小名义额的纯数值判断(F10 审计,2026-07-08 抽取)。

`round_to_step` 原样搬自 `live/execution/filters.py`(逐字节复制,零行为变化;搬迁历史见
`live/execution/filters.py` 顶部注记 / `docs/audit/2026-07-07-live-readiness-top-tier-gap-audit.md`
§4.1 F10)。`live/execution/filters.py` 现在 `from services.execution.quantization import
round_to_step` 复用同一份实现,不留两份可能悄悄分叉的复制品——`live/` import `services/` 是本仓库
允许的单向依赖方向(`services/` 反向 import `live/` 会撞 `tests/governance/
test_domain_import_boundary.py` / `test_live_execution_boundary.py`)。

`meets_min_notional` 是新加的姊妹纯函数,供 `services/execution/dry_run_pipeline.py` 的可选量化
开关使用(回测/干跑侧只需要"数量取整 + 名义额门槛"这两个纯数值判断,不需要
`live/execution/filters.py::apply_exchange_filters` 整个函数——那个函数额外绑定
`LiveOrderRequest`/`client_order_id` 等 live 执行栈专属概念)。

纯函数、无 IO / 网络 / 墙钟 / RNG。
"""
from __future__ import annotations

import math


def round_to_step(qty: float, step_size: float) -> float:
    """向下取整到交易所步长网格(绝不超发——宁可少买/少卖,不可超过策略意图)。step_size<=0 直通。
    +1e-9 epsilon 只为吸收浮点表示噪声(如 0.3/0.1 因二进制浮点略小于 3),不改变真实取整方向。"""
    if step_size <= 0:
        return qty
    steps = math.floor(qty / step_size + 1e-9)
    return round(steps * step_size, 10)


def meets_min_notional(qty: float, price: float, min_notional: float) -> bool:
    """数量*价格是否达到交易所最小名义额门槛。`min_notional<=0` 视为无门槛,恒真。纯数值判断,
    镜像 `live/execution/filters.py::apply_exchange_filters` 里 `rounded * price < filt.min_notional`
    的丢弃条件(此处返回其否定式:True=达标可交易,False=该被诚实丢弃)。"""
    if min_notional <= 0:
        return True
    return qty * price >= min_notional
