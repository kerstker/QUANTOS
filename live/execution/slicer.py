"""TWAP 切单(2026-07-05)。设计:
docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md §⑥。

诚实定位:$3-5k 周度调仓单子极小,**现阶段默认不启用**(`submit_live_batch(slicer=None)`);
这是为规模化预留的已测试机制。纯函数、无墙钟——"T"WAP 的时间间隔由调用方在 `poll_sleep`/
调度层控制,本文件只负责确定性切分。

幂等语义:子单 client_order_id = "{parent_id}-s{i}"(确定性派生),重试同一母单产生同一批
子单 ID,交易所天然去重;不需要切分(单片)时原样返回母单、不改 ID——幂等语义与不启用
slicer 完全一致。

步长注意:切分发生在交易所过滤器取整**之后**,等分出的子单数量可能落不到步长网格上——
调用方应把 `step_size` 传进来,子单逐片向下取整、余量并入最后一片(总量绝不超过母单;
取整丢掉的尘埃如实少执行,宁可少不可多)。
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import List, Optional

from live.contracts import LiveOrderRequest
from live.execution.filters import round_to_step


def plan_slices(request: LiveOrderRequest, *, n_slices: Optional[int] = None,
                max_child_notional: Optional[float] = None, price: Optional[float] = None,
                step_size: Optional[float] = None) -> List[LiveOrderRequest]:
    """母单 -> 确定性子单序列。两种模式(可组合,取更细的切分):
    - n_slices:等分成 N 片;
    - max_child_notional(需给 price):每片名义额不超过上限,片数 = ceil(总名义额/上限)。
    """
    if n_slices is None and max_child_notional is None:
        raise ValueError("plan_slices needs n_slices and/or max_child_notional")
    if n_slices is not None and n_slices < 1:
        raise ValueError(f"n_slices must be >= 1, got {n_slices}")
    n = n_slices or 1
    if max_child_notional is not None:
        if price is None or price <= 0:
            raise ValueError("max_child_notional mode requires a positive price")
        if max_child_notional <= 0:
            raise ValueError(f"max_child_notional must be positive, got {max_child_notional}")
        needed = max(1, math.ceil(request.quantity * price / max_child_notional - 1e-9))
        n = max(n, needed)
    if n == 1:
        return [request]                       # 不切就不改 ID,幂等语义与不启用完全一致

    share = request.quantity / n
    children: List[LiveOrderRequest] = []
    allocated = 0.0
    for i in range(n):
        if i < n - 1:
            qty = round_to_step(share, step_size) if step_size else share
        else:
            remainder = request.quantity - allocated          # 余量并入最后一片
            qty = round_to_step(remainder, step_size) if step_size else remainder
        if qty <= 0:
            continue                                          # 取整到 0 的尘埃片,诚实丢弃
        allocated += qty
        children.append(replace(request, quantity=qty,
                                client_order_id=f"{request.client_order_id}-s{i}"))
    return children
