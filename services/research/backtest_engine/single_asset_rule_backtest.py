"""Single-asset, stateful, bar-close rule backtester (docs/test/lance_1m_strategy.md).

Why this is a NEW, separate module rather than a change to `event_engine.py`:
`run_event_backtest` is a target-WEIGHT, multi-instrument engine whose entire
design rests on two premises that the Lance strategy directly violates:
  1. decisions made from bar i are filled at bar i+1's OPEN (`NextOpenFill`,
     "no same-bar fill" is that engine's own anti-lookahead invariant), while
     the Lance spec explicitly wants "条件满足后立即入场, 无需等下一根K线确认"
     (decide-and-fill at bar i's own CLOSE);
  2. it is stateless across bars (no notion of "the entry bar's low/high" or
     "bars held since entry") — this strategy's exits (§6.3/§6.4) are defined
     entirely in terms of that per-trade state.
Bolting either of those onto the shared engine would corrupt behaviour every
other strategy/test relies on, so this module implements a small, independent,
single-asset event loop that lives beside `event_engine.py`, not inside it.

Pure-function core: `run_single_asset_rule_backtest(bars, params, ...)` consumes
an already-assembled, time-ordered bar sequence and returns trades + a bar-by-bar
mark-to-market equity curve + summary stats. No IO / network / wall-clock / RNG —
sourcing real bars (and any wall-clock/random concerns) is entirely the caller's
job, matching this repo's backtest-core convention (see module docstring of
`backtest_engine/__init__.py`).

Indicators (data prep, §9): EMA20 seeded by the first `ema_period` closes' SMA
then recursed (`close*k + prev*(1-k)`, k = 2/(period+1) — the standard
"EMA-with-SMA-seed" convention); SMA(volume, N) / SMA(candle_size, N) as
trailing windows INCLUDING the current bar (matches the doc's `SMA(x, 20)`
notation literally). All three are computed strictly causally from bars
[0..i] — none of them peek past the bar they're evaluated on, so evaluating
them "as of this bar's close" is not lookahead (see task framing).

Periods are exposed as parameters (doc §8 lists them "固定"/fixed as a
PRODUCTION recommendation, i.e. 20) purely so tests can hand-verify short,
tractable warm-up windows; the formulas themselves are untouched.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

from quantos.contracts.data import Bar

from .costs import CostModel
from .metrics import max_drawdown as _max_drawdown
from .metrics import returns_from_equity as _returns_from_equity
from .metrics import sharpe as _sharpe

LONG = "long"
SHORT = "short"

TARGET_TP = "target_tp"    # §6.1 目标止盈: 价格回到 EMA20 附近 (|trend_20| < 阈值)
QUICK_TP = "quick_tp"      # §6.2 快盈止盈: 浮盈 > 快盈阈值
TIMEOUT = "timeout"        # §6.3 超时止损: 持仓K线数 >= 上限
STOP_LOSS = "stop_loss"    # §6.4 价格止损: 跌破/涨破入场K线极值 * 止损系数


@dataclass(frozen=True)
class LanceRuleParams:
    """文档 §8 关键参数汇总。"""
    ema_period: int = 20
    vol_lookback: int = 20
    candle_lookback: int = 20
    vol_ratio_threshold: float = 1.3
    # 同一个 0.3% 在文档里身兼两职: §3.2 入场"偏离阈值"(|trend_20| > 阈值) 与
    # §6.1 出场"回归带宽"(|trend_20| < 阈值) —— 文档 §8 参数表只列了一行
    # "价格偏离阈值", 没有为出场单独开一行, 因此这里就该是同一个参数, 不是
    # 巧合撞了同一个数字才拆两个字段。
    price_dev_threshold: float = 0.003
    candle_spike_threshold: float = 1.3
    position_pct: float = 0.4
    max_bars_held: int = 20
    quick_profit_pct: float = 0.0015
    stop_loss_pct: float = 0.002
    # 本模块自己的显式工程决策 (不是文档 §9 伪代码字面要求的一部分, 也不是从
    # 任何外部任务书转录来的 —— 那个说法在本仓库内无法核实, 已移除): 一根
    # bar 只有 open/high/low/close 四个数, 不知道 high/low 真实出现的先后
    # 顺序, 所以任何"由本根K线收盘价才能算出"的出场结论 (target_tp/
    # quick_tp/timeout 三者的结算价都是 bar.close) 都存在同一种歧义 —— 如果
    # 本根K线同时也摸到了止损线, 无法排除"先触发止损、后才走到收盘价这个
    # 结论"这条更差路径先发生; 而挂着的止损单只要盘中价格摸到止损线就会
    # 成交, 这在时间上不会晚于本根K线的收盘 tick。默认 True = 遇到这种歧义
    # 时假设更差路径发生, 强制按止损价结算 (经济保守, 不会把亏损做小);
    # 设为 False 则严格复现文档 §9 的字面 if/elif 优先级顺序, 不做任何事后
    # 改判 —— 用于需要逐字对照文档 §10 历史基准数字的场景。
    conservative_ohlc_ambiguity: bool = True

    def __post_init__(self) -> None:
        if self.ema_period < 1 or self.vol_lookback < 1 or self.candle_lookback < 1:
            raise ValueError("lookback periods must be >= 1")
        if not (0.0 < self.position_pct <= 1.0):
            raise ValueError("position_pct must be in (0, 1]")
        if self.max_bars_held < 1:
            raise ValueError("max_bars_held must be >= 1")


@dataclass(frozen=True)
class Trade:
    direction: str
    entry_index: int
    entry_timestamp: int
    entry_price: float
    entry_bar_low: float
    entry_bar_high: float
    exit_index: int
    exit_timestamp: int
    exit_price: float
    exit_reason: str
    bars_held: int
    qty: float
    notional: float
    gross_pnl: float
    entry_cost: float
    exit_cost: float
    net_pnl: float

    @property
    def total_cost(self) -> float:
        return self.entry_cost + self.exit_cost


@dataclass(frozen=True)
class SingleAssetBacktestResult:
    initial_capital: float
    timestamps: list
    equity_curve: list
    trades: list

    def final_equity(self) -> float:
        return self.equity_curve[-1] if self.equity_curve else self.initial_capital

    def summary(self, *, periods_per_year: int = 525_600) -> dict:
        """总收益/夏普/最大回撤/胜率/盈亏比/交易数 (文档 §10 "已知表现" 同名指标口径).

        `periods_per_year` defaults to a 24/7 1-minute bar calendar
        (60*24*365) matching the doc's BTCUSDT-1m context; pass the actual
        value for whatever bar cadence you feed in.
        """
        rets = _returns_from_equity(self.equity_curve)
        final_equity = self.final_equity()
        total_return = (final_equity / self.initial_capital - 1.0) if self.initial_capital else 0.0

        wins = [t.net_pnl for t in self.trades if t.net_pnl > 0]
        losses = [-t.net_pnl for t in self.trades if t.net_pnl < 0]   # positive magnitudes
        num_trades = len(self.trades)
        win_rate = (len(wins) / num_trades) if num_trades else 0.0
        avg_win = statistics.fmean(wins) if wins else 0.0
        avg_loss = statistics.fmean(losses) if losses else 0.0
        # "盈亏比" (win/loss ratio) = 平均盈利 / 平均亏损, 标准零售交易口径
        # (与"盈利因子" profit_factor = 总盈利/总亏损 是两个不同但都常见的指标,
        # 两个都算出来, 避免只猜中一个)。
        if avg_loss > 0:
            win_loss_ratio = avg_win / avg_loss
        else:
            win_loss_ratio = math.inf if avg_win > 0 else 0.0
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            profit_factor = math.inf if gross_profit > 0 else 0.0

        return {
            "num_trades": num_trades,
            "win_rate": win_rate,
            "win_loss_ratio": win_loss_ratio,
            "profit_factor": profit_factor,
            "total_return": total_return,
            "total_net_pnl": sum(t.net_pnl for t in self.trades),
            "total_cost": sum(t.total_cost for t in self.trades),
            "sharpe": _sharpe(rets, periods_per_year),
            "max_drawdown": _max_drawdown(self.equity_curve),
            "avg_bars_held": statistics.fmean([t.bars_held for t in self.trades]) if self.trades else 0.0,
        }


@dataclass
class _OpenPosition:
    direction: str
    entry_index: int
    entry_timestamp: int
    entry_price: float
    entry_bar_low: float
    entry_bar_high: float
    qty: float
    notional: float
    entry_cost: float


def _sma(values: Sequence[float], period: int) -> list:
    """Trailing SMA over a window of `period` bars ENDING AT AND INCLUDING the
    current bar (matches the doc's `SMA(x, 20)` notation: bar i's own value is
    part of its own average). None while fewer than `period` observations
    exist yet (causal warm-up, never peeks forward)."""
    out: list = [None] * len(values)
    window_sum = 0.0
    for i, v in enumerate(values):
        window_sum += v
        if i >= period:
            window_sum -= values[i - period]
        if i >= period - 1:
            out[i] = window_sum / period
    return out


def _ema(values: Sequence[float], period: int) -> list:
    """EMA(period), seeded by the SMA of the first `period` values then
    recursed with k = 2/(period+1) (the standard "EMA with SMA seed"
    convention — deterministic, causal, and small enough to hand-verify)."""
    out: list = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def _unrealized_pnl(position: _OpenPosition, price: float) -> float:
    if position.direction == LONG:
        return position.qty * (price - position.entry_price)
    return position.qty * (position.entry_price - price)


def _evaluate_exit(position: _OpenPosition, bar: Bar, trend: Optional[float],
                    bar_index: int, params: LanceRuleParams):
    """§6 + §9 出场判定, 严格按伪代码 if/elif 顺序 (target_tp -> quick_tp ->
    timeout -> stop_loss), 默认外加一条本模块自己的显式保守裁决 (见
    `LanceRuleParams.conservative_ohlc_ambiguity` 字段注释), 可关闭以严格
    复现文档 §9 的字面顺序、不做任何事后改判。

    Returns (exit_reason, exit_price) or (None, None) if nothing fires.
    """
    is_long = position.direction == LONG

    # 止损触发只问"本根K线的 low/high 是否摸到过止损线", 不依赖 high/low
    # 出现的真实先后顺序 —— 这一步本身是良定义的(布尔判定), 歧义出现在
    # "止损触发" 与 "基于收盘价的止盈类判定" 谁先发生, 见下方保守裁决。
    if is_long:
        stop_price = position.entry_bar_low * (1.0 - params.stop_loss_pct)
        stop_touched = bar.low <= stop_price
    else:
        stop_price = position.entry_bar_high * (1.0 + params.stop_loss_pct)
        stop_touched = bar.high >= stop_price

    if is_long:
        unrealized_pnl_pct = (bar.close - position.entry_price) / position.entry_price
    else:
        unrealized_pnl_pct = (position.entry_price - bar.close) / position.entry_price

    target_tp = trend is not None and abs(trend) < params.price_dev_threshold
    quick_tp = unrealized_pnl_pct > params.quick_profit_pct
    timed_out = (bar_index - position.entry_index) >= params.max_bars_held

    # --- 文档 §9 伪代码字面 if/elif 顺序 (基线, 不可重排) ---
    if target_tp:
        reason, price = TARGET_TP, bar.close
    elif quick_tp:
        reason, price = QUICK_TP, bar.close
    elif timed_out:
        reason, price = TIMEOUT, bar.close
    elif stop_touched:
        reason, price = STOP_LOSS, stop_price
    else:
        return None, None

    # --- OHLC 同根K线出场歧义的显式保守裁决 (覆盖上面基线结果, 默认开启) ---
    # 一根 bar 只有 open/high/low/close 四个数, 不知道 high/low 真实出现的
    # 先后顺序。target_tp/quick_tp/timeout 三者的结算价都是 bar.close (§9
    # 字面如此) —— 这三个都是"必须等到本根K线收盘才能算出"的事后结论。
    # 如果本根K线的 low/high 也摸到了止损线, 就不能排除"先跌破止损、后
    # 再反弹收出这个收盘价结论"这条更差路径先发生: 挂着的止损单只要盘中
    # 价格摸到止损线就会成交, 这在时间上不会晚于本根K线的收盘 tick, 所以
    # 这条歧义对 target_tp/quick_tp/timeout 是同一种性质, 不因"结论听起来
    # 是不是盈利"而有区别 (timeout 本身无盈亏方向, 但它的结算价一样是
    # close, 一样可能被同根K线里更早触发的止损抢先)。因此: 只要止损线
    # 本根K线被触及, 且基线选中的不是 stop_loss 本身, 就强制改判为止损、
    # 按止损价(而不是收盘价)结算 —— 经济保守, 绝不会把亏损做小。
    #
    # 这是本模块自己的工程决策, 默认通过 `conservative_ohlc_ambiguity=True`
    # 启用; 需要逐字对照文档 §9 伪代码 (无事后改判) 时可显式传
    # `conservative_ohlc_ambiguity=False` 关闭 (下面有专项测试覆盖两种模式)。
    if params.conservative_ohlc_ambiguity and reason != STOP_LOSS and stop_touched:
        reason, price = STOP_LOSS, stop_price

    return reason, price


def _open_position(direction: str, i: int, bar: Bar, equity: float,
                    params: LanceRuleParams, cost: CostModel) -> _OpenPosition:
    entry_price = bar.close
    notional = params.position_pct * equity
    qty = notional / entry_price
    entry_cost = cost.trade_cost(notional_abs=notional)
    return _OpenPosition(
        direction=direction, entry_index=i, entry_timestamp=bar.event_time,
        entry_price=entry_price, entry_bar_low=bar.low, entry_bar_high=bar.high,
        qty=qty, notional=notional, entry_cost=entry_cost,
    )


def _close_trade(position: _OpenPosition, i: int, bar: Bar, exit_price: float,
                  reason: str, cost: CostModel, equity: float):
    is_long = position.direction == LONG
    qty = position.qty
    if is_long:
        gross_pnl = qty * (exit_price - position.entry_price)
    else:
        gross_pnl = qty * (position.entry_price - exit_price)
    exit_notional = qty * exit_price
    exit_cost = cost.trade_cost(notional_abs=exit_notional)
    net_pnl = gross_pnl - position.entry_cost - exit_cost
    # `equity` walking into this call already had entry_cost deducted (at
    # open time, see the main loop) -> don't subtract it again here.
    new_equity = equity + gross_pnl - exit_cost
    trade = Trade(
        direction=position.direction, entry_index=position.entry_index,
        entry_timestamp=position.entry_timestamp, entry_price=position.entry_price,
        entry_bar_low=position.entry_bar_low, entry_bar_high=position.entry_bar_high,
        exit_index=i, exit_timestamp=bar.event_time, exit_price=exit_price,
        exit_reason=reason, bars_held=i - position.entry_index,
        qty=qty, notional=position.notional, gross_pnl=gross_pnl,
        entry_cost=position.entry_cost, exit_cost=exit_cost, net_pnl=net_pnl,
    )
    return trade, new_equity


def run_single_asset_rule_backtest(
    bars: Sequence[Bar],
    params: LanceRuleParams = LanceRuleParams(),
    *,
    initial_capital: float = 100_000.0,
    cost: CostModel = CostModel(),
) -> SingleAssetBacktestResult:
    """Run the Lance mean-reversion rule set over `bars` (single instrument,
    time-ordered, no gaps assumed). Pure function: same inputs -> bit-identical
    outputs, no IO/network/wall-clock/RNG."""
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    volumes = [b.volume for b in bars]
    candle_size = [(h - l) / c for h, l, c in zip(highs, lows, closes)]

    ema = _ema(closes, params.ema_period)
    vol_sma = _sma(volumes, params.vol_lookback)
    candle_sma = _sma(candle_size, params.candle_lookback)
    trend_20 = [
        (closes[i] - ema[i]) / ema[i] if ema[i] is not None else None
        for i in range(len(bars))
    ]

    equity = float(initial_capital)
    position: Optional[_OpenPosition] = None
    trades: list = []
    equity_curve: list = []
    timestamps: list = []

    for i, bar in enumerate(bars):
        # === 出场检查 (如果持仓), §6/§9 ===
        if position is not None:
            reason, exit_price = _evaluate_exit(position, bar, trend_20[i], i, params)
            if reason is not None:
                trade, equity = _close_trade(position, i, bar, exit_price, reason, cost, equity)
                trades.append(trade)
                position = None

        # === 入场检查 (如果空仓), §3/§4/§5/§9 ===
        # 注意: 文档伪代码里出场块和入场块是两个独立的顶层 if (不是 elif),
        # 顺序执行 —— 若本根K线在上面被平仓, 这里 position 已是 None, 允许
        # 同一根K线立即重新入场(含反手), 与伪代码字面结构一致, 不是 bug。
        if position is None:
            v_sma_i = vol_sma[i]
            c_sma_i = candle_sma[i]
            t_i = trend_20[i]
            if v_sma_i is not None and c_sma_i is not None and t_i is not None:
                vol_ratio = (volumes[i] / v_sma_i) if v_sma_i > 0 else None
                candle_spike = (candle_size[i] / c_sma_i) if c_sma_i > 0 else None
                vol_ok = vol_ratio is not None and vol_ratio > params.vol_ratio_threshold
                price_far = abs(t_i) > params.price_dev_threshold
                candle_big = candle_spike is not None and candle_spike > params.candle_spike_threshold
                if vol_ok and price_far and candle_big:
                    direction = None
                    if t_i < 0:
                        direction = LONG
                    elif t_i > 0:
                        direction = SHORT
                    if direction is not None:
                        position = _open_position(direction, i, bar, equity, params, cost)
                        equity -= position.entry_cost

        timestamps.append(bar.event_time)
        if position is None:
            equity_curve.append(equity)
        else:
            equity_curve.append(equity + _unrealized_pnl(position, bar.close))

    return SingleAssetBacktestResult(
        initial_capital=float(initial_capital), timestamps=timestamps,
        equity_curve=equity_curve, trades=trades,
    )
