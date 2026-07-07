"""Reduce-only 豁免(2026-07-07):**降低敞口的单永远不该被 ADD-side 市场风险检查挡住**(fail-safe 也有方向,
对退出/减仓 fail-closed 是错的)。豁免从**真实当前仓位**推导(非可伪造的调用方标志):pos≠0 且 side 反向 且
|delta|≤|pos|(不穿越零点);pos=0/未知 -> False(fail-closed 保留检查)。只豁免 liquidity/volatility;**绝不**
豁免 kill-switch/net_exposure/position_limit 等。镜像 live/execution/emergency.validate_flatten_order 的证明。"""
from quantos.contracts.killswitch import KillSwitchState
from quantos.contracts.portfolio import Position
from quantos.contracts.rejection import RejectionReason as R
from quantos.contracts.trading import OrderIntent, OrderSide, OrderType
from services.risk.pre_trade import checks as C
from services.risk.pre_trade import (AccountRiskState, MarketContext, RiskContext, RiskLimits,
                                      StrategyApproval)
from services.risk.pre_trade.checks import _is_exposure_reducing

L = RiskLimits(allowed_instruments=frozenset({"AAA"}), allowed_accounts=frozenset({"acct1"}))


def _intent(side, qty, inst="AAA"):
    return OrderIntent(instrument_id=inst, side=side, quantity=qty, order_type=OrderType.MARKET,
                       strategy_id="s1")


def _ctx(positions=(), *, est_vol=1e6, ivol=0.2, kill=KillSwitchState()):
    return RiskContext(
        account=AccountRiskState(account_id="acct1", cash=1e6, equity=1e6, peak_equity=1e6,
                                 positions=positions),
        market=MarketContext(instrument_id="AAA", known=True, reference_price=100.0, market_open=True,
                             price_as_of=1000, est_daily_volume=est_vol, instrument_vol=ivol),
        strategy=StrategyApproval(strategy_id="s1", approved=True, approval_id="ap1"),
        kill_switch=kill, now=1000)


def _pos(qty):
    return (Position(instrument_id="AAA", quantity=qty, market_value=qty * 100.0),)


# --- truth table ---

def test_reducing_true_only_for_opposing_within_position():
    assert _is_exposure_reducing(_intent(OrderSide.SELL, 5), _ctx(_pos(10))) is True   # 平多一部分
    assert _is_exposure_reducing(_intent(OrderSide.SELL, 10), _ctx(_pos(10))) is True  # 平多到零
    assert _is_exposure_reducing(_intent(OrderSide.BUY, 5), _ctx(_pos(-10))) is True   # 平空一部分


def test_reducing_false_for_adds_flips_and_flat():
    assert _is_exposure_reducing(_intent(OrderSide.BUY, 5), _ctx(_pos(10))) is False   # 同向加多
    assert _is_exposure_reducing(_intent(OrderSide.SELL, 5), _ctx(_pos(-10))) is False # 同向加空
    assert _is_exposure_reducing(_intent(OrderSide.SELL, 16), _ctx(_pos(10))) is False # 穿越零点(翻空)
    assert _is_exposure_reducing(_intent(OrderSide.SELL, 5), _ctx(())) is False         # 无仓(纯开)-> fail-closed
    assert _is_exposure_reducing(_intent(OrderSide.BUY, 5), _ctx(())) is False


# --- liquidity/volatility 豁免(核心:能安全激活 ADV/vol)---

def test_liquidity_exempts_reduce_but_not_add():
    # 缺 ADV(est_daily_volume=None):平仓单豁免过,加仓单仍 fail-closed 拒
    assert C.check_liquidity(_intent(OrderSide.SELL, 5), _ctx(_pos(10), est_vol=None), L) == ()
    assert C.check_liquidity(_intent(OrderSide.BUY, 5), _ctx(_pos(10), est_vol=None), L) == (R.LIQUIDITY_LIMIT_EXCEEDED,)


def test_volatility_exempts_reduce_but_not_add():
    assert C.check_volatility(_intent(OrderSide.SELL, 5), _ctx(_pos(10), ivol=None), L) == ()
    assert C.check_volatility(_intent(OrderSide.BUY, 5), _ctx(_pos(10), ivol=None), L) == (R.VOLATILITY_LIMIT_EXCEEDED,)


def test_missing_position_fails_closed_keeps_check():
    # reduce 形状但无仓(平仓证据不可得)-> 不豁免 -> 仍拒(锁死 dry-run positions=() 字节等价)
    assert C.check_liquidity(_intent(OrderSide.SELL, 5), _ctx((), est_vol=None), L) == (R.LIQUIDITY_LIMIT_EXCEEDED,)


# --- 绝不豁免:kill-switch / net_exposure / position_limit(防越权 + 非单调) ---

def test_kill_switch_still_halts_a_reduce():
    # kill-switch 跳闸挡下一切(含减仓)——受制裁的减仓路径是独立 emergency 通道,不是共用 gate
    r = C.check_kill_switch(_intent(OrderSide.SELL, 5), _ctx(_pos(10), kill=KillSwitchState(tripped=True)), L)
    assert r == (R.KILL_SWITCH_ACTIVE,)


def test_position_limit_still_rejects_flip_through_zero():
    # 穿越零点(SELL 16 vs long 10 -> proj -6)不是 reduce,position_limit 照常拒(防借"减仓"翻仓)
    tight = RiskLimits(allowed_instruments=frozenset({"AAA"}), allowed_accounts=frozenset({"acct1"}),
                       max_position_qty=(("AAA", 5),))
    assert C.check_position_limit(_intent(OrderSide.SELL, 16), _ctx(_pos(10)), tight) == (R.POSITION_LIMIT_EXCEEDED,)


def test_net_exposure_not_exempted_for_reduce():
    # 逐标的减仓可能增大账户 |net|(非单调)-> net_exposure 刻意不豁免。构造:AAA 多 + 别处大空,SELL AAA 使
    # net 更负越限。net_exposure 应仍拒(证明未被豁免)。
    positions = (Position(instrument_id="AAA", quantity=10, market_value=1000.0),
                 Position(instrument_id="BBB", quantity=-100, market_value=-10000.0))
    tight = RiskLimits(allowed_instruments=frozenset({"AAA", "BBB"}), allowed_accounts=frozenset({"acct1"}),
                       max_net_exposure=9500.0)
    # 当前 net = 1000 - 10000 = -9000(在 9500 内);SELL AAA 5 -> AAA proj 5*100=500,new_net = -9000 -1000 +500 = -9500 -> abs=9500 未越;
    # SELL AAA 10 -> proj 0,new_net = -9000 -1000 +0 = -10000 -> abs 10000 > 9500 越限
    assert C.check_net_exposure(_intent(OrderSide.SELL, 10), _ctx(positions), tight) == (R.POSITION_LIMIT_EXCEEDED,)
