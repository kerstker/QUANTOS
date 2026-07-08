"""日内盈亏跟踪(2026-07-05,机构审计 P1)。设计:
docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md 补充。

纯函数、无 IO(状态的文件读写在调用方边界完成,与 `_load_peak_equity`/`_save_peak_equity`
同款模式)。`check_daily_loss` 门需要真实 day_pnl 才能生效——此前恒 0.0 意味着日内止损
结构性永不触发。
"""
from __future__ import annotations

from typing import Optional, Tuple


def compute_day_pnl(state: Optional[dict], *, equity: float, day_key: str) -> Tuple[float, dict]:
    """state = 上次持久化的 {"day": "YYYY-MM-DD", "start_equity": float} 或 None(首次运行)。
    跨自然日(day_key 变化)或状态缺失/损坏(start_equity 非正)-> 诚实重置:day_pnl=0.0、
    以当前权益为新的当日起点(不假装知道一个从未记录过的起点)。同一天 -> day_pnl = 当前
    权益 - 当日起始权益,状态不变(起点在一天内不会移动)。"""
    if state is None or state.get("day") != day_key or (state.get("start_equity") or 0) <= 0:
        return 0.0, {"day": day_key, "start_equity": equity}
    return equity - state["start_equity"], state
