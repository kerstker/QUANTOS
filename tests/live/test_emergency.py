"""live/execution/emergency.py —— 紧急减仓独立通道(2026-07-05,用户拍板"独立紧急通道")。
设计:docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md §②。

关键性质:
① 结构上只可能减少敞口——方向必须与持仓相反、数量 ≤ |持仓|(减到零可以,反向开仓绝对不行)、
   订单强制 reduce_only=True(交易所侧第二重保证);
② 不经过共用 PreTradeRiskGate/kill-switch——kill switch 已触发时依然能送出减仓单,这正是
   这条通道存在的意义(共用 gate 的 check_kill_switch 会挡下包括 reduce-only 在内的一切);
③ 全程审计(GlobalAuditLog);
④ 成交确认沿用"轮询终态 + 超时撤单回查"的完整机制;
⑤ 交易所过滤器失败时刻意 fail-open 提交原始数量——与加仓路径的 fail-closed 相反:对
   减仓单来说"不提交"才是危险动作(敞口继续暴露),交易所自己是最终校验者。
"""
import pytest

from live.execution.emergency import (flatten_target_for_breach, submit_emergency_flatten,
                                      validate_flatten_order)
from live.execution.position_ledger import LiveLedger
from live.execution.risk_monitor import RiskBreach, compute_flatten_orders
from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.trading import OrderSide
from services.monitoring.audit_log import GlobalAuditLog
from services.risk.pre_trade.kill_switch import KillSwitch

from test_pipeline import _PRICES, _FakeVenue, _ExplodingFiltersVenue, _LedgerThatBreaksOn

_NOW = 1_700_000_000_000


@pytest.fixture()
def ledger(tmp_path):
    return LiveLedger(str(tmp_path / "live.db"), create=True)


def _prefill(ledger, positions, *, strategy_id="crowd_contra"):
    """把"当前持仓"预填进账本(模拟历史成交的累计结果),让减仓后的净仓位有真实语义。"""
    i = 0
    for inst, qty in positions.items():
        side = OrderSide.BUY if qty > 0 else OrderSide.SELL
        ledger.record_fill(f"pre-{i}", f"pre-order-{i}", inst, side, abs(qty),
                           _PRICES[inst], strategy_id=strategy_id, filled_at=_NOW - 1000)
        i += 1


# --- validate_flatten_order:结构性"只能减仓"校验(纯函数) ---

def test_validate_long_position_sell_within_qty_ok():
    assert validate_flatten_order(OrderSide.SELL, 0.5, position=1.0) is None


def test_validate_long_position_buy_rejected():
    assert validate_flatten_order(OrderSide.BUY, 0.5, position=1.0) is not None


def test_validate_short_position_buy_ok():
    assert validate_flatten_order(OrderSide.BUY, 0.5, position=-1.0) is None


def test_validate_short_position_sell_rejected():
    assert validate_flatten_order(OrderSide.SELL, 0.5, position=-1.0) is not None


def test_validate_qty_exceeding_position_rejected():
    # 超量执行会穿过零点反向开仓——绝对禁止
    assert validate_flatten_order(OrderSide.SELL, 1.5, position=1.0) is not None


def test_validate_flatten_to_exactly_zero_is_allowed():
    assert validate_flatten_order(OrderSide.SELL, 1.0, position=1.0) is None


def test_validate_flat_position_rejected():
    assert validate_flatten_order(OrderSide.SELL, 0.5, position=0.0) is not None


def test_validate_nonpositive_qty_rejected():
    assert validate_flatten_order(OrderSide.SELL, 0.0, position=1.0) is not None
    assert validate_flatten_order(OrderSide.SELL, -0.5, position=1.0) is not None


def test_validate_exhaustive_grid_only_strictly_reducing_orders_pass():
    """性质网格:任意 持仓×方向×数量 组合,放行 <=> 严格减少 |敞口| 且绝不穿过零点反向。"""
    for pos in (-2.0, -0.5, 0.0, 0.5, 2.0):
        for side in (OrderSide.BUY, OrderSide.SELL):
            for qty in (-1.0, 0.0, 0.3, abs(pos), abs(pos) + 0.1, 5.0):
                verdict = validate_flatten_order(side, qty, position=pos)
                signed = qty if side == OrderSide.BUY else -qty
                new_pos = pos + signed
                reduces = (qty > 0 and pos != 0.0
                           and abs(new_pos) < abs(pos) - 1e-12
                           and new_pos * pos >= 0)            # 减到 0 允许,变号禁止
                allows_exact_zero = (qty > 0 and pos != 0.0 and abs(new_pos) < 1e-12
                                     and ((side == OrderSide.SELL) == (pos > 0)))
                assert (verdict is None) == (reduces or allows_exact_zero), \
                    (pos, side, qty, verdict)


# --- flatten_target_for_breach:越限类型 -> 减仓目标(纯函数策略) ---

def _breach(reasons, *, gross=1000.0, net=100.0, dd=0.0):
    return RiskBreach(breached=bool(reasons), reasons=tuple(reasons), drawdown_pct=dd,
                      gross_exposure=gross, net_exposure=net)


def test_drawdown_breach_targets_full_flatten():
    b = _breach(["drawdown_exceeded:0.25>0.2"], dd=0.25)
    assert flatten_target_for_breach(b, max_gross_exposure=2000.0, max_net_exposure=300.0) == 0.0


def test_gross_breach_targets_back_under_cap_with_margin():
    b = _breach(["gross_exposure_exceeded:2500.00>2000.0"], gross=2500.0)
    t = flatten_target_for_breach(b, max_gross_exposure=2000.0, max_net_exposure=300.0)
    assert t == pytest.approx(1800.0)                        # 0.9 * cap,留余量防止立刻再次越限


def test_net_breach_scales_gross_down_enough_to_clear_net():
    # 等比缩减下 net 与 gross 同因子缩——目标必须缩到 |net| 也回到 0.9*net_cap 以内
    b = _breach(["net_exposure_exceeded:600.00>300.0"], gross=1000.0, net=600.0)
    t = flatten_target_for_breach(b, max_gross_exposure=2000.0, max_net_exposure=300.0)
    assert t == pytest.approx(1000.0 * (0.9 * 300.0 / 600.0))


def test_data_quality_only_breach_does_not_auto_flatten():
    # "数据不完整"要保守停机+告警,但不能在不可信的数据上自动交易(那可能是错误方向的减仓)
    b = _breach(["missing_price_for:GHOST"], gross=100.0, net=10.0)
    assert flatten_target_for_breach(b, max_gross_exposure=2000.0, max_net_exposure=300.0) is None
    b2 = _breach(["peak_equity_unknown"])
    assert flatten_target_for_breach(b2, max_gross_exposure=2000.0, max_net_exposure=300.0) is None


def test_no_breach_returns_none():
    b = _breach([])
    assert flatten_target_for_breach(b, max_gross_exposure=2000.0, max_net_exposure=300.0) is None


# --- submit_emergency_flatten:提交编排 ---

def test_full_flatten_submits_reduce_only_and_records_fills(ledger):
    positions = {"BTC": 2.0, "ETH": -50.0}
    _prefill(ledger, positions)
    orders = compute_flatten_orders(positions, _PRICES, target_gross_exposure=0.0)
    venue = _FakeVenue()
    audit = GlobalAuditLog()
    result = submit_emergency_flatten(flatten_orders=orders, positions=positions, prices=_PRICES,
                                      venue=venue, ledger=ledger, audit=audit, now=_NOW)
    assert len(result.submitted) == 2
    assert result.rejected == () and result.errors == ()
    assert all(o.reduce_only for o in venue.calls)           # 交易所侧强制 reduce-only
    assert ledger.position("BTC") == pytest.approx(0.0)      # 2.0 - 2.0
    assert ledger.position("ETH") == pytest.approx(0.0)      # -50 + 50
    actions = [e.action for e in audit.events]
    assert "EMERGENCY_FLATTEN_START" in actions
    assert actions.count("EMERGENCY_ORDER_SUBMITTED") == 2
    assert audit.chain_valid()


def test_emergency_channel_works_while_kill_switch_is_tripped(ledger):
    # 这条通道存在的意义:kill switch 已触发时,共用 gate 会挡下一切(包括减仓单),
    # 紧急通道根本不咨询它——结构独立,零参数耦合
    ks = KillSwitch()
    ks.arm(Actor(actor_id="t", kind=ActorKind.SYSTEM), "test breach", at=_NOW)
    assert ks.state.tripped
    positions = {"BTC": 1.0}
    _prefill(ledger, positions)
    orders = compute_flatten_orders(positions, _PRICES, target_gross_exposure=0.0)
    venue = _FakeVenue()
    result = submit_emergency_flatten(flatten_orders=orders, positions=positions, prices=_PRICES,
                                      venue=venue, ledger=ledger, audit=GlobalAuditLog(), now=_NOW)
    assert len(result.submitted) == 1                        # 依然送得出去
    assert ledger.position("BTC") == pytest.approx(0.0)


def test_exposure_increasing_order_is_rejected_and_never_reaches_venue(ledger):
    positions = {"BTC": 2.0}
    orders = [("BTC", OrderSide.BUY, 1.0)]                   # 加仓方向——恶意/bug 输入
    venue = _FakeVenue()
    audit = GlobalAuditLog()
    result = submit_emergency_flatten(flatten_orders=orders, positions=positions, prices=_PRICES,
                                      venue=venue, ledger=ledger, audit=audit, now=_NOW)
    assert result.submitted == () and venue.calls == []
    assert len(result.rejected) == 1
    assert "EMERGENCY_ORDER_REJECTED" in [e.action for e in audit.events]


def test_overflatten_order_is_rejected(ledger):
    positions = {"BTC": 2.0}
    orders = [("BTC", OrderSide.SELL, 3.0)]                  # 超量——会穿仓反向
    venue = _FakeVenue()
    result = submit_emergency_flatten(flatten_orders=orders, positions=positions, prices=_PRICES,
                                      venue=venue, ledger=ledger, audit=GlobalAuditLog(), now=_NOW)
    assert result.submitted == () and venue.calls == []
    assert len(result.rejected) == 1


def test_unknown_instrument_order_is_rejected(ledger):
    orders = [("GHOST", OrderSide.SELL, 1.0)]                # 无持仓标的
    venue = _FakeVenue()
    result = submit_emergency_flatten(flatten_orders=orders, positions={"BTC": 2.0}, prices=_PRICES,
                                      venue=venue, ledger=ledger, audit=GlobalAuditLog(), now=_NOW)
    assert result.submitted == () and len(result.rejected) == 1


def test_exchange_filters_round_quantity_down(ledger):
    positions = {"BTC": 2.00006}
    _prefill(ledger, positions)
    orders = [("BTC", OrderSide.SELL, 2.00006)]
    venue = _FakeVenue()
    result = submit_emergency_flatten(flatten_orders=orders, positions=positions, prices=_PRICES,
                                      venue=venue, ledger=ledger, audit=GlobalAuditLog(), now=_NOW)
    assert len(result.submitted) == 1
    assert venue.calls[0].quantity == pytest.approx(2.0)     # step=0.0001 向下取整,绝不超发


def test_filters_failure_fails_open_and_submits_raw_quantity(ledger):
    # 刻意与加仓路径相反的不对称:减仓单"不提交"才是危险动作。过滤器拿不到 -> 提交原始数量,
    # 交易所自己是最终校验者(被拒也只是保持现状,不比不试更糟)。
    positions = {"BTC": 2.0}
    _prefill(ledger, positions)
    orders = [("BTC", OrderSide.SELL, 2.0)]
    venue = _ExplodingFiltersVenue()
    audit = GlobalAuditLog()
    result = submit_emergency_flatten(flatten_orders=orders, positions=positions, prices=_PRICES,
                                      venue=venue, ledger=ledger, audit=audit, now=_NOW)
    assert len(result.submitted) == 1
    assert venue.calls[0].quantity == pytest.approx(2.0)
    assert "EMERGENCY_FILTERS_UNAVAILABLE" in [e.action for e in audit.events]


def test_confirm_timeout_cancels_zombie_emergency_order(ledger):
    positions = {"BTC": 2.0}
    _prefill(ledger, positions)
    orders = [("BTC", OrderSide.SELL, 2.0)]
    venue = _FakeVenue(never_confirms_for={"BTC"})
    result = submit_emergency_flatten(flatten_orders=orders, positions=positions, prices=_PRICES,
                                      venue=venue, ledger=ledger, audit=GlobalAuditLog(), now=_NOW,
                                      max_confirm_polls=2)
    assert len(result.submitted) == 1
    assert result.submitted[0].status == "CANCELED"          # 超时撤单回查的终态
    assert venue.cancel_calls != []
    assert ledger.position("BTC") == pytest.approx(2.0)      # 零成交,仓位未动


def test_place_order_exception_requeries_before_declaring_failure(ledger):
    # 响应丢失但订单真实存在:回查 get_order 恢复,不误判失败(镜像主流水线同一教训)
    positions = {"BTC": 2.0}
    _prefill(ledger, positions)
    orders = [("BTC", OrderSide.SELL, 2.0)]
    venue = _FakeVenue(fail_recoverable_for={"BTC"})
    result = submit_emergency_flatten(flatten_orders=orders, positions=positions, prices=_PRICES,
                                      venue=venue, ledger=ledger, audit=GlobalAuditLog(), now=_NOW)
    assert result.errors == ()
    assert len(result.submitted) == 1 and result.submitted[0].status == "FILLED"
    assert ledger.position("BTC") == pytest.approx(0.0)


def test_genuine_submit_failure_is_fail_soft_per_order(ledger):
    positions = {"BTC": 2.0, "ETH": -50.0}
    _prefill(ledger, positions)
    orders = compute_flatten_orders(positions, _PRICES, target_gross_exposure=0.0)
    venue = _FakeVenue(fail_unrecoverable_for={"BTC"})
    result = submit_emergency_flatten(flatten_orders=orders, positions=positions, prices=_PRICES,
                                      venue=venue, ledger=ledger, audit=GlobalAuditLog(), now=_NOW)
    assert len(result.errors) == 1                           # BTC 真失败
    assert len(result.submitted) == 1                        # ETH 不受影响
    assert ledger.position("ETH") == pytest.approx(0.0)


def test_ledger_write_failure_tracked_separately_not_as_submit_failure(ledger):
    positions = {"BTC": 2.0}
    orders = [("BTC", OrderSide.SELL, 2.0)]
    broken = _LedgerThatBreaksOn(ledger, break_for={"BTC"})
    venue = _FakeVenue()
    result = submit_emergency_flatten(flatten_orders=orders, positions=positions, prices=_PRICES,
                                      venue=venue, ledger=broken, audit=GlobalAuditLog(), now=_NOW)
    assert result.errors == ()
    assert len(result.submitted) == 1                        # venue 侧真实成功
    assert len(result.ledger_write_errors) == 1


def test_empty_flatten_orders_is_a_noop(ledger):
    venue = _FakeVenue()
    result = submit_emergency_flatten(flatten_orders=[], positions={}, prices=_PRICES,
                                      venue=venue, ledger=ledger, audit=GlobalAuditLog(), now=_NOW)
    assert result.submitted == () and venue.calls == []


# --- 2026-07-06 战线③:紧急减仓成交记入保留账本 emergency_flatten ---

def test_emergency_fills_use_reserved_strategy_id(ledger):
    from live.execution.emergency import EMERGENCY_STRATEGY_ID
    positions = {"BTC": 2.0}                                    # 账户级 venue 仓位
    _prefill(ledger, positions, strategy_id="strat_a")          # 本地分账里这是 strat_a 的仓
    orders = compute_flatten_orders(positions, _PRICES, target_gross_exposure=0.0)
    venue = _FakeVenue()
    result = submit_emergency_flatten(flatten_orders=orders, positions=positions, prices=_PRICES,
                                      venue=venue, ledger=ledger, audit=GlobalAuditLog(), now=_NOW)
    assert len(result.submitted) == 1
    emg_fills = ledger.fills(strategy_id=EMERGENCY_STRATEGY_ID)
    assert len(emg_fills) == 1 and emg_fills[0]["instrument_id"] == "BTC"
    # 账户级立即含减仓效果;strat_a 的分账不变("它以为自己持有的仓"),差额落在保留账本里,
    # 分区恒等式不破——事后重归属是事故复盘的人工决定
    assert ledger.all_positions().get("BTC", 0.0) == pytest.approx(0.0)
    assert ledger.all_positions(strategy_id="strat_a")["BTC"] == pytest.approx(2.0)
    assert ledger.all_positions(strategy_id=EMERGENCY_STRATEGY_ID)["BTC"] == pytest.approx(-2.0)
    # TCA 同样落保留账本
    assert all(t["strategy_id"] == EMERGENCY_STRATEGY_ID for t in ledger.tca_records())
