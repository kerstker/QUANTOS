"""TWAP 切单(2026-07-05)。设计:
docs/engineering/specs/2026-07-05-institutional-live-stack-design.md §⑥。

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


def _finite_pos(value: object) -> bool:
    return (isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) > 0.0)


def plan_slices(request: LiveOrderRequest, *, n_slices: Optional[int] = None,
                max_child_notional: Optional[float] = None, price: Optional[float] = None,
                step_size: Optional[float] = None) -> List[LiveOrderRequest]:
    """母单 -> 确定性子单序列。两种模式(可组合,取更细的切分):
    - n_slices:等分成 N 片;
    - max_child_notional(需给 price):每片名义额不超过上限,片数 = ceil(总名义额/上限)。
    """
    if not _finite_pos(request.quantity):
        raise ValueError(f"request.quantity must be finite and positive, got {request.quantity!r}")
    if n_slices is None and max_child_notional is None:
        raise ValueError("plan_slices needs n_slices and/or max_child_notional")
    if n_slices is not None:
        if isinstance(n_slices, bool) or not isinstance(n_slices, int) or n_slices < 1:
            raise ValueError(f"n_slices must be an integer >= 1, got {n_slices!r}")
    if step_size is not None:
        if (isinstance(step_size, bool) or not isinstance(step_size, (int, float))
                or not math.isfinite(float(step_size)) or float(step_size) < 0.0):
            raise ValueError(f"step_size must be finite and >= 0, got {step_size!r}")
    n = n_slices or 1
    if max_child_notional is not None:
        if not _finite_pos(price):
            raise ValueError("max_child_notional mode requires a positive finite price")
        if not _finite_pos(max_child_notional):
            raise ValueError(f"max_child_notional must be finite and positive, got {max_child_notional!r}")
        ratio = float(request.quantity) * float(price) / float(max_child_notional)
        if not math.isfinite(ratio):
            raise ValueError("slice count overflowed to non-finite value")
        needed = max(1, math.ceil(ratio - 1e-9))
        n = max(n, needed)
    if n == 1:
        return [request]                       # 不切就不改 ID,幂等语义与不启用完全一致

    step_f = float(step_size) if step_size is not None and float(step_size) > 0.0 else None
    share = float(request.quantity) / n
    children: List[LiveOrderRequest] = []
    allocated = 0.0
    for i in range(n):
        if i < n - 1:
            qty = round_to_step(share, step_f) if step_f is not None else share
        else:
            remainder = max(0.0, float(request.quantity) - allocated)   # 余量并入最后一片
            qty = round_to_step(remainder, step_f) if step_f is not None else remainder
        if qty <= 0:
            continue                                          # 取整到 0 的尘埃片,诚实丢弃
        allocated += qty
        children.append(replace(request, quantity=qty,
                                client_order_id=f"{request.client_order_id}-s{i}"))
    return children
