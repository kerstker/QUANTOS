"""交易所过滤器 + 冪等 client_order_id(Stage 1)。设计:
docs/engineering/specs/2026-07-04-live-execution-stack-design.md §3.4。

纯函数、无网络、无墙钟——输入输出皆为真实浮点数量(见 contracts.py 顶部关于两级下单类型的
说明)。`apply_exchange_filters` 是唯一把"策略想要的数量"折算成"交易所肯接受的数量"的地方:
步长取整、低于最小名义额/最小数量 fail-soft 丢弃(附原因,绝不因单笔失败中断整批)。

`round_to_step` 已搬到 `services/execution/quantization.py`(2026-07-08,F10 审计——回测/干跑
引擎需要同一份步长取整实现,而 `services/` 不能反向 import `live/`)。这里原样 re-export,逐字节
零行为变化(见 tests/live/test_filters.py);两边用的是同一份函数体,不是两份可能分叉的复制品。
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from live.contracts import ExchangeFilters, LiveOrderRequest
from quantos.contracts.trading import OrderSide
from services.execution.quantization import round_to_step

_MAX_CLIENT_ORDER_ID_LEN = 36     # Binance newClientOrderId 硬限制


def decision_prefix(strategy_id: str, decided_ts: int, instrument_id: str) -> str:
    """同一决策(策略+决策时间+标的)下所有订单共享的 16 位前缀——live-paper 对账用它把订单
    挂回决策腿(一条腿可能因调仓/重试产生多笔不同数量的订单)。"""
    key = f"{strategy_id}|{decided_ts}|{instrument_id}".encode()
    return hashlib.sha256(key).hexdigest()[:16]


def client_order_id(strategy_id: str, decided_ts: int, instrument_id: str,
                    side, quantity: float, limit_price: Optional[float] = None,
                    prior_qty: Optional[float] = None) -> str:
    """确定性订单 ID = 决策前缀 16 位 + 订单身份(方向+数量[+前仓][+限价])16 位,共 32 位 hex
    (<36 上限,留切单 -sNN 后缀余量)。

    **为什么必须包含方向+数量(2026-07-05 双所真实调仓暴露的关键 bug):** 旧键只有
    (策略, 决策时间, 标的)——同一决策时间下的第二批订单(mandate 提额后的调仓增量)与第一批
    **同 ID**:Binance 允许已成交 ID 复用,新单真实执行但账本 INSERT OR IGNORE 把记账当重复
    静默吞掉(仓位漂移不报错);Bybit 直接按 orderLinkId 去重没执行,轮询还查到旧单的 FILLED
    误报成功。正确的幂等语义是"同一笔订单的重试去重",订单身份 = 决策 + 方向 + 数量。

    **prior_qty(2026-07-06 审计 P1-3):** 加方向+数量只把碰撞从"必然"收窄到"同数量"——同一
    decided_ts、同标的、同方向、**同数量**的两笔逻辑上不同的调仓增量(current=0→delta 5.0,后
    current=5→delta 5.0)仍然撞 ID。这两笔的真正区别是**下单时的前仓位**(current)。把它纳入
    身份即可区分,同时**保留崩溃重建幂等**:对同一 current 重算 → 同 ID → 交易所去重;若前仓已因
    未记账的成交移动,则 delta 本身改变、不会重发同一笔。默认 None → 与改动前逐字节相同(旧调用
    方/golden 测试安全),仅 live 下单口径(submit_live_batch)传入激活。

    **limit_price(2026-07-06 战线②):** 默认 None,输出与改动前逐字节相同(golden 测试
    钉死——已在途的 MARKET 幂等链不许漂移)。LIMIT 改价重发必须是不同订单(同一决策同数量但
    不同限价,交易所不能把它当同一笔订单的重试去重),同限价重试仍幂等。"""
    ident = f"{getattr(side, 'value', side)}|{quantity:.10f}"
    if prior_qty is not None:
        ident += f"|P{prior_qty:.10f}"
    if limit_price is not None:
        ident += f"|L{limit_price:.10f}"
    return decision_prefix(strategy_id, decided_ts, instrument_id) + \
        hashlib.sha256(ident.encode()).hexdigest()[:16]


def apply_exchange_filters(desired: Sequence[Tuple[str, OrderSide, float]],
                           filters: Mapping[str, ExchangeFilters],
                           prices: Mapping[str, float], *,
                           strategy_id: str, decided_ts: int,
                           current_positions: Optional[Mapping[str, float]] = None
                           ) -> Tuple[List[LiveOrderRequest], List[Tuple[str, str]]]:
    """`desired` = [(instrument_id, side, real_qty), ...](真实浮点数量,取整前)。

    每笔独立评估,fail-soft:任何一笔被丢弃都不影响其它笔。丢弃原因(附在 dropped 里):
    "no_filter"(交易所未提供该标的过滤器)/ "below_min_qty"(取整后低于最小数量,含取整到 0)/
    "no_price"(缺参考价,无法算名义额)/ "below_min_notional"(数量*价格低于最小名义额)。

    `current_positions`(审计 P1-3):可选的下单口径前仓位——传入时纳入 client_order_id 身份,
    使同 decided_ts、同数量、不同前仓的两笔逻辑不同增量得到不同 ID(见 client_order_id docstring)。
    None(默认)→ 输出与改动前逐字节相同(emergency 通道等旧调用方安全)。
    """
    adjusted: List[LiveOrderRequest] = []
    dropped: List[Tuple[str, str]] = []
    for instrument_id, side, qty in desired:
        filt = filters.get(instrument_id)
        if filt is None:
            dropped.append((instrument_id, "no_filter"))
            continue
        rounded = round_to_step(qty, filt.step_size)
        if rounded <= 0 or rounded < filt.min_qty:
            dropped.append((instrument_id, "below_min_qty"))
            continue
        price = prices.get(instrument_id)
        if price is None or price <= 0:
            dropped.append((instrument_id, "no_price"))
            continue
        if rounded * price < filt.min_notional:
            dropped.append((instrument_id, "below_min_notional"))
            continue
        prior = None if current_positions is None else float(current_positions.get(instrument_id, 0.0))
        cid = client_order_id(strategy_id, decided_ts, instrument_id, side, rounded, prior_qty=prior)
        adjusted.append(LiveOrderRequest(instrument_id=instrument_id, side=side,
                                         quantity=rounded, client_order_id=cid))
    return adjusted, dropped
