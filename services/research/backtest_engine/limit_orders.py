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
    """限价成交模型 + 排队启发式。

    expire_bars 是订单的**成交尝试次数**(下单那根 bar 的收盘不撮合,从次根 bar 开盘起
    每根 bar 一次撮合机会;到第 expire_bars 次仍未成交则撤销),不等同于订单"存在"的
    日历 bar 数。
    """
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
        # 触及判定用区间(bar.low<=L),不用精确相等:L=close×(1∓off) 是派生浮点,真实数据上
        # bar.low==L 几乎不可达,会让排队分支在生产数据上变死代码(审查 F7)。全额成交只保留给
        # 真正的跳空——开盘价已穿过限价(你被扫,marketable);其余盘中触及一律走 participation+
        # 排队的部分成交(保守:不高估可执行性,审查 F1)。
        L = order.limit_price
        new_q = order.queue_ahead
        if order.side > 0:                                   # 买
            if bar.low > L:                                  # 未触及(全程在限价上方)
                return 0, 0.0, 0.0, new_q
            if bar.open <= L:                                # 跳空开在限价下方 → marketable 全成
                filled = order.qty_remaining
                base_price = bar.open                        # = min(open, L)
                new_q = 0.0
            else:                                            # 盘中下探触及 → 部分成交,排队生效
                vol_at = self.participation * bar.volume
                fillable = max(0.0, vol_at - order.queue_ahead)
                filled = min(order.qty_remaining, int(fillable))
                new_q = max(0.0, order.queue_ahead - vol_at)
                if filled == 0:
                    return 0, 0.0, 0.0, new_q
                base_price = L
        else:                                                # 卖(qty_remaining 为负)
            if bar.high < L:                                 # 未触及(全程在限价下方)
                return 0, 0.0, 0.0, new_q
            if bar.open >= L:                                # 跳空开在限价上方 → marketable 全成
                filled = order.qty_remaining
                base_price = bar.open                        # = max(open, L)
                new_q = 0.0
            else:                                            # 盘中上冲触及 → 部分成交,排队生效
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
