"""退出策略实验室核心(W4,spec §8)—— 纯函数,无 IO、无 RNG、无墙钟、无网络。

把 EXIT 提升为一等、可独立评估的研究对象:每个退出策略是一个纯函数
`evaluate(position_state, market_state, alpha_state, params) -> ExitDecision`,
在**决策点**(调仓期边界)只凭尾随数据判定 HOLD/REDUCE/EXIT。硬纪律(spec §2.3
「禁止以移动止损、复杂退出规则掩盖无效 Alpha」的机器化):

- **只能减、不能加**:所有策略输出 `target_fraction ∈ [0,1]`,执行权重 =
  信号权重 × fraction——退出策略永远无法制造信号之外的敞口,只能削减它。
- **baseline_signal_only 恒为对照**:实验室永远先跑纯信号基线(退出全由信号本身
  给出),每个策略只报相对该基线的增量,绝不以最终净 PnL 单独排名。
- **无前视(双保险)**:① MarketState 的全部取值访问器过滤 `event_time <= as_of`,
  策略即便被喂入含未来 bar 的序列也读不到(性质测试钉死:追加未来 bar 决策不变);
  ② 驱动器侧本就按 `fetch_bars_pit` + 决策点切片喂数据。
- **无证据不发明退出**:价格/ATR/波动率数据不足时一律 HOLD 直通( fail-safe 方向 =
  让信号自己说话),绝不因数据缺口凭空平仓。

锁定(latch)语义:硬性止损类(fixed/atr/chandelier/time/vol_adaptive)触发 EXIT 后
进入 `stopped_out` 锁定——信号不变向(归零或翻号)就保持空仓,防止「同信号下周
重新进场→再止损」的抖动循环;信号复位后自动重新武装。`risk_budget_reduce` 是
**无硬止损的减仓**(镜像 W6 drawdown_controller 语义,仓位级):乘数随回撤实时升降、
不锁定——episode 水位线由**信号**驱动(信号持同向就延续 episode,即便执行已减到 0),
价格修复时乘数自动回升,这正是它与硬止损的唯一差别。

阈值全部为 **UNCALIBRATED 工程默认**(文档化、参数化、版本化),诊断非门禁:
本模块不碰晋级/下单/实盘任何东西。
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Protocol, Sequence, Tuple

HOLD = "HOLD"
REDUCE = "REDUCE"
EXIT = "EXIT"


# ---------------------------------------------------------------------------
# 数据合约
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExitBar:
    """退出策略消费的最小日线 bar(驱动器从 PIT store 转换而来)。"""
    event_time: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class MarketState:
    """单一标的的市场状态。`bars` 允许含 as_of 之后的 bar;所有访问器只读
    `event_time <= as_of` 的行 —— 无前视在 API 层机器钉死(性质测试验证)。"""
    instrument: str
    as_of: int
    bars: Tuple[ExitBar, ...]

    def trailing_bars(self, n: Optional[int] = None) -> Tuple[ExitBar, ...]:
        rows = [b for b in self.bars if b.event_time <= self.as_of]
        if n is not None:
            rows = rows[-n:]
        return tuple(rows)

    def current_close(self) -> Optional[float]:
        rows = self.trailing_bars(1)
        return rows[-1].close if rows else None

    def atr(self, window: int) -> Optional[float]:
        """SMA-ATR(window 个 TR,需 window+1 根 bar)。数据不足 → None(调用方 HOLD)。"""
        if window < 1:
            raise ValueError(f"window must be >= 1, got {window}")
        rows = self.trailing_bars(window + 1)
        if len(rows) < window + 1:
            return None
        trs = []
        for prev, cur in zip(rows[-(window + 1):-1], rows[-window:]):
            trs.append(max(cur.high - cur.low,
                           abs(cur.high - prev.close),
                           abs(cur.low - prev.close)))
        return statistics.fmean(trs)

    def realized_daily_vol(self, lookback: int) -> Optional[float]:
        """尾随 lookback 个日收益的 pstdev(需 lookback+1 个收盘)。不足 → None。"""
        if lookback < 2:
            raise ValueError(f"lookback must be >= 2, got {lookback}")
        rows = self.trailing_bars(lookback + 1)
        if len(rows) < lookback + 1:
            return None
        closes = [b.close for b in rows]
        rets = [closes[i + 1] / closes[i] - 1.0 for i in range(len(closes) - 1)
                if closes[i] > 0]
        if len(rets) < lookback:
            return None
        return statistics.pstdev(rets)


@dataclass(frozen=True)
class PositionState:
    """仓位状态(episode 由信号驱动,见模块 docstring)。signed_qty 只承载方向,
    策略全部用百分比/水位线判定,与手数规模无关。"""
    instrument: str
    signed_qty: float                 # >0 多 / <0 空 / 0 平
    entry_price: Optional[float]      # episode 起始收盘价
    periods_held: int                 # episode 起始=0,每过一个决策期 +1
    high_water: Optional[float]       # episode 内最高收盘(≤as_of)
    low_water: Optional[float]        # episode 内最低收盘
    max_favorable_pct: float          # episode 内最大浮盈(对 entry 的比例,≥0)
    stopped_out: bool = False         # 硬止损锁定闩


@dataclass(frozen=True)
class AlphaState:
    """信号状态:本期/上期信号目标权重(退出策略只能对它做乘法削减)。"""
    signal_weight: float
    prev_signal_weight: float


@dataclass(frozen=True)
class ExitDecision:
    action: str                       # HOLD | REDUCE | EXIT
    target_fraction: float            # 信号权重乘数,恒 ∈ [0,1]
    reason_code: str
    policy_version: str


class ExitPolicyFn(Protocol):
    """策略协议:evaluate(position, market, alpha, params) -> ExitDecision。"""
    def __call__(self, position: PositionState, market: MarketState,
                 alpha: AlphaState, params) -> ExitDecision:  # pragma: no cover - 类型锚
        ...


# ---------------------------------------------------------------------------
# 参数(全冻结、版本化;阈值 = UNCALIBRATED 工程默认)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BaselineParams:
    pass


@dataclass(frozen=True)
class FixedStopParams:
    pct: float = 0.10                 # 浮亏 ≥10% 触发


@dataclass(frozen=True)
class AtrStopParams:
    mult: float = 3.0
    window: int = 14


@dataclass(frozen=True)
class ChandelierParams:
    mult: float = 3.0
    window: int = 22


@dataclass(frozen=True)
class TimeStopParams:
    max_periods: int = 8              # 持有 ≥8 期强平(锁定至信号复位)


@dataclass(frozen=True)
class ScaleOutParams:
    # (浮盈水位, 保留比例) 升序;触发最深档位的保留比例。单向(水位线),不回落。
    levels: Tuple[Tuple[float, float], ...] = ((0.10, 0.5), (0.20, 0.25))


@dataclass(frozen=True)
class VolAdaptiveStopParams:
    base_mult: float = 2.0
    vol_lookback: int = 30


@dataclass(frozen=True)
class RiskBudgetReduceParams:
    # (回撤水位, 乘数) 升序 —— 镜像 W6:5%→0.5 / 10%→0.25 / 20%→0.0,UNCALIBRATED。
    levels: Tuple[Tuple[float, float], ...] = ((0.05, 0.5), (0.10, 0.25), (0.20, 0.0))


# ---------------------------------------------------------------------------
# 判定辅助
# ---------------------------------------------------------------------------

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


_EPS = 1e-12   # 边界即触发:仅吸收浮点尘埃,远小于任何有意义的价格跳动


def _hold(version: str, reason: str = "NO_TRIGGER") -> ExitDecision:
    return ExitDecision(HOLD, 1.0, reason, version)


def _exit(version: str, reason: str) -> ExitDecision:
    return ExitDecision(EXIT, 0.0, reason, version)


def _reduce(version: str, fraction: float, reason: str) -> ExitDecision:
    return ExitDecision(REDUCE, _clamp01(fraction), reason, version)


def _direction(ps: PositionState) -> int:
    return 1 if ps.signed_qty > 0 else (-1 if ps.signed_qty < 0 else 0)


def _unrealized_pct(ps: PositionState, price: float) -> float:
    """浮盈比例(带方向):多 = price/entry-1;空 = entry/price-1 的等价 entry 基准。"""
    if ps.entry_price is None or ps.entry_price <= 0:
        return 0.0
    d = _direction(ps)
    return d * (price / ps.entry_price - 1.0)


def _drawdown_from_watermark(ps: PositionState, price: float) -> float:
    """自 episode 最优价的不利偏移(≥0):多 = 1-price/high_water;空 = 1-low_water/price。"""
    d = _direction(ps)
    if d > 0 and ps.high_water and ps.high_water > 0:
        return max(0.0, 1.0 - price / ps.high_water)
    if d < 0 and ps.low_water and ps.low_water > 0 and price > 0:
        return max(0.0, 1.0 - ps.low_water / price)
    return 0.0


def _guard(ps: PositionState, ms: MarketState, version: str):
    """公共 fail-safe:无仓位/无入场价/无现价 → HOLD 直通(不发明退出)。返回 price 或 None。"""
    if _direction(ps) == 0 or ps.entry_price is None:
        return None, _hold(version, "FLAT_OR_NO_ENTRY")
    price = ms.current_close()
    if price is None or price <= 0:
        return None, _hold(version, "NO_MARKET_DATA")
    return price, None


# ---------------------------------------------------------------------------
# 八个策略(全纯函数;version 串进每条决策)
# ---------------------------------------------------------------------------

V_BASELINE = "baseline_signal_only.v1"
V_FIXED = "fixed_stop.v1"
V_ATR = "atr_stop.v1"
V_CHANDELIER = "chandelier.v1"
V_TIME = "time_stop.v1"
V_SCALE = "scale_out.v1"
V_VOLAD = "vol_adaptive_stop.v1"
V_RISKBD = "risk_budget_reduce.v1"


def baseline_signal_only(ps: PositionState, ms: MarketState, alpha: AlphaState,
                         params: BaselineParams = BaselineParams()) -> ExitDecision:
    """对照组:恒 HOLD——退出完全由信号自身给出。实验室的控制基线。"""
    return _hold(V_BASELINE, "SIGNAL_ONLY_BASELINE")


def fixed_stop(ps: PositionState, ms: MarketState, alpha: AlphaState,
               params: FixedStopParams = FixedStopParams()) -> ExitDecision:
    """固定百分比止损:浮亏 ≥ pct → EXIT(锁定至信号复位)。"""
    price, early = _guard(ps, ms, V_FIXED)
    if early is not None:
        return early
    if _unrealized_pct(ps, price) <= -params.pct + _EPS:
        return _exit(V_FIXED, "FIXED_STOP_HIT")
    return _hold(V_FIXED)


def atr_stop(ps: PositionState, ms: MarketState, alpha: AlphaState,
             params: AtrStopParams = AtrStopParams()) -> ExitDecision:
    """ATR 止损:多 价格 ≤ entry - mult×ATR;空 价格 ≥ entry + mult×ATR → EXIT(锁定)。"""
    price, early = _guard(ps, ms, V_ATR)
    if early is not None:
        return early
    atr = ms.atr(params.window)
    if atr is None:
        return _hold(V_ATR, "ATR_INSUFFICIENT_DATA")
    d = _direction(ps)
    level = ps.entry_price - d * params.mult * atr
    if d * (price - level) <= _EPS:
        return _exit(V_ATR, "ATR_STOP_HIT")
    return _hold(V_ATR)


def chandelier(ps: PositionState, ms: MarketState, alpha: AlphaState,
               params: ChandelierParams = ChandelierParams()) -> ExitDecision:
    """吊灯止损(趋势候选):多 价格 ≤ high_water - mult×ATR;空 ≥ low_water + mult×ATR → EXIT(锁定)。"""
    price, early = _guard(ps, ms, V_CHANDELIER)
    if early is not None:
        return early
    atr = ms.atr(params.window)
    if atr is None:
        return _hold(V_CHANDELIER, "ATR_INSUFFICIENT_DATA")
    d = _direction(ps)
    water = ps.high_water if d > 0 else ps.low_water
    if water is None:
        return _hold(V_CHANDELIER, "NO_WATERMARK")
    level = water - d * params.mult * atr
    if d * (price - level) <= _EPS:
        return _exit(V_CHANDELIER, "CHANDELIER_HIT")
    return _hold(V_CHANDELIER)


def time_stop(ps: PositionState, ms: MarketState, alpha: AlphaState,
              params: TimeStopParams = TimeStopParams()) -> ExitDecision:
    """时间止损:持有 ≥ max_periods 期 → EXIT(锁定至信号复位)。不看价格。"""
    if _direction(ps) == 0:
        return _hold(V_TIME, "FLAT_OR_NO_ENTRY")
    if ps.periods_held >= params.max_periods:
        return _exit(V_TIME, "TIME_STOP_HIT")
    return _hold(V_TIME)


def scale_out(ps: PositionState, ms: MarketState, alpha: AlphaState,
              params: ScaleOutParams = ScaleOutParams()) -> ExitDecision:
    """分批止盈:浮盈水位线触及档位 → REDUCE 到该档保留比例(单向、不锁定)。"""
    if _direction(ps) == 0 or ps.entry_price is None:
        return _hold(V_SCALE, "FLAT_OR_NO_ENTRY")
    fraction = None
    for i, (level, keep) in enumerate(sorted(params.levels)):
        if ps.max_favorable_pct >= level - _EPS:
            fraction = (i, keep)
    if fraction is None:
        return _hold(V_SCALE)
    return _reduce(V_SCALE, fraction[1], f"SCALE_OUT_LEVEL_{fraction[0]}")


def vol_adaptive_stop(ps: PositionState, ms: MarketState, alpha: AlphaState,
                      params: VolAdaptiveStopParams = VolAdaptiveStopParams()) -> ExitDecision:
    """波动自适应止损:距离 = base_mult × σ_daily × √max(1,periods_held)
    (随机游走下 t 期位移按 √t 缩放),浮亏超过 → EXIT(锁定)。"""
    price, early = _guard(ps, ms, V_VOLAD)
    if early is not None:
        return early
    sigma = ms.realized_daily_vol(params.vol_lookback)
    if sigma is None:
        return _hold(V_VOLAD, "VOL_INSUFFICIENT_DATA")
    horizon = math.sqrt(max(1, ps.periods_held))
    if _unrealized_pct(ps, price) <= -(params.base_mult * sigma * horizon) + _EPS:
        return _exit(V_VOLAD, "VOL_ADAPTIVE_STOP_HIT")
    return _hold(V_VOLAD)


def risk_budget_reduce(ps: PositionState, ms: MarketState, alpha: AlphaState,
                       params: RiskBudgetReduceParams = RiskBudgetReduceParams()) -> ExitDecision:
    """风险预算减仓(W6 仓位级镜像,无硬止损):回撤自水位线 ≥5%→×0.5 / ≥10%→×0.25 /
    ≥20%→×0.0。乘数随价格修复自动回升,不锁定 —— 与硬止损的唯一差别即无闩锁。"""
    price, early = _guard(ps, ms, V_RISKBD)
    if early is not None:
        return early
    dd = _drawdown_from_watermark(ps, price)
    mult = 1.0
    for level, keep in sorted(params.levels):
        if dd >= level - _EPS:
            mult = keep
    if mult >= 1.0:
        return _hold(V_RISKBD)
    if mult <= 0.0:
        return _reduce(V_RISKBD, 0.0, "RISK_BUDGET_FLAT")
    return _reduce(V_RISKBD, mult, "RISK_BUDGET_DE_RISK")


@dataclass(frozen=True)
class RegisteredPolicy:
    name: str
    fn: Callable[[PositionState, MarketState, AlphaState, object], ExitDecision]
    params: object
    version: str
    description: str


POLICY_REGISTRY: Dict[str, RegisteredPolicy] = {
    p.name: p for p in (
        RegisteredPolicy("baseline_signal_only", baseline_signal_only, BaselineParams(),
                         V_BASELINE, "对照:恒 HOLD,退出全由信号给出"),
        RegisteredPolicy("fixed_stop", fixed_stop, FixedStopParams(),
                         V_FIXED, "固定百分比止损(锁定)"),
        RegisteredPolicy("atr_stop", atr_stop, AtrStopParams(),
                         V_ATR, "ATR 止损(锁定)"),
        RegisteredPolicy("chandelier", chandelier, ChandelierParams(),
                         V_CHANDELIER, "吊灯追踪止损(锁定)"),
        RegisteredPolicy("time_stop", time_stop, TimeStopParams(),
                         V_TIME, "时间止损(锁定)"),
        RegisteredPolicy("scale_out", scale_out, ScaleOutParams(),
                         V_SCALE, "分批止盈(水位线单向减仓,不锁定)"),
        RegisteredPolicy("vol_adaptive_stop", vol_adaptive_stop, VolAdaptiveStopParams(),
                         V_VOLAD, "波动自适应止损(base_mult×σ×√t,锁定)"),
        RegisteredPolicy("risk_budget_reduce", risk_budget_reduce, RiskBudgetReduceParams(),
                         V_RISKBD, "W6 仓位级风险预算减仓(无闩锁)"),
    )
}


# ---------------------------------------------------------------------------
# 权重流后处理器(实验室与干跑流水线之间的接缝)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExitEvent:
    """一条决策留痕(全字段值,可 JSON 化)。"""
    t: int
    instrument: str
    action: str
    fraction: float
    reason_code: str
    policy_version: str
    signal_weight: float
    executed_weight: float
    price: Optional[float]


@dataclass(frozen=True)
class ExitLabStream:
    """后处理结果:修改后的权重流 + 决策日志。weights 与输入同形({t: {inst: w}},
    零权重恒省略 —— 与 plan_target_quantities 的 q==0 跳过语义一致)。"""
    weights: Dict[int, Dict[str, float]]
    events: Tuple[ExitEvent, ...]


@dataclass
class _Episode:
    direction: int
    entry_price: Optional[float]
    periods_held: int
    high_water: Optional[float]
    low_water: Optional[float]
    max_favorable_pct: float
    stopped_out: bool


def apply_exit_policy(*, periods: Sequence[int], weights_by_period: Dict[int, Dict[str, float]],
                      bars_by_instrument: Dict[str, Sequence[ExitBar]],
                      policy: str, params=None) -> ExitLabStream:
    """把一条退出策略应用到基线权重流(纯函数、逐期逐标的独立判定)。

    接缝纪律:输出只做「信号权重 × [0,1] 乘数」,形状与输入相同,可原样喂给
    `run_multi_period_dry_run`(门/对账/成本逻辑零改动)。episode(入场价/水位线/
    持有期数/锁定闩)由**信号方向**驱动:信号归零或翻号 = episode 结束 + 锁定复位;
    信号同向延续 = episode 延续(即便执行已被减到 0,risk_budget 乘数才能随修复回升)。
    """
    reg = POLICY_REGISTRY.get(policy)
    if reg is None:
        raise KeyError(f"unknown exit policy: {policy!r} (known: {sorted(POLICY_REGISTRY)})")
    fn, version = reg.fn, reg.version
    prm = reg.params if params is None else params
    instruments = sorted({inst for w in weights_by_period.values() for inst in w})
    episodes: Dict[str, Optional[_Episode]] = {inst: None for inst in instruments}
    prev_sig: Dict[str, float] = {inst: 0.0 for inst in instruments}
    out: Dict[int, Dict[str, float]] = {}
    events: list = []
    for t in periods:
        w_t = weights_by_period.get(t, {})
        out_t: Dict[str, float] = {}
        for inst in instruments:
            sig = float(w_t.get(inst, 0.0))
            ms = MarketState(inst, int(t), tuple(bars_by_instrument.get(inst, ())))
            px = ms.current_close()
            if sig == 0.0:                      # 信号离场:episode 结束,锁定复位
                episodes[inst] = None
                prev_sig[inst] = 0.0
                continue
            d = 1 if sig > 0 else -1
            ep = episodes[inst]
            if ep is None or ep.direction != d:  # 新 episode(含信号翻号 → 锁定复位)
                ep = _Episode(d, px, 0, px, px, 0.0, False)
                episodes[inst] = ep
            else:
                ep.periods_held += 1
                if px is not None and px > 0:
                    ep.high_water = px if ep.high_water is None else max(ep.high_water, px)
                    ep.low_water = px if ep.low_water is None else min(ep.low_water, px)
                    if ep.entry_price and ep.entry_price > 0:
                        fav = d * (px / ep.entry_price - 1.0)
                        ep.max_favorable_pct = max(ep.max_favorable_pct, fav)
            if ep.stopped_out:                  # 锁定中:信号未复位 → 保持空仓
                events.append(ExitEvent(int(t), inst, EXIT, 0.0, "STOP_LOCKOUT",
                                        version, sig, 0.0, px))
                prev_sig[inst] = sig
                continue
            ps = PositionState(inst, float(d), ep.entry_price, ep.periods_held,
                               ep.high_water, ep.low_water, ep.max_favorable_pct, False)
            alpha = AlphaState(sig, prev_sig[inst])
            if px is None or ep.entry_price is None:
                decision = _hold(version, "NO_MARKET_DATA")   # fail-safe:直通
            else:
                decision = fn(ps, ms, alpha, prm)
            if decision.action == EXIT:
                executed = 0.0
                ep.stopped_out = True
            else:
                executed = sig * _clamp01(decision.target_fraction)
            if executed != 0.0:
                out_t[inst] = executed
            events.append(ExitEvent(int(t), inst, decision.action,
                                    _clamp01(decision.target_fraction) if decision.action != EXIT else 0.0,
                                    decision.reason_code, decision.policy_version,
                                    sig, executed, px))
            prev_sig[inst] = sig
        if out_t:
            out[int(t)] = out_t
    return ExitLabStream(out, tuple(events))


# ---------------------------------------------------------------------------
# 实验室度量(纯函数;Sharpe/回撤直接读流水线 run.pnl,这里只算增量指标)
# ---------------------------------------------------------------------------

def period_dollar_pnl(equities: Sequence[float], aum: float) -> list:
    """逐期美元 PnL = equity_t - equity_{t-1}(equity 已含成本/资金费,equity_0 之前 = aum)。"""
    out, prev = [], float(aum)
    for e in equities:
        out.append(float(e) - prev)
        prev = float(e)
    return out


def profit_factor(period_pnls: Sequence[float]) -> Optional[float]:
    """盈利因子 = Σ正期PnL / |Σ负期PnL|。无亏损期 → None(诚实,不报 inf)。"""
    gains = sum(p for p in period_pnls if p > 0)
    losses = -sum(p for p in period_pnls if p < 0)
    if losses <= 0:
        return None
    return round(gains / losses, 4)


def right_tail_share(period_pnls: Sequence[float], *, decile: int = 10) -> Optional[float]:
    """右尾贡献:按逐期 PnL 降序取 top ceil(n/decile) 期,其和占总 PnL 的份额。
    总 PnL ≤ 0 → None(「右尾贡献」对亏损书无意义,诚实不硬算)。份额 >1 表示
    右尾之外净亏损。"""
    total = sum(period_pnls)
    if total <= 0 or not period_pnls:
        return None
    k = max(1, math.ceil(len(period_pnls) / decile))
    top = sum(sorted(period_pnls, reverse=True)[:k])
    return round(top / total, 4)


def delta_concentration(period_deltas: Sequence[float], *, hard_min: int = 5,
                        soft_min: int = 10) -> dict:
    """增量集中度(诚实):把逐期 (policy − baseline) 美元差降序,累计达到总正增量
    所需的最少期数 k。总增量 ≤0 → k=None(无正增量可集中)。k<hard_min → 选择规则
    硬失败;k<soft_min → selection_warning。"""
    total = sum(period_deltas)
    if total <= 0:
        return {"total_delta": round(total, 6), "periods_for_delta": None,
                "concentration_ok": True, "selection_warning": None}
    k, cum = 0, 0.0
    for d in sorted(period_deltas, reverse=True):
        if d <= 0:
            break
        cum += d
        k += 1
        if cum >= total:
            break
    warning = None
    if k < hard_min:
        warning = (f"CONCENTRATED_DELTA: entire positive delta covered by {k}<{hard_min} "
                   f"periods — a handful of periods, not a robust exit edge")
    elif k < soft_min:
        warning = (f"NARROW_DELTA: {k} periods cover the delta (<{soft_min}) — treat any "
                   f"selection as tentative")
    return {"total_delta": round(total, 6), "periods_for_delta": k,
            "concentration_ok": k >= hard_min, "selection_warning": warning}


SELECTION_RULE_VERSION = "exit_lab_selection.v1"
SELECTION_RULE_TEXT = (
    "A policy is 'worth further study' ONLY IF ALL of: "
    "(1) Sharpe delta > 0 (RAW series); "
    "(2) maxDD not worsened by more than 2pp (mdd_delta <= 0.02); "
    "(3) delta not concentrated: if incremental PnL > 0, at least 5 periods are needed "
    "to cover it (negative/zero delta cannot be 'concentrated' and does not block); "
    "(4) profit factor >= baseline profit factor (both computed; None PF fails unless "
    "baseline PF is also None). "
    "Ranking is reported on multiple criteria (Sharpe delta / maxDD delta / PF delta); "
    "selection by final net PnL alone is FORBIDDEN (spec §8/§2.3)."
)


def evaluate_selection(*, sharpe_delta: float, mdd_delta: float, profit_factor_value,
                       baseline_profit_factor, delta_pnl: float, periods_for_delta,
                       mdd_tolerance: float = 0.02, min_periods: int = 5) -> dict:
    """预注册选择规则(SELECTION_RULE_TEXT)的机器执行。返回逐条结果 + 总判定。"""
    sharpe_ok = sharpe_delta > 0
    mdd_ok = mdd_delta <= mdd_tolerance
    conc_ok = True if delta_pnl <= 0 else (periods_for_delta is not None
                                           and periods_for_delta >= min_periods)
    if profit_factor_value is None:
        pf_ok = baseline_profit_factor is None
    elif baseline_profit_factor is None:
        pf_ok = True
    else:
        pf_ok = profit_factor_value >= baseline_profit_factor
    worth = sharpe_ok and mdd_ok and conc_ok and pf_ok
    failed = [name for name, ok in (("sharpe_delta>0", sharpe_ok),
                                    (f"mdd_delta<={mdd_tolerance}", mdd_ok),
                                    (f"periods_for_delta>={min_periods}", conc_ok),
                                    ("profit_factor>=baseline", pf_ok)) if not ok]
    return {"sharpe_ok": sharpe_ok, "mdd_ok": mdd_ok, "concentration_ok": conc_ok,
            "pf_ok": pf_ok, "worth_further_study": worth, "failed": failed,
            "rule_version": SELECTION_RULE_VERSION}
