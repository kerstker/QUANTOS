"""VenueAdapter 协议(Stage 1)—— venue-agnostic 执行接缝。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.2。

Binance 是本阶段唯一实现(`binance_futures.py`);Bybit/Hyperliquid 后续实现同一协议
(用户已拍板"venue 可插拔")。这是执行侧的协议,与 `services/trading/broker_adapter.py::
BrokerAdapter`(干跑流水线专用,消费 `DryRunCommand`/`DryRunAdapterRun`)刻意分开——
那个协议的存在是为了让 `assert_dry_run_adapter` 能诚实地只放行"证明为干跑"的适配器进
干跑流水线;这里的 `VenueAdapter` 是走真实(哪怕测试网)网络请求的执行栈协议,两者不应
合并,合并会让 `assert_dry_run_adapter` 的"只放行干跑"承诺失真。
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from live.contracts import ExchangeFilters, LiveOrderRequest, VenueOrderResult, VenuePosition


@runtime_checkable
class VenueAdapter(Protocol):
    venue_mode: str            # "testnet" | "mainnet"
    venue_name: str            # "binance" | "bybit" | ...(mandate 交易所域匹配用,2026-07-05)

    def place_order(self, order: LiveOrderRequest) -> VenueOrderResult: ...

    def get_order(self, instrument_id: str, client_order_id: str) -> Optional[VenueOrderResult]: ...

    # 撤单(2026-07-05 OMS 补全):订单不存在/已终态无法撤 -> None(与 get_order 的"语义化不存在"
    # 口径一致)。撤单 ACK 不保证已撤成(可能与成交赛跑)——调用方必须再 get_order 回查权威状态。
    def cancel_order(self, instrument_id: str, client_order_id: str) -> Optional[VenueOrderResult]: ...

    def account(self) -> dict: ...                        # 原始诊断形状,不用于对账

    def positions(self) -> dict[str, VenuePosition]: ...   # 规范化仓位——reconcile_positions 的输入

    def exchange_filters(self) -> dict[str, ExchangeFilters]: ...

    # 只读盘口(2026-07-06 战线②,LIMIT 定价锚来源):fail-soft——网络失败/解析失败一律
    # 返回 None(合法),调用方(live/execution/pricing.py::resolve_anchor)据此回退决策价锚。
    # 绝不因盘口拿不到而抛异常打断整批。
    def book_ticker(self, instrument_id: str) -> Optional[dict]: ...
