"""限价单成交模型 + 跨 bar 挂单状态(事件驱动回测,#3 子项)。

纯 bar(OHLCV)数据下诚实建模两件事:非执行风险(限价可能永不成交)与排队位置
(用"前置量随成交消耗"近似)。无 L2、无逐笔。确定性、纯 Python、失败关闭。

限价由决策收盘价 ± offset 导出(target-weight 接口只发权重,不发价)。触及价位按
participation×volume 与 queue_ahead 双重约束部分成交;穿透则全成。默认保守
(queue_fraction>0 低估成交、高估非执行风险),符合安全关键平台"不高估可执行性"。
"""
from __future__ import annotations

from dataclasses import dataclass

from .costs import CostModel


@dataclass(frozen=True)
class RestingOrder:
    """一张跨 bar 存活的限价挂单(每标的至多一张)。"""
    instrument_id: str
    side: int               # +1 买,−1 卖
    qty_remaining: int      # 仍待成交的有符号股数(买为正、卖为负)
    limit_price: float
    queue_ahead: float      # 必须先于我们成交的流动性;随该价位成交量递减
    bars_alive: int         # 每 bar 递增;> expire_bars 则撤销


@dataclass(frozen=True)
class LimitFill:
    """限价成交模型 + 排队启发式。"""
    limit_offset_bps: float = 0.0
    queue_fraction: float = 0.25
    participation: float = 0.10
    expire_bars: int = 3

    def __post_init__(self):
        if self.limit_offset_bps < 0:
            raise ValueError("limit_offset_bps must be >= 0")
        if self.queue_fraction < 0:
            raise ValueError("queue_fraction must be >= 0")
        if not (0.0 < self.participation <= 1.0):
            raise ValueError("participation must be in (0, 1]")
        if self.expire_bars < 1:
            raise ValueError("expire_bars must be >= 1")

    def limit_price(self, side: int, close: float) -> float:
        off = self.limit_offset_bps / 10_000.0
        return close * (1.0 - off) if side > 0 else close * (1.0 + off)

    def initial_queue(self, bar) -> float:
        return self.queue_fraction * bar.volume

    def match(self, order: RestingOrder, bar, cost: CostModel) -> tuple[int, float, float, float]:
        """对单根 bar 撮合一张挂单。返回 (filled_signed, fill_price, fee_cost, new_queue_ahead)。
        不修改 order;filled 可为 0。买看 bar.low,卖看 bar.high(镜像)。"""
        L = order.limit_price
        new_q = order.queue_ahead
        if order.side > 0:                                   # 买
            if bar.low > L:                                  # 未触及
                return 0, 0.0, 0.0, new_q
            if bar.low < L:                                  # 穿透 → 全成
                filled = order.qty_remaining
                base_price = min(bar.open, L)
                new_q = 0.0
            else:                                            # 触及 → 部分
                vol_at = self.participation * bar.volume
                fillable = max(0.0, vol_at - order.queue_ahead)
                filled = min(order.qty_remaining, int(fillable))
                new_q = max(0.0, order.queue_ahead - vol_at)
                if filled == 0:
                    return 0, 0.0, 0.0, new_q
                base_price = L
        else:                                                # 卖(qty_remaining 为负)
            if bar.high < L:
                return 0, 0.0, 0.0, new_q
            if bar.high > L:
                filled = order.qty_remaining
                base_price = max(bar.open, L)
                new_q = 0.0
            else:
                vol_at = self.participation * bar.volume
                fillable = max(0.0, vol_at - order.queue_ahead)
                filled = -min(-order.qty_remaining, int(fillable))
                new_q = max(0.0, order.queue_ahead - vol_at)
                if filled == 0:
                    return 0, 0.0, 0.0, new_q
                base_price = L
        sign = 1.0 if filled > 0 else -1.0
        fill_price = base_price * (1.0 + sign * cost.slippage_bps / 10_000.0)
        fee = abs(filled) * fill_price * (cost.fee_bps + cost.impact_bps(abs(filled), bar.volume)) / 10_000.0
        return filled, fill_price, fee, new_q
