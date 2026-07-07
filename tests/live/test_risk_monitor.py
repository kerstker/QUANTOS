"""仓位/回撤风险监控(live/execution/risk_monitor.py)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §9。

分两级(与 mandate 分级授权同一哲学):① 越限即 arm KillSwitch——用既有、已测试、"任何 actor
皆可触发"的安全机制,零新风险(halt 只会让系统更保守);② 减仓订单的**计算**(compute_flatten_orders)
是纯函数、已测试,但**是否/如何自动提交**是一个未解决的架构问题(kill-switch 一旦触发,既有
PreTradeRiskGate 会挡下所有意图,包括 reduce-only——见 spec §9 讨论),本文件不做提交决定。
"""
import pytest

from live.execution.risk_monitor import compute_flatten_orders, evaluate_risk_state, respond_to_breach
from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.killswitch import KillSwitchScope
from quantos.contracts.trading import OrderSide
from services.risk.pre_trade.kill_switch import KillSwitch


# --- evaluate_risk_state ---

def test_no_breach_when_within_all_limits():
    r = evaluate_risk_state(equity=95_000.0, peak_equity=100_000.0,
                            positions={"BTC": 0.01, "ETH": -1.0}, prices={"BTC": 60000.0, "ETH": 3000.0},
                            max_drawdown_pct=0.20, max_gross_exposure=10000.0, max_net_exposure=5000.0)
    assert r.breached is False and r.reasons == ()
    assert r.drawdown_pct == pytest.approx(0.05)
    assert r.gross_exposure == pytest.approx(600.0 + 3000.0)
    assert r.net_exposure == pytest.approx(600.0 - 3000.0)


def test_drawdown_breach_detected():
    r = evaluate_risk_state(equity=80_000.0, peak_equity=100_000.0, positions={},
                            prices={}, max_drawdown_pct=0.10, max_gross_exposure=1e9,
                            max_net_exposure=1e9)
    assert r.breached is True
    assert r.drawdown_pct == pytest.approx(0.20)
    assert any("drawdown_exceeded" in reason for reason in r.reasons)


def test_gross_exposure_breach_detected():
    r = evaluate_risk_state(equity=100_000.0, peak_equity=100_000.0,
                            positions={"BTC": 0.2}, prices={"BTC": 60000.0},
                            max_drawdown_pct=0.5, max_gross_exposure=5000.0, max_net_exposure=1e9)
    assert r.breached is True
    assert r.gross_exposure == pytest.approx(12000.0)
    assert any("gross_exposure_exceeded" in reason for reason in r.reasons)


def test_net_exposure_breach_detected():
    r = evaluate_risk_state(equity=100_000.0, peak_equity=100_000.0,
                            positions={"BTC": 0.05, "ETH": 0.1}, prices={"BTC": 60000.0, "ETH": 3000.0},
                            max_drawdown_pct=0.5, max_gross_exposure=1e9, max_net_exposure=1000.0)
    assert r.breached is True
    assert r.net_exposure == pytest.approx(3000.0 + 300.0)
    assert any("net_exposure_exceeded" in reason for reason in r.reasons)


def test_missing_price_fails_closed_as_a_breach():
    r = evaluate_risk_state(equity=100_000.0, peak_equity=100_000.0,
                            positions={"GHOST": 1.0}, prices={},
                            max_drawdown_pct=0.5, max_gross_exposure=1e9, max_net_exposure=1e9)
    assert r.breached is True
    assert any("missing_price_for" in reason and "GHOST" in reason for reason in r.reasons)


def test_zero_or_negative_peak_equity_fails_closed():
    r = evaluate_risk_state(equity=100.0, peak_equity=0.0, positions={}, prices={},
                            max_drawdown_pct=0.5, max_gross_exposure=1e9, max_net_exposure=1e9)
    assert r.breached is True
    assert "peak_equity_unknown" in r.reasons


def test_multiple_simultaneous_breaches_all_reported():
    r = evaluate_risk_state(equity=50_000.0, peak_equity=100_000.0,
                            positions={"BTC": 1.0}, prices={"BTC": 60000.0},
                            max_drawdown_pct=0.1, max_gross_exposure=1000.0, max_net_exposure=1000.0)
    assert r.breached is True
    reasons_str = ",".join(r.reasons)
    assert "drawdown_exceeded" in reasons_str
    assert "gross_exposure_exceeded" in reasons_str
    assert "net_exposure_exceeded" in reasons_str


# --- evaluate_risk_state: additive margin-ratio floor (Phase 5 item 2, all params default None) ---

def test_margin_params_default_none_byte_identical():
    # 新增 margin 参默认 None -> 与既有 no-breach 用例逐位相同(零回归)
    r = evaluate_risk_state(equity=95_000.0, peak_equity=100_000.0,
                            positions={"BTC": 0.01}, prices={"BTC": 60000.0},
                            max_drawdown_pct=0.20, max_gross_exposure=1e9, max_net_exposure=1e9)
    assert r.breached is False and r.reasons == ()


def test_margin_ratio_below_floor_adds_reason():
    r = evaluate_risk_state(equity=100_000.0, peak_equity=100_000.0, positions={}, prices={},
                            max_drawdown_pct=0.5, max_gross_exposure=1e9, max_net_exposure=1e9,
                            maint_margin_balance=1100.0, margin_maint_amount=1000.0,
                            min_margin_ratio=1.5)          # ratio 1.1 < 1.5 -> breach
    assert r.breached is True
    assert any("margin_ratio_below_floor" in x for x in r.reasons)


def test_margin_ratio_above_floor_no_reason():
    r = evaluate_risk_state(equity=100_000.0, peak_equity=100_000.0, positions={}, prices={},
                            max_drawdown_pct=0.5, max_gross_exposure=1e9, max_net_exposure=1e9,
                            maint_margin_balance=3000.0, margin_maint_amount=1000.0,
                            min_margin_ratio=1.5)          # ratio 3.0 >= 1.5 -> ok
    assert r.breached is False


def test_margin_no_maintenance_requirement_is_safe():
    # margin_maint_amount 0(无持仓/无维持保证金要求)-> 安全,不报(不是数据缺失)
    r = evaluate_risk_state(equity=100_000.0, peak_equity=100_000.0, positions={}, prices={},
                            max_drawdown_pct=0.5, max_gross_exposure=1e9, max_net_exposure=1e9,
                            maint_margin_balance=100.0, margin_maint_amount=0.0, min_margin_ratio=1.5)
    assert r.breached is False


def test_margin_incomplete_data_fails_closed_when_enabled():
    # 开启 min_margin_ratio 但余额缺 -> fail-closed 记一条 reason(数据不全 = 保守当越限)
    r = evaluate_risk_state(equity=100_000.0, peak_equity=100_000.0, positions={}, prices={},
                            max_drawdown_pct=0.5, max_gross_exposure=1e9, max_net_exposure=1e9,
                            maint_margin_balance=None, margin_maint_amount=1000.0, min_margin_ratio=1.5)
    assert r.breached is True
    assert any("margin_data_incomplete" in x for x in r.reasons)


# --- respond_to_breach ---

def test_no_breach_does_not_arm_kill_switch():
    ks = KillSwitch()
    r = evaluate_risk_state(equity=100_000.0, peak_equity=100_000.0, positions={}, prices={},
                            max_drawdown_pct=0.5, max_gross_exposure=1e9, max_net_exposure=1e9)
    out = respond_to_breach(kill_switch=ks, breach=r, at=1000)
    assert out["action"] == "none"
    assert ks.state.tripped is False


def test_breach_arms_kill_switch_with_system_actor():
    ks = KillSwitch()
    r = evaluate_risk_state(equity=50_000.0, peak_equity=100_000.0, positions={}, prices={},
                            max_drawdown_pct=0.1, max_gross_exposure=1e9, max_net_exposure=1e9)
    out = respond_to_breach(kill_switch=ks, breach=r, at=1000)
    assert out["action"] == "kill_switch_armed"
    assert ks.state.tripped is True
    assert ks.state.armed_by.kind == ActorKind.SYSTEM        # 非人 actor,自动触发的诚实标注
    assert ks.state.scope == KillSwitchScope.GLOBAL


def test_already_tripped_kill_switch_is_idempotent_no_double_arm():
    ks = KillSwitch()
    r = evaluate_risk_state(equity=50_000.0, peak_equity=100_000.0, positions={}, prices={},
                            max_drawdown_pct=0.1, max_gross_exposure=1e9, max_net_exposure=1e9)
    respond_to_breach(kill_switch=ks, breach=r, at=1000)
    trip_count_1 = ks.state.trip_count
    respond_to_breach(kill_switch=ks, breach=r, at=2000)      # 再次越限,不该重复 arm 制造噪音
    assert ks.state.trip_count == trip_count_1


# --- compute_flatten_orders(纯计算,未接自动提交——见 spec §9) ---

def test_flatten_orders_empty_when_already_within_target():
    orders = compute_flatten_orders({"BTC": 0.01}, {"BTC": 60000.0}, target_gross_exposure=10000.0)
    assert orders == []


def test_flatten_orders_scale_down_proportionally_toward_zero():
    # gross = 0.1*60000 + 2*3000 = 6000+6000=12000,target=6000 -> scale=0.5
    orders = compute_flatten_orders({"BTC": 0.1, "ETH": -2.0}, {"BTC": 60000.0, "ETH": 3000.0},
                                    target_gross_exposure=6000.0)
    by_inst = {o[0]: o for o in orders}
    assert by_inst["BTC"][1] == OrderSide.SELL and by_inst["BTC"][2] == pytest.approx(0.05)
    assert by_inst["ETH"][1] == OrderSide.BUY and by_inst["ETH"][2] == pytest.approx(1.0)


def test_flatten_orders_never_flip_direction_or_increase_exposure():
    orders = compute_flatten_orders({"BTC": 0.1}, {"BTC": 60000.0}, target_gross_exposure=1000.0)
    inst, side, qty = orders[0]
    assert side == OrderSide.SELL          # 平多只会卖,不会反手做空
    assert qty < 0.1                        # 只减不增


def test_flatten_orders_skips_missing_price_and_zero_positions():
    orders = compute_flatten_orders({"BTC": 0.1, "GHOST": 1.0, "FLAT": 0.0},
                                    {"BTC": 60000.0}, target_gross_exposure=1000.0)
    instruments = {o[0] for o in orders}
    assert "GHOST" not in instruments and "FLAT" not in instruments


def test_flatten_orders_empty_when_gross_is_zero():
    orders = compute_flatten_orders({}, {}, target_gross_exposure=1000.0)
    assert orders == []
