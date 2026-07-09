"""TDD coverage for the Lance 1m mean-reversion single-asset rule backtester.

Every scenario here is a hand-built OHLCV bar sequence with hand-derived
expected indicator/trade values (see the docstring math in each test) — not
"runs without crashing" smoke tests. The arithmetic mirrors
docs/test/lance_1m_strategy.md §3/§4/§6/§9 literally:
  - EMA(period) seeded by the first `period` closes' SMA, then recursed
    close*k + prev*(1-k), k = 2/(period+1);
  - SMA(x, period) is a trailing window ENDING AT AND INCLUDING the current
    bar (i.e. bar i's own value participates in its own average) — matches
    the doc's `SMA(x, 20)` notation.
All tests use a short warm-up (`ema_period=vol_lookback=candle_lookback=5`)
instead of the doc's production default of 20 purely so the warm-up window
is small enough to hand-verify; the RULES themselves are exactly as specified.
"""
from __future__ import annotations

import math

import pytest
from quantos.contracts.data import Bar

from services.research.backtest_engine.costs import CostModel
from services.research.backtest_engine.single_asset_rule_backtest import (
    LONG,
    QUICK_TP,
    SHORT,
    STOP_LOSS,
    TARGET_TP,
    TIMEOUT,
    LanceRuleParams,
    run_single_asset_rule_backtest,
)


def _bar(t, o, h, l, c, v):
    return Bar(instrument_id="BTCUSDT", event_time=t, knowledge_time=t,
               open=o, high=h, low=l, close=c, volume=v)


def _warmup(n=5):
    """5 flat bars: close=100 constant -> ema[4]=100, vol_sma[4]=1000,
    candle_sma[4]=0.008 (candle_size=(100.4-99.6)/100=0.008 each bar)."""
    return [_bar(t, 100.0, 100.4, 99.6, 100.0, 1000.0) for t in range(n)]


SMALL = LanceRuleParams(ema_period=5, vol_lookback=5, candle_lookback=5)


def _long_setup():
    """Warm-up + bar 5 that satisfies all 3 entry conditions AND trend_20 < 0
    -> opens LONG at close=97. Hand-derived (see module docstring formulas):
      ema5 = 97*(1/3) + 100*(2/3) = 99.0            -> trend5 = -2.02%  (>0.3%)
      vol_sma5 = mean(1000,1000,1000,1000,2000) = 1200 -> vol_ratio5 = 1.667 (>1.3)
      candle_size5 = (99-95)/97 = 0.041237
      candle_sma5 = mean(0.008*4, 0.041237)/5 = 0.0146474 -> spike5 = 2.815 (>1.3)
    entry_bar_low=95.0 -> long stop price = 95.0*0.998 = 94.81.
    """
    return _warmup() + [_bar(5, 97.0, 99.0, 95.0, 97.0, 2000.0)]


def _short_setup():
    """Mirror of `_long_setup` with trend_20 > 0 -> opens SHORT at close=103.
      ema5 = 103*(1/3) + 100*(2/3) = 101.0           -> trend5 = +1.98%  (>0.3%)
      vol_sma5 = 1200 -> vol_ratio5 = 1.667 (>1.3)
      candle_size5 = (105-101)/103 = 0.038835
      candle_sma5 = 0.014167 -> spike5 = 2.741 (>1.3)
    entry_bar_high=105.0 -> short stop price = 105.0*1.002 = 105.21.
    """
    return _warmup() + [_bar(5, 103.0, 105.0, 101.0, 103.0, 2000.0)]


# ---------------------------------------------------------------------------
# 1) 入场三条件 AND — 分别只满足 2/3
# ---------------------------------------------------------------------------

def test_entry_blocked_when_only_volume_condition_fails():
    # close=97 (price_far True), high/low wide (candle_big True), but
    # volume=1000 == warm-up average -> vol_ratio=1.0 (not > 1.3).
    bars = _warmup() + [_bar(5, 97.0, 99.0, 95.0, 97.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(0, 0, 0))
    assert result.trades == []


def test_entry_blocked_when_only_price_deviation_condition_fails():
    # close=99.9 (tiny move -> |trend_20| well under 0.3%), but volume+candle
    # both spike.
    bars = _warmup() + [_bar(5, 99.9, 100.6, 99.2, 99.9, 2000.0)]
    result = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(0, 0, 0))
    assert result.trades == []


def test_entry_blocked_when_only_candle_spike_condition_fails():
    # close=97 (price_far True) and volume=2000 (vol_ok True), but a narrow
    # candle (high-low=0.97 -> candle_size=0.01) keeps spike under 1.3x.
    bars = _warmup() + [_bar(5, 97.0, 97.485, 96.515, 97.0, 2000.0)]
    result = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(0, 0, 0))
    assert result.trades == []


def test_entry_requires_all_three_together():
    # Positive control: the exact same style of bar as the 3 "one condition
    # fails" tests above, but with all 3 conditions restored -> entry DOES
    # fire. Proves the AND is load-bearing, not just "no entry ever happens
    # with this warm-up". No exit condition is reached within these 6 bars,
    # so there's no closed Trade yet to inspect directly — instead we check
    # that the LAST bar's mark-to-market equity already reflects a paid
    # entry_cost (0.4 * 100,000 * 1bps = 4.0) with zero unrealized P&L
    # (entry price == that same bar's close), which is only possible if a
    # position was actually opened.
    bars = _long_setup()
    result = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(1, 0, 0))
    assert result.trades == []
    assert result.equity_curve[-1] == pytest.approx(100_000.0 - 4.0)


# ---------------------------------------------------------------------------
# 2) 方向判断 (trend_20<0 -> 多; trend_20>0 -> 空), 顺带用 target_tp 出场拿到
#    Trade 记录以校验入场字段。
# ---------------------------------------------------------------------------

def test_direction_long_when_trend_negative():
    bars = _long_setup() + [_bar(6, 99.0, 99.5, 98.5, 99.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(1, 0, 0))
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.direction == LONG
    assert t.entry_index == 5
    assert t.entry_price == pytest.approx(97.0)
    assert t.entry_bar_low == pytest.approx(95.0)
    assert t.entry_bar_high == pytest.approx(99.0)


def test_direction_short_when_trend_positive():
    bars = _short_setup() + [_bar(6, 101.0, 101.5, 100.5, 101.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(1, 0, 0))
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.direction == SHORT
    assert t.entry_index == 5
    assert t.entry_price == pytest.approx(103.0)
    assert t.entry_bar_low == pytest.approx(101.0)
    assert t.entry_bar_high == pytest.approx(105.0)


# ---------------------------------------------------------------------------
# 3) 四种出场条件, 各自独立触发 + 多个条件同时满足时的优先级顺序
# ---------------------------------------------------------------------------

def test_exit_target_tp_wins_over_quick_tp_when_both_true():
    # bar6 close=99.0 == ema5 -> ema6 recomputes back to ~99.0 -> trend6~0
    # (target_tp True). unrealized_pnl_pct = (99-97)/97 = 2.06% > 0.15% too
    # (quick_tp ALSO true) -> §9 elif order must pick target_tp (checked
    # first), not quick_tp.
    bars = _long_setup() + [_bar(6, 99.0, 99.5, 98.5, 99.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(0, 0, 0))
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == TARGET_TP
    assert t.exit_price == pytest.approx(99.0)
    assert t.exit_index == 6
    assert t.bars_held == 1


def test_exit_quick_tp_only_when_target_tp_not_reached():
    # bar6 close=98.0: trend6 = (98-98.6667)/98.6667 = -0.676% (still > 0.3%,
    # target_tp False); unrealized_pnl_pct = (98-97)/97 = 1.03% > 0.15%
    # (quick_tp True).
    bars = _long_setup() + [_bar(6, 98.0, 98.5, 97.5, 98.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(0, 0, 0))
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == QUICK_TP
    assert t.exit_price == pytest.approx(98.0)


def test_exit_quick_tp_wins_over_timeout_when_both_true():
    # max_bars_held overridden to 1 (bars_held reaches 1 at bar6 -> timeout
    # condition true). bar6 close=97.5: trend6 = -1.02% (target_tp False),
    # unrealized_pnl_pct = 0.52% > 0.15% (quick_tp True) -> §9 elif order
    # must pick quick_tp (checked before timeout), not timeout.
    params = LanceRuleParams(ema_period=5, vol_lookback=5, candle_lookback=5, max_bars_held=1)
    bars = _long_setup() + [_bar(6, 97.5, 98.0, 97.0, 97.5, 1000.0)]
    result = run_single_asset_rule_backtest(bars, params, cost=CostModel(0, 0, 0))
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == QUICK_TP


def test_exit_timeout():
    # max_bars_held=2. bar6 (bars_held=1) and bar7 (bars_held=2) both hold
    # close=97 flat (no reversion -> target_tp False throughout; no profit
    # -> quick_tp False throughout; lows stay well above the 94.81 stop).
    # Timeout fires exactly at bars_held==2 (bar7), not before.
    params = LanceRuleParams(ema_period=5, vol_lookback=5, candle_lookback=5, max_bars_held=2)
    bars = _long_setup() + [
        _bar(6, 97.0, 97.5, 96.5, 97.0, 1000.0),
        _bar(7, 97.0, 97.5, 96.5, 97.0, 1000.0),
    ]
    result = run_single_asset_rule_backtest(bars, params, cost=CostModel(0, 0, 0))
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == TIMEOUT
    assert t.exit_index == 7
    assert t.bars_held == 2
    assert t.exit_price == pytest.approx(97.0)


def test_exit_stop_loss_clean_fills_at_stop_price_not_close():
    # bar6 close=95 (a straight loss, no reversion -> target_tp False, no
    # profit -> quick_tp False, bars_held=1 < 20 -> timeout False); low=94.5
    # <= stop price 94.81 -> stop_loss fires. Exit must settle at the STOP
    # PRICE (94.81), not the bar's close (95) or its low (94.5) — the engine
    # assumes the stop order filled exactly at the stop level.
    bars = _long_setup() + [_bar(6, 95.0, 95.5, 94.5, 95.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(0, 0, 0))
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == STOP_LOSS
    assert t.exit_price == pytest.approx(94.81)
    assert t.exit_price != pytest.approx(t.entry_bar_low)   # not the raw low either


def test_exit_timeout_is_overridden_by_a_simultaneous_stop_touch_by_default():
    """Boundary test for the OHLC-ambiguity override: TIMEOUT settles at
    `bar.close` (§9), which is exactly the same kind of "only knowable at
    this bar's close" conclusion as target_tp/quick_tp — a resting stop-loss
    order fires the instant intrabar price touches the stop level, no later
    than the bar's close tick, so the same worse-path ambiguity applies to
    TIMEOUT too. Same bar as `test_exit_stop_loss_clean...` (close=95, low
    touches the 94.81 stop), but with max_bars_held=1 so timeout ALSO fires
    on this bar: the baseline §9 elif order picks timeout (checked before
    stop_loss), but the default conservative override (`params.
    conservative_ohlc_ambiguity=True`) forces STOP_LOSS at the stop price
    instead, matching the treatment already given to target_tp/quick_tp."""
    params = LanceRuleParams(ema_period=5, vol_lookback=5, candle_lookback=5, max_bars_held=1)
    bars = _long_setup() + [_bar(6, 95.0, 95.5, 94.5, 95.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, params, cost=CostModel(0, 0, 0))
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == STOP_LOSS
    assert t.exit_price == pytest.approx(94.81)
    assert t.exit_reason != TIMEOUT


def test_exit_timeout_keeps_doc_literal_order_when_conservative_override_disabled():
    """`conservative_ohlc_ambiguity=False` opts back into the literal §9
    if/elif pseudocode order with no post-hoc reclassification — same bar as
    the test above, but with the override switched off: baseline order picks
    timeout (checked before stop_loss) and nothing overrides it."""
    params = LanceRuleParams(ema_period=5, vol_lookback=5, candle_lookback=5, max_bars_held=1,
                              conservative_ohlc_ambiguity=False)
    bars = _long_setup() + [_bar(6, 95.0, 95.5, 94.5, 95.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, params, cost=CostModel(0, 0, 0))
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == TIMEOUT
    assert t.exit_price == pytest.approx(95.0)


# ---------------------------------------------------------------------------
# 4) OHLC 同根K线出场歧义 (target_tp/quick_tp/timeout 均适用) —— 保守裁决
#    专项测试, 以及 `conservative_ohlc_ambiguity=False` 显式关闭该裁决、
#    严格复现文档 §9 字面 if/elif 顺序的对照测试。
# ---------------------------------------------------------------------------

def test_ohlc_ambiguity_long_prefers_stop_loss_over_target_tp():
    # bar6: close=99.0 -> trend6~0 -> target_tp condition WOULD fire (this is
    # exactly the same close as test_exit_target_tp_wins_over_quick_tp_when_
    # both_true), BUT this bar's low=94.5 also breaches the long stop
    # (94.81) -> we cannot tell whether price plunged through the stop
    # BEFORE rallying back to close at 99, or the reverse. Conservative
    # rule: assume the worse path (stop hit first) -> settle at stop_loss
    # using the STOP PRICE (94.81), not target_tp at the close (99.0).
    bars = _long_setup() + [_bar(6, 97.0, 99.5, 94.5, 99.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(0, 0, 0))
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == STOP_LOSS
    assert t.exit_price == pytest.approx(94.81)
    assert t.exit_reason != TARGET_TP


def test_ohlc_ambiguity_short_prefers_stop_loss_over_quick_tp():
    # Symmetric short-side variant, and against quick_tp instead of
    # target_tp (both are "close-derived profit conclusions" per the
    # override's scope). bar6: close=99 -> unrealized_pnl_pct=(103-99)/103
    # =3.88% > 0.15% (quick_tp WOULD fire; trend6=-1.33% keeps target_tp
    # False so quick_tp is cleanly the baseline winner) but high=106 >=
    # the short stop (105.21) -> conservative override forces stop_loss at
    # the stop price, not quick_tp at the close.
    bars = _short_setup() + [_bar(6, 103.0, 106.0, 98.5, 99.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(0, 0, 0))
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == STOP_LOSS
    assert t.exit_price == pytest.approx(105.21)
    assert t.exit_reason != QUICK_TP
    assert t.net_pnl < 0   # a stopped-out short must be a loss, not the "profit" quick_tp implied


def test_ohlc_ambiguity_override_can_be_disabled_for_literal_doc_order():
    """`conservative_ohlc_ambiguity=False` opts out of the post-hoc
    reclassification entirely and reproduces the doc §9 pseudocode's literal
    if/elif order verbatim (target_tp checked and picked before stop_loss,
    with no regard for a same-bar stop touch). Same bar as
    `test_ohlc_ambiguity_long_prefers_stop_loss_over_target_tp`, opposite
    param -> opposite exit."""
    params = LanceRuleParams(ema_period=5, vol_lookback=5, candle_lookback=5,
                              conservative_ohlc_ambiguity=False)
    bars = _long_setup() + [_bar(6, 97.0, 99.5, 94.5, 99.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, params, cost=CostModel(0, 0, 0))
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == TARGET_TP
    assert t.exit_price == pytest.approx(99.0)
    assert t.exit_reason != STOP_LOSS


# ---------------------------------------------------------------------------
# 5) 仓位比例 / 名义额计算
# ---------------------------------------------------------------------------

def test_position_sizing_uses_configured_position_pct():
    params = LanceRuleParams(ema_period=5, vol_lookback=5, candle_lookback=5, position_pct=0.25)
    bars = _long_setup() + [_bar(6, 99.0, 99.5, 98.5, 99.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, params, initial_capital=100_000.0,
                                            cost=CostModel(1, 0, 0))
    t = result.trades[0]
    assert t.notional == pytest.approx(25_000.0)          # 0.25 * 100,000
    assert t.qty == pytest.approx(25_000.0 / 97.0)
    assert t.entry_cost == pytest.approx(2.5)              # 25,000 * 1bps


# ---------------------------------------------------------------------------
# 6) 手续费/滑点参数化 —— 净收益按费率正确扣减
# ---------------------------------------------------------------------------

def test_fee_bps_is_parameterized_and_net_pnl_matches_formula():
    # Same trade (entry 97 -> target_tp exit @ 99, qty=40000/97) run under
    # each of the doc §7 breakpoints (1 / 5(~2-5bps) / 8 / 10bps, zero
    # slippage). net_pnl must exactly equal gross_pnl - entry_cost -
    # exit_cost for each, and must be strictly decreasing as the rate rises.
    bars = _long_setup() + [_bar(6, 99.0, 99.5, 98.5, 99.0, 1000.0)]
    qty = 40_000.0 / 97.0
    gross_pnl = qty * (99.0 - 97.0)
    exit_notional = qty * 99.0
    expected_by_fee = {}
    for fee_bps in (1.0, 5.0, 8.0, 10.0):
        entry_cost = 40_000.0 * fee_bps / 10_000.0
        exit_cost = exit_notional * fee_bps / 10_000.0
        expected_by_fee[fee_bps] = gross_pnl - entry_cost - exit_cost

    nets = []
    for fee_bps in (1.0, 5.0, 8.0, 10.0):
        result = run_single_asset_rule_backtest(bars, SMALL, initial_capital=100_000.0,
                                                cost=CostModel(fee_bps=fee_bps, slippage_bps=0.0))
        net = result.trades[0].net_pnl
        assert net == pytest.approx(expected_by_fee[fee_bps])
        nets.append(net)

    assert nets == sorted(nets, reverse=True)   # strictly worse as fees rise
    assert all(a > b for a, b in zip(nets, nets[1:]))


def test_zero_cost_run_has_no_cost_deducted():
    bars = _long_setup() + [_bar(6, 99.0, 99.5, 98.5, 99.0, 1000.0)]
    result = run_single_asset_rule_backtest(bars, SMALL, initial_capital=100_000.0,
                                            cost=CostModel(0.0, 0.0, 0.0))
    t = result.trades[0]
    assert t.entry_cost == 0.0
    assert t.exit_cost == 0.0
    assert t.net_pnl == pytest.approx(t.gross_pnl)


# ---------------------------------------------------------------------------
# 7) 端到端手算验证 (20 根 bar, 1 次完整交易, 逐字段核对)
# ---------------------------------------------------------------------------

def test_end_to_end_hand_calculated_sequence():
    """20 hand-built bars: 5-bar warm-up, an entry bar (index 5), an exit
    bar (index 6, target_tp), then 13 neutral bars (7..19) that must NOT
    produce any further signal. Every number below was independently
    derived from the documented formulas (§9), not by calling this module —
    see the derivation in services/research/backtest_engine/
    single_asset_rule_backtest.py's docstrings for the same formulas spelled
    out; the arithmetic here was cross-checked with a standalone script
    implementing those formulas from scratch.

    Entry (bar5): close=97, ema5=99.0 (=97/3+100*2/3) -> trend5=-2.02%,
    vol_ratio5=1.667, candle_spike5=2.815 -> all 3 AND conditions pass,
    trend5<0 -> LONG @ 97.0. notional=0.4*100,000=40,000,
    qty=40,000/97=412.37113402061857, entry_cost (1bps)=4.0.
    equity after entry = 100,000 - 4.0 = 99,996.0.

    Exit (bar6): close=99.0 == ema5 -> ema6 recomputes back to ~99.0 (float
    noise ~1e-16) -> trend6~0 -> target_tp. gross_pnl = qty*(99-97) =
    824.7422680412371. exit_notional = qty*99 = 40,824.74226804124,
    exit_cost (1bps) = 4.082474226804124.
    net_pnl = 824.7422680412371 - 4.0 - 4.082474226804124 = 816.659793814433.
    equity after exit = 99,996.0 + 824.7422680412371 - 4.082474226804124
                       = 100,816.65979381443... (== initial + net_pnl).

    Bars 7..19: flat at close=99.66666666666667 (an arbitrary post-trade
    level near the last EMA) with unremarkable volume/candle size -> no new
    entry signal -> equity stays exactly flat at the post-exit value through
    the rest of the sequence.
    """
    entry_cost = 40_000.0 * 1.0 / 10_000.0
    qty = 40_000.0 / 97.0
    gross_pnl = qty * (99.0 - 97.0)
    exit_notional = qty * 99.0
    exit_cost = exit_notional * 1.0 / 10_000.0
    expected_net_pnl = gross_pnl - entry_cost - exit_cost
    expected_final_equity = 100_000.0 - entry_cost + gross_pnl - exit_cost

    bars = _warmup() + [
        _bar(5, 97.0, 99.0, 95.0, 97.0, 2000.0),     # entry: LONG @ 97
        _bar(6, 99.0, 99.5, 98.5, 99.0, 1000.0),     # exit: target_tp @ 99
    ]
    # 13 neutral post-trade bars, indices 7..19 (no signal: flat close,
    # ordinary volume/candle range).
    flat_close = 99.66666666666667
    for t in range(7, 20):
        bars.append(_bar(t, flat_close, flat_close + 0.1, flat_close - 0.1, flat_close, 1000.0))

    assert len(bars) == 20

    result = run_single_asset_rule_backtest(
        bars, LanceRuleParams(ema_period=5, vol_lookback=5, candle_lookback=5),
        initial_capital=100_000.0, cost=CostModel(fee_bps=1.0, slippage_bps=0.0),
    )

    # --- exactly one complete trade, every field checked ---
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.direction == LONG
    assert t.entry_index == 5
    assert t.entry_timestamp == 5
    assert t.entry_price == pytest.approx(97.0)
    assert t.entry_bar_low == pytest.approx(95.0)
    assert t.entry_bar_high == pytest.approx(99.0)
    assert t.exit_index == 6
    assert t.exit_timestamp == 6
    assert t.exit_price == pytest.approx(99.0)
    assert t.exit_reason == TARGET_TP
    assert t.bars_held == 1
    assert t.qty == pytest.approx(qty)
    assert t.notional == pytest.approx(40_000.0)
    assert t.gross_pnl == pytest.approx(gross_pnl)
    assert t.entry_cost == pytest.approx(entry_cost)
    assert t.exit_cost == pytest.approx(exit_cost)
    assert t.net_pnl == pytest.approx(expected_net_pnl)

    # --- bar-by-bar equity curve ---
    assert len(result.equity_curve) == 20
    for i in range(5):   # warm-up bars: flat, no position, no cost
        assert result.equity_curve[i] == pytest.approx(100_000.0)
    assert result.equity_curve[5] == pytest.approx(100_000.0 - entry_cost)   # entry bar, 0 unrealized
    assert result.equity_curve[6] == pytest.approx(expected_final_equity)
    for i in range(7, 20):   # flat afterward, no further trade
        assert result.equity_curve[i] == pytest.approx(expected_final_equity)

    # --- summary stats (文档 §10 同名指标口径) ---
    summary = result.summary()
    assert summary["num_trades"] == 1
    assert summary["win_rate"] == pytest.approx(1.0)
    assert summary["total_net_pnl"] == pytest.approx(expected_net_pnl)
    assert summary["total_cost"] == pytest.approx(entry_cost + exit_cost)
    assert summary["total_return"] == pytest.approx(expected_final_equity / 100_000.0 - 1.0)
    assert summary["profit_factor"] == math.inf   # no losing trades
    assert summary["win_loss_ratio"] == math.inf
    assert summary["avg_bars_held"] == pytest.approx(1.0)
    # equity dips from 100,000 -> 99,996.0 at the entry bar (paid entry_cost,
    # zero unrealized P&L yet) before recovering on the exit bar and staying
    # flat -> a single small drawdown of entry_cost / initial_capital.
    assert summary["max_drawdown"] == pytest.approx(entry_cost / 100_000.0)


# ---------------------------------------------------------------------------
# 8) 通用卫生测试: 纯函数确定性 + 空数据/极短序列不崩溃
# ---------------------------------------------------------------------------

def test_is_deterministic():
    bars = _long_setup() + [_bar(6, 99.0, 99.5, 98.5, 99.0, 1000.0)]
    a = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(1, 2, 0))
    b = run_single_asset_rule_backtest(bars, SMALL, cost=CostModel(1, 2, 0))
    assert a.equity_curve == b.equity_curve
    assert [vars(t) for t in a.trades] == [vars(t) for t in b.trades]


def test_empty_and_short_bar_sequences_do_not_crash():
    assert run_single_asset_rule_backtest([], SMALL).trades == []
    short = _warmup(3)   # fewer bars than any lookback period (5) -> all indicators None
    result = run_single_asset_rule_backtest(short, SMALL)
    assert result.trades == []
    assert len(result.equity_curve) == 3


def test_initial_capital_must_be_positive():
    with pytest.raises(ValueError):
        run_single_asset_rule_backtest(_warmup(), SMALL, initial_capital=0.0)
