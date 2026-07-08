"""live/execution/gate_context.py —— Gate 账户上下文去虚构(2026-07-05,机构审计 P1,
"证据最干净"的一条)。

背景:`make_ctx_factory` 此前把 `AccountRiskState` 硬编码成 positions=()/day_pnl=0.0/
peak_equity=aum——账户级检查(仓位限额/集中度/敞口/杠杆/日内止损/回撤)全部对着一个虚构的
"从未持仓、当天零盈亏、峰值等于当前权益"的账户评估,结构性永久失效(双层防御实质变单层,
只有 mandate 那层在用真实数据)。本模块把真实账本仓位折算成 gate 认识的 `Position` 序列
(与既有 OrderIntent 同一缩放单位约定)。
"""
import pytest

from live.execution.gate_context import build_gate_positions
from quantos.contracts.portfolio import Position


def test_builds_position_per_nonzero_instrument_scaled_to_intent_units():
    positions = build_gate_positions({"BTC": 0.015, "ETH": -2.0}, {"BTC": 60000.0, "ETH": 3000.0},
                                     unit_scale=1_000_000)
    by_inst = {p.instrument_id: p for p in positions}
    assert set(by_inst) == {"BTC", "ETH"}
    # 缩放到 gate 的整数单位(1 unit = 1e-6 币,同 live/pipeline.py::UNIT_SCALE 约定)
    assert by_inst["BTC"].quantity == 15_000
    assert by_inst["ETH"].quantity == -2_000_000
    # market_value 是真实美元名义额(带符号)
    assert by_inst["BTC"].market_value == pytest.approx(0.015 * 60000.0)
    assert by_inst["ETH"].market_value == pytest.approx(-2.0 * 3000.0)


def test_zero_quantity_position_is_excluded():
    positions = build_gate_positions({"BTC": 0.0}, {"BTC": 60000.0}, unit_scale=1_000_000)
    assert positions == ()


def test_missing_price_falls_back_to_zero_market_value_not_a_crash():
    # 缺价的持仓依然算进 quantity(gate 的仓位数量检查不该因缺价就假装没有这个仓位),
    # 但 market_value 诚实为 0(无法估值,不是"值 0 美元"的断言,只是"我们不知道")——
    # 这与既有 apply_exchange_filters 遇缺价时的"诚实缺失"处理姿态一致
    positions = build_gate_positions({"GHOST": 5.0}, {}, unit_scale=1_000_000)
    assert len(positions) == 1
    assert positions[0].quantity == 5_000_000
    assert positions[0].market_value == 0.0


def test_returns_real_position_dataclass():
    positions = build_gate_positions({"BTC": 1.0}, {"BTC": 100.0}, unit_scale=1_000_000)
    assert isinstance(positions[0], Position)
