"""#3 限价单 + 排队位置:LimitFill 配置/撮合 + 引擎集成(跨 bar 挂单、生命周期)。
纯 bar 数据下诚实建模非执行风险与排队位置;contained + deterministic + 对账恒等式保持。"""
import pytest
from quantos.contracts.data import Bar
from services.research.backtest_engine.costs import CostModel
from services.research.backtest_engine.limit_orders import LimitFill, RestingOrder
from services.research.backtest_engine.event_engine import run_event_backtest


def _bar(o, h, l, c, v=10000):
    return Bar(instrument_id="x", event_time=0, knowledge_time=0,
               open=o, high=h, low=l, close=c, volume=v)


_NOCOST = CostModel(fee_bps=0.0, slippage_bps=0.0)


# ---------------- Task 1: 配置 + 状态 ----------------

def test_limitfill_rejects_bad_params():
    with pytest.raises(ValueError):
        LimitFill(participation=0.0)
    with pytest.raises(ValueError):
        LimitFill(participation=1.5)
    with pytest.raises(ValueError):
        LimitFill(queue_fraction=-0.1)
    with pytest.raises(ValueError):
        LimitFill(expire_bars=0)
    with pytest.raises(ValueError):
        LimitFill(limit_offset_bps=-1.0)


def test_limit_price_buy_below_sell_above():
    lf = LimitFill(limit_offset_bps=10.0)            # 10 bps = 0.001
    assert lf.limit_price(1, 100.0) == pytest.approx(100.0 * (1 - 0.001))
    assert lf.limit_price(-1, 100.0) == pytest.approx(100.0 * (1 + 0.001))


def test_initial_queue_is_fraction_of_volume():
    lf = LimitFill(queue_fraction=0.25)

    class _B:
        volume = 10000
    assert lf.initial_queue(_B()) == pytest.approx(2500.0)


# ---------------- Task 2: match 单 bar 撮合 ----------------

def test_match_buy_not_touched():
    lf = LimitFill()
    order = RestingOrder("x", 1, 100, limit_price=99.0, queue_ahead=0.0, bars_alive=0)
    filled, price, c, q = lf.match(order, _bar(100, 101, 99.5, 100.5), _NOCOST)
    assert filled == 0 and q == 0.0


def test_match_buy_through_fills_full_at_min_open_limit():
    lf = LimitFill()
    order = RestingOrder("x", 1, 100, limit_price=99.0, queue_ahead=1e9, bars_alive=0)
    filled, price, c, q = lf.match(order, _bar(98.0, 100, 97.0, 99.5), _NOCOST)
    assert filled == 100                       # 穿透 → 全成,排队无关
    assert price == pytest.approx(98.0)        # min(open=98, limit=99)


def test_match_buy_touched_partial_bounded():
    lf = LimitFill(participation=0.1, queue_fraction=0.0)
    order = RestingOrder("x", 1, 100000, limit_price=99.0, queue_ahead=0.0, bars_alive=0)
    bar = _bar(100, 101, 99.0, 100, v=10000)   # low==limit 触及;vol_at=0.1*10000=1000
    filled, price, c, q = lf.match(order, bar, _NOCOST)
    assert filled == 1000


def test_match_queue_blocks_then_reduces():
    lf = LimitFill(participation=0.1)
    bar = _bar(100, 101, 99.0, 100, v=10000)   # vol_at=1000
    o1 = RestingOrder("x", 1, 5000, limit_price=99.0, queue_ahead=2500, bars_alive=0)
    f1, _, _, q1 = lf.match(o1, bar, _NOCOST)
    assert f1 == 0 and q1 == pytest.approx(1500.0)
    o2 = RestingOrder("x", 1, 5000, limit_price=99.0, queue_ahead=q1, bars_alive=0)
    f2, _, _, q2 = lf.match(o2, bar, _NOCOST)
    assert f2 == 0 and q2 == pytest.approx(500.0)
    o3 = RestingOrder("x", 1, 5000, limit_price=99.0, queue_ahead=q2, bars_alive=0)
    f3, _, _, q3 = lf.match(o3, bar, _NOCOST)
    assert f3 == 500 and q3 == pytest.approx(0.0)   # fillable=max(0,1000-500)=500


def test_match_larger_queue_fraction_fills_no_more():
    bar = _bar(100, 101, 99.0, 100, v=10000)
    o_small = RestingOrder("x", 1, 5000, limit_price=99.0, queue_ahead=0.0, bars_alive=0)
    o_big = RestingOrder("x", 1, 5000, limit_price=99.0, queue_ahead=900.0, bars_alive=0)
    lf = LimitFill(participation=0.1)
    f_small, *_ = lf.match(o_small, bar, _NOCOST)
    f_big, *_ = lf.match(o_big, bar, _NOCOST)
    assert f_big <= f_small                      # 排队越靠后,成交不增(保守单调)


def test_match_sell_mirror_through():
    lf = LimitFill()
    order = RestingOrder("x", -1, -100, limit_price=101.0, queue_ahead=1e9, bars_alive=0)
    filled, price, c, q = lf.match(order, _bar(102, 103, 100, 101.5), _NOCOST)
    assert filled == -100
    assert price == pytest.approx(102.0)         # max(open=102, limit=101)


def test_match_applies_slippage_and_fee():
    lf = LimitFill()
    order = RestingOrder("x", 1, 100, limit_price=99.0, queue_ahead=1e9, bars_alive=0)
    cost = CostModel(fee_bps=1.0, slippage_bps=2.0)
    filled, price, c, q = lf.match(order, _bar(98.0, 100, 97.0, 99.5), cost)
    assert price == pytest.approx(98.0 * (1.0 + 2.0 / 10_000.0))   # 买:滑点加价
    assert c == pytest.approx(abs(filled) * price * (1.0 / 10_000.0))


# ---------------- Task 3: 引擎集成 ----------------

def _aaa(o, h, l, c, v=100000):
    return Bar(instrument_id="AAA", event_time=0, knowledge_time=0,
               open=o, high=h, low=l, close=c, volume=v)


def test_limit_none_path_reconciles():
    timeline = [(t, {"AAA": _aaa(100, 101, 99, 100)}) for t in range(4)]
    targets = [{"AAA": 0.5}, {"AAA": 0.5}, {"AAA": 0.5}, {"AAA": 0.5}]
    res = run_event_backtest(timeline, targets, initial_capital=1_000_000, limit_fill=None)
    assert res.reconciliation_residual() < 1e-6


def test_limit_through_fills_and_reconciles():
    timeline = [
        (0, {"AAA": _aaa(100, 101, 99, 100)}),
        (1, {"AAA": _aaa(100, 100, 98, 99)}),
        (2, {"AAA": _aaa(99, 100, 98, 99)}),
        (3, {"AAA": _aaa(99, 100, 98, 99)}),
    ]
    targets = [{"AAA": 0.5}] * 4
    res = run_event_backtest(timeline, targets, initial_capital=1_000_000,
                             limit_fill=LimitFill())
    assert res.final_positions.get("AAA", 0) > 0      # 成交了多头
    assert res.reconciliation_residual() < 1e-6


def test_unfilled_limit_expires_no_position():
    timeline = [(t, {"AAA": _aaa(100, 101, 99, 100)}) for t in range(6)]
    targets = [{"AAA": 0.5}] * 6
    res = run_event_backtest(timeline, targets, initial_capital=1_000_000,
                             limit_fill=LimitFill(limit_offset_bps=5000.0, expire_bars=2))
    assert res.final_positions == {}
    assert res.reconciliation_residual() < 1e-6


def test_target_reversal_cancels_buy_before_it_fills():
    timeline = [
        (0, {"AAA": _aaa(100, 101, 99.5, 100)}),
        (1, {"AAA": _aaa(101, 102, 100.5, 101)}),   # low=100.5 > 买限价100 → 未触及(合法bar)
        (2, {"AAA": _aaa(100, 100, 97, 100)}),
        (3, {"AAA": _aaa(100, 103, 100, 100)}),
    ]
    targets = [{"AAA": 0.5}, {"AAA": -0.5}, {"AAA": -0.5}, {"AAA": -0.5}]
    res = run_event_backtest(timeline, targets, initial_capital=1_000_000,
                             limit_fill=LimitFill())
    assert res.final_positions.get("AAA", 0) < 0      # 空头,非多头
    assert res.reconciliation_residual() < 1e-6


def test_no_overshoot_under_constant_target():
    timeline = [(t, {"AAA": _aaa(100, 101, 99.0, 100, v=20000)}) for t in range(8)]
    targets = [{"AAA": 0.5}] * 8
    res = run_event_backtest(timeline, targets, initial_capital=1_000_000,
                             limit_fill=LimitFill(participation=0.05, queue_fraction=0.0))
    desired = round(0.5 * 1_000_000 / 100)            # 5000 股上限
    assert 0 <= res.final_positions.get("AAA", 0) <= desired
    assert res.reconciliation_residual() < 1e-6


def test_limit_path_is_deterministic():
    timeline = [
        (0, {"AAA": _aaa(100, 101, 99, 100)}),
        (1, {"AAA": _aaa(100, 100, 98, 99)}),
        (2, {"AAA": _aaa(99, 100, 98, 99)}),
    ]
    targets = [{"AAA": 0.5}] * 3
    kw = dict(initial_capital=1_000_000, limit_fill=LimitFill())
    a = run_event_backtest(timeline, targets, **kw)
    b = run_event_backtest(timeline, targets, **kw)
    assert a.equity_curve == b.equity_curve and a.final_positions == b.final_positions
