"""Live-vs-replay 衰减测量纯核(W1:研究→实盘之间的收益漏损在哪里,2026-07-24)。

回答的问题:**同一条策略,纸面(replay,模型成本)与真实执行(live 账本)在同一段窗口上
各赚/亏了多少,差额由哪些成分构成。** 输入全部是调用方(驱动器 `tools/live_degradation.py`)
在 IO 边界备好的普通 dict:live 账本行(orders/fills/TCA)、重放汇总(模型成本/资金费/净值)、
逐日 mark/资金费率矩阵。本模块纯函数、无 IO/网络/墙钟/RNG。

诚实纪律(规格红线:missing data marked UNKNOWN, never silently estimated):
- 账本不记录 venue 手续费与资金费现金流 -> `fee_actual`/`funding_actual` 恒 None,
  字段名进 `unknown_fields`;绝不拿模型估计冒充实际值。
- `live_net_pnl` = 已记录现实里的净额 = 实现+浮动交易 PnL(成交价已内含滑点);
  真实净额 = live_net − fee_actual + funding_actual(后两者 UNKNOWN,如实标注)。
- `live_degradation_ratio` 在 replay_net ≈ 0 或任一侧 None 时 -> None + reason,绝不硬算。
- 分解恒等式(已记录现实内精确成立,测试钉死):
    live_net − replay_net = trading_pnl_delta + replay_model_cost − replay_funding
    trading_pnl_delta = −slippage_cost_actual + position_timing_effect
  residual 是该恒等式的浮点残差(≈0),不是"解释不了就往里扔"的口袋。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from live.execution.filters import decision_prefix


# 已知的拒绝/撤销终态(raw venue 状态串;canonical_state 缺失/迁移前存量行的回退口径)
_REJECT_STATUSES = frozenset({"REJECTED", "CANCELED", "CANCELLED", "EXPIRED", "FAILED"})

# 显著性机械门槛(不是统计断言——样本/窗口太小时如实标注"只读描述、别外推")
_MIN_MEANINGFUL_FILLS = 30
_MIN_MEANINGFUL_DAYS = 7
# 比值分母地板:|replay_net| < 0.1%·aum 时 live/replay 是噪声放大(如 replay_net $2
# 时比值 56×),机械守护为 None+reason——绝不把运气报成 56 倍衰减/超额。
_RATIO_MIN_DENOM_FRAC_OF_AUM = 0.001


def _signed_qty(side: str, quantity: float) -> float:
    return float(quantity) if str(side).upper() == "BUY" else -float(quantity)


def positions_from_fills(fills: Sequence[Mapping]) -> Dict[str, float]:
    """全部 fills 的带符号净仓位(账户级口径同 LiveLedger.all_positions 的数学,纯函数)。"""
    out: Dict[str, float] = {}
    for f in fills:
        inst = str(f["instrument_id"])
        out[inst] = out.get(inst, 0.0) + _signed_qty(f["side"], float(f["quantity"]))
    return out


def turnover_notional(fills: Sequence[Mapping]) -> float:
    """Σ qty×price(单边成交额)——模型成本假设(fee_expected)作用的基数。"""
    return sum(abs(float(f["quantity"]) * float(f["price"])) for f in fills)


def mtm_trading_pnl(fills: Sequence[Mapping], marks: Mapping[str, float]) -> Tuple[Optional[float], List[str]]:
    """Σ signed_qty×(mark−fill_price) = 实现+浮动交易 PnL(平均成本恒等式,测试对 reconcile 钉死)。

    任一持仓标的缺 mark -> (None, missing) ——诚实缺失,绝不用最后可得价/邻居价凑数。"""
    missing = sorted({str(f["instrument_id"]) for f in fills
                      if marks.get(str(f["instrument_id"])) is None
                      or float(marks.get(str(f["instrument_id"]), 0.0) or 0.0) <= 0})
    if missing:
        return None, missing
    total = 0.0
    for f in fills:
        mark = float(marks[str(f["instrument_id"])])
        total += _signed_qty(f["side"], float(f["quantity"])) * (mark - float(f["price"]))
    return total, []


def positions_by_day(fills: Sequence[Mapping], day_grid: Sequence[int]) -> List[Dict[str, float]]:
    """逐日(网格行 t = 当日 00:00)持仓快照:fill 在 filled_at <= t 时入账。

    决策/成交发生在日内的,从**下一行**起进持仓——与"行 t 的 mark 是 t 时刻可见价"一致,
    不拿当天收盘价给当天盘中的成交做 MTM(那会引入半天免费的前视)。"""
    ordered = sorted(fills, key=lambda f: (int(f["filled_at"]), str(f.get("fill_id", ""))))
    out: List[Dict[str, float]] = []
    held: Dict[str, float] = {}
    idx = 0
    for t in day_grid:
        while idx < len(ordered) and int(ordered[idx]["filled_at"]) <= int(t):
            f = ordered[idx]
            inst = str(f["instrument_id"])
            held[inst] = held.get(inst, 0.0) + _signed_qty(f["side"], float(f["quantity"]))
            idx += 1
        out.append(dict(held))
    return out


def live_daily_series(fills: Sequence[Mapping], day_grid: Sequence[int],
                      marks_by_day: Sequence[Mapping[str, float]], aum: float
                      ) -> Tuple[List[dict], List[str]]:
    """live 侧逐日 MTM 净值序列:equity_t = aum + 当日持仓 MTM 交易 PnL。

    某日任一持仓标的缺 mark -> 该日 equity/gross_pnl = None(诚实空洞,不插值);
    缺标的名单汇总返回(进 unknown_fields)。"""
    if len(day_grid) != len(marks_by_day):
        raise ValueError("day_grid and marks_by_day must align")
    series: List[dict] = []
    missing_names: set = set()
    ordered = sorted(fills, key=lambda f: (int(f["filled_at"]), str(f.get("fill_id", ""))))
    for t, marks in zip(day_grid, marks_by_day):
        # 该日已入账 fills(fill_at <= t)的 MTM——按 fill 切片保留成本基,与
        # positions_by_day 同一入账规则(当日盘中成交从下一行起进 MTM,不前视)。
        day_fills = [f for f in ordered if int(f["filled_at"]) <= int(t)]
        pnl, missing = mtm_trading_pnl(day_fills, marks)
        missing_names.update(missing)
        series.append({"t": int(t),
                       "gross_pnl": None if pnl is None else round(pnl, 6),
                       "equity": None if pnl is None else round(float(aum) + pnl, 6)})
    return series, sorted(missing_names)


def tca_slippage_cost(tca_rows: Sequence[Mapping]) -> Tuple[Optional[float], int]:
    """TCA 覆盖成交的美元滑点成本:Σ sign×(fill_price−decision_price)×qty(买贵/卖贱为正=成本)。

    返回 (cost, n_rows_skipped)。一行都没有 -> (None, 0)(诚实:不是零成本,是没有记录);
    单行 decision/fill 价非法 -> 跳过并计数(不污染合计)。"""
    if not tca_rows:
        return None, 0
    total, skipped = 0.0, 0
    for r in tca_rows:
        try:
            dp, fp, qty = float(r["decision_price"]), float(r["fill_price"]), float(r["quantity"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if dp <= 0 or fp <= 0 or qty <= 0:
            skipped += 1
            continue
        sign = 1.0 if str(r["side"]).upper() == "BUY" else -1.0
        total += sign * (fp - dp) * qty
    return total, skipped


def weighted_slippage_bps(tca_rows: Sequence[Mapping]) -> Optional[float]:
    """名义额加权滑点(bps,镜像 tca.py::tca_summary 口径):用落库的 slippage_bps(写时算好,
    带方向),None 行不进加权;无有效行 -> None。"""
    valid = [r for r in tca_rows if r.get("slippage_bps") is not None]
    if not valid:
        return None
    notionals = [abs(float(r["fill_price"]) * float(r["quantity"])) for r in valid]
    total = sum(notionals)
    if total <= 0:
        return None
    return sum(float(r["slippage_bps"]) * n for r, n in zip(valid, notionals)) / total


def arrival_slippage_coverage(tca_rows: Sequence[Mapping]) -> Tuple[Optional[float], int]:
    """到达价快照口径滑点(arrival_price 基线)的覆盖率与加权值。

    存量 TCA 行的 arrival_* 全 NULL(决策价代理,见 position_ledger 迁移注释)-> (None, 0):
    "相对真实到达价的滑点"不可知,诚实 UNKNOWN,绝不拿决策价代理冒充到达价。"""
    rows = [r for r in tca_rows if r.get("arrival_price") is not None]
    if not rows:
        return None, 0
    weighted = weighted_slippage_bps(rows)
    return weighted, len(rows)


def expected_funding(positions: Sequence[Mapping[str, float]], day_grid: Sequence[int],
                     rates_by_day: Sequence[Mapping[str, float]],
                     marks_by_day: Sequence[Mapping[str, float]]
                     ) -> Tuple[Optional[float], int]:
    """模型侧资金费预期(作用在 **live 实际持仓**上):Σ_t Σ_i −rate×qty×mark。

    这是"按记录的资金费利率,live 这本账本应收/付多少"的模型量——不是 venue 实际扣收
    (funding_actual 恒 UNKNOWN)。某持仓标的某日缺 rate 或 mark -> 该格跳过并计数;
    有跳过 -> 返回值是**偏**合计(调用方把 funding_expected_complete 列进 unknown_fields);
    全窗口一个格都算不出 -> None。"""
    total, skipped, computed = 0.0, 0, 0
    for held, t, rates, marks in zip(positions, day_grid, rates_by_day, marks_by_day):
        for inst, qty in held.items():
            if qty == 0.0:
                continue
            rate = rates.get(inst)
            mark = marks.get(inst)
            if rate is None or mark is None or float(mark) <= 0:
                skipped += 1
                continue
            total += -float(rate) * qty * float(mark)
            computed += 1
    if computed == 0:
        return None, skipped
    return total, skipped


def fee_expected(turnover: float, cost_bps: float) -> float:
    """模型成本假设(flat bps,与 canonical 干跑同一成本模型)作用在 live 成交额上的预期成本。"""
    return float(turnover) * float(cost_bps) / 1e4


def degradation_ratio(live_net: Optional[float], replay_net: Optional[float],
                      *, min_denominator: float = 1e-9) -> Tuple[Optional[float], Optional[str]]:
    """live_net/replay_net 守护:任一侧未知 / 分母≈0 / 异号(比值失去"衰减"语义)-> None + reason。"""
    if live_net is None:
        return None, "live_net_unknown"
    if replay_net is None:
        return None, "replay_net_unknown"
    if abs(replay_net) < min_denominator:
        return None, "replay_net_approx_zero_ratio_meaningless"
    if (live_net > 0) != (replay_net > 0):
        return None, "sign_flip_ratio_meaningless"
    return live_net / replay_net, None


def recover_decision_ts(orders: Sequence[Mapping], strategy_id: str,
                        candidate_decided_ts: Sequence[int]) -> Tuple[Dict[str, int], List[str]]:
    """用 client_order_id 的 16 位决策前缀反推每笔订单的 decided_ts(完整 trace 验证,纯哈希)。

    候选集由驱动器给出(panel 行 ∪ 前向台账 decided_ts,窗口内有界)。返回
    ({client_order_id: decided_ts}, [无 trace 的 client_order_id])——后者如实列出
    (如 quantos-smoke-* 手工运维单),绝不猜。"""
    candidates = sorted({int(t) for t in candidate_decided_ts})
    prefix_to_ts: Dict[str, int] = {}
    for o in orders:
        cid = str(o["client_order_id"])
        if len(cid) < 16:
            continue
        prefix = cid[:16]
        if prefix in prefix_to_ts:
            continue
        for ts in candidates:
            if decision_prefix(strategy_id, ts, str(o["instrument_id"])) == prefix:
                prefix_to_ts[prefix] = ts
                break
    traced = {str(o["client_order_id"]): prefix_to_ts[str(o["client_order_id"])[:16]]
              for o in orders
              if len(str(o["client_order_id"])) >= 16 and str(o["client_order_id"])[:16] in prefix_to_ts}
    untraced = [str(o["client_order_id"]) for o in orders
                if str(o["client_order_id"]) not in traced]
    return traced, untraced


@dataclass(frozen=True)
class DegradationComponents:
    """漏损成分分解(美元;None = 记录不可导出的 UNKNOWN,绝不估算)。"""
    fee_expected: Optional[float] = None           # 模型成本假设 × live 成交额
    fee_actual: Optional[float] = None             # 账本不记录 venue 手续费 -> 恒 None
    slippage_cost_actual: Optional[float] = None   # TCA 记录的真实滑点成本(决策价基线)
    slippage_vs_arrival: Optional[float] = None    # 到达价基线滑点(存量账本无快照 -> None)
    funding_expected: Optional[float] = None       # 模型资金费预期(作用在 live 持仓,记录利率)
    funding_actual: Optional[float] = None         # 账本不记录 venue 资金费现金流 -> 恒 None
    replay_model_cost: Optional[float] = None      # replay 被扣的模型成本
    replay_funding: Optional[float] = None         # replay 累计的模型资金费
    trading_pnl_delta: Optional[float] = None      # live_gross − replay_gross
    position_timing_effect: Optional[float] = None # trading_delta + slippage( timing/构成/漏调仓)
    residual: Optional[float] = None               # 分解恒等式浮点残差(≈0)

    def as_dict(self) -> dict:
        return {k: (None if v is None else round(float(v), 6))
                for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class DegradationReport:
    """单 venue×strategy×窗口 的 live-vs-replay 衰减报告。"""
    strategy_id: str
    venue: str
    window: Mapping
    live_net_pnl: Optional[float]
    replay_net_pnl: Optional[float]
    live_gross_pnl: Optional[float]
    replay_gross_pnl: Optional[float]
    live_degradation_ratio: Optional[float]
    degradation_ratio_reason: Optional[str]
    cost_overrun: Optional[float]
    cost_overrun_recorded_part: Optional[float]
    slippage_bps_weighted: Optional[float]
    fill_rate: Optional[float]
    n_orders: int
    n_fills: int
    n_rejects: int
    n_tca: int
    n_fills_without_tca: int
    unknown_fields: Tuple[str, ...] = ()
    components: DegradationComponents = field(default_factory=DegradationComponents)
    daily: Tuple[Mapping, ...] = ()

    def as_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "venue": self.venue,
            "window": dict(self.window),
            "live_net_pnl": self.live_net_pnl,
            "replay_net_pnl": self.replay_net_pnl,
            "live_gross_pnl": self.live_gross_pnl,
            "replay_gross_pnl": self.replay_gross_pnl,
            "live_degradation_ratio": self.live_degradation_ratio,
            "degradation_ratio_reason": self.degradation_ratio_reason,
            "cost_overrun": self.cost_overrun,
            "cost_overrun_recorded_part": self.cost_overrun_recorded_part,
            "slippage_bps_weighted": self.slippage_bps_weighted,
            "fill_rate": self.fill_rate,
            "n_orders": self.n_orders,
            "n_fills": self.n_fills,
            "n_rejects": self.n_rejects,
            "n_tca": self.n_tca,
            "n_fills_without_tca": self.n_fills_without_tca,
            "unknown_fields": list(self.unknown_fields),
            "components": self.components.as_dict(),
            "daily": [dict(d) for d in self.daily],
        }


def significance_label(n_fills: int, n_days: int) -> dict:
    """机械显著性标注(非统计检验):样本/窗口太小 -> SMALL_SAMPLE,如实说"只读描述"。"""
    if n_fills < _MIN_MEANINGFUL_FILLS:
        return {"level": "SMALL_SAMPLE",
                "reason": f"n_fills={n_fills} < {_MIN_MEANINGFUL_FILLS} — 样本太小,比值只作描述、"
                          f"不构成任何显著性断言"}
    if n_days < _MIN_MEANINGFUL_DAYS:
        return {"level": "SMALL_SAMPLE",
                "reason": f"window={n_days}d < {_MIN_MEANINGFUL_DAYS}d — 窗口太短,比值只作描述"}
    return {"level": "DESCRIPTIVE_ONLY",
            "reason": "单一样本期的事后对照,无重复抽样——描述性数字,非显著性检验"}


def build_degradation_report(*, strategy_id: str, venue: str, window: Mapping,
                             fills: Sequence[Mapping], orders: Sequence[Mapping],
                             tca_rows: Sequence[Mapping],
                             day_grid: Sequence[int],
                             marks_by_day: Sequence[Mapping[str, float]],
                             rates_by_day: Sequence[Mapping[str, float]],
                             replay: Optional[Mapping], aum: float,
                             cost_bps: float) -> DegradationReport:
    """装配 DegradationReport。`replay` = {"net_pnl","gross_pnl","model_cost","funding",
    "curve":[{t,equity}]}(驱动器 `_replay_book` 产出——干跑流水线同款原语,等价性测试钉死);
    None = 重放不可构造(如已执行决策缺记录在案权重)-> replay 侧全 None + unknown_fields。"""
    unknown: List[str] = []

    live_gross, missing_marks = mtm_trading_pnl(fills, marks_by_day[-1] if marks_by_day else {})
    for name in missing_marks:
        unknown.append(f"marks:{name}")
    # 已记录现实里的 live 净额:成交价内含真实滑点;venue 手续费/资金费本地无记录。
    live_net = live_gross
    unknown += ["fee_actual", "funding_actual"]

    turnover = turnover_notional(fills)
    fee_exp = fee_expected(turnover, cost_bps)
    slip_cost, n_tca_skipped = tca_slippage_cost(tca_rows)
    slip_bps = weighted_slippage_bps(tca_rows)
    slip_arrival, n_arrival = arrival_slippage_coverage(tca_rows)
    if slip_arrival is None and tca_rows:
        unknown.append("slippage_vs_arrival")          # 只有决策价代理,无真实到达价快照
    if n_tca_skipped:
        unknown.append(f"tca_rows_invalid:{n_tca_skipped}")

    day_positions = positions_by_day(fills, day_grid)
    funding_exp, n_funding_skipped = expected_funding(day_positions, day_grid,
                                                      rates_by_day, marks_by_day)
    if funding_exp is None:
        unknown.append("funding_expected")
    elif n_funding_skipped:
        unknown.append("funding_expected_complete")    # 偏合计:有持仓标的-日缺 rate/mark

    live_series, series_missing = live_daily_series(fills, day_grid, marks_by_day, aum)
    for name in series_missing:
        tag = f"daily_marks:{name}"
        if tag not in unknown:
            unknown.append(tag)

    # 订单/成交计数与 fill_rate
    n_orders = len(orders)
    n_filled_orders = sum(1 for o in orders if str(o.get("status", "")).upper() == "FILLED")
    n_rejects = sum(1 for o in orders
                    if str(o.get("status", "")).upper() in _REJECT_STATUSES
                    or str(o.get("canonical_state", "")).upper() in _REJECT_STATUSES)
    fill_rate = (n_filled_orders / n_orders) if n_orders else None
    if fill_rate is None:
        unknown.append("fill_rate")
    tca_fill_ids = {str(r.get("fill_id")) for r in tca_rows} | {
        str(r.get("client_order_id")) for r in tca_rows}
    n_fills_without_tca = sum(
        1 for f in fills
        if str(f.get("fill_id")) not in tca_fill_ids
        and str(f.get("client_order_id")) not in tca_fill_ids)

    # replay 侧
    if replay is None:
        replay_net = replay_gross = replay_cost = replay_funding = None
        replay_curve: Sequence[Mapping] = ()
        unknown += ["replay_net_pnl", "replay_gross_pnl", "replay_model_cost", "replay_funding"]
    else:
        replay_net = replay.get("net_pnl")
        replay_gross = replay.get("gross_pnl")
        replay_cost = replay.get("model_cost")
        replay_funding = replay.get("funding")
        replay_curve = replay.get("curve") or ()
        if replay_net is None:
            unknown.append("replay_net_pnl")

    ratio_floor = max(1e-9, _RATIO_MIN_DENOM_FRAC_OF_AUM * abs(float(aum)))
    ratio, ratio_reason = degradation_ratio(live_net, replay_net, min_denominator=ratio_floor)
    if ratio_reason == "replay_net_approx_zero_ratio_meaningless":
        ratio_reason = (f"replay_net_approx_zero_ratio_meaningless"
                        f"(|replay_net|<{ratio_floor:.4g}=0.1%·aum)")

    # 成本超支:总额口径要求 fee_actual,缺失 -> None(UNKNOWN);已记录部分单独给。
    cost_overrun = None
    unknown.append("cost_overrun")
    cost_overrun_recorded = (slip_cost - fee_exp) if slip_cost is not None else None
    if cost_overrun_recorded is None:
        unknown.append("cost_overrun_recorded_part")

    # 分解恒等式(模块 docstring):残差 ≈ 0(浮点尘),非"解释口袋"。
    trading_delta = (live_gross - replay_gross) if (live_gross is not None
                                                    and replay_gross is not None) else None
    position_timing = (trading_delta + slip_cost) if (trading_delta is not None
                                                      and slip_cost is not None) else None
    residual = None
    if (live_net is not None and replay_net is not None and trading_delta is not None
            and replay_cost is not None and replay_funding is not None):
        # gap = live_gross − (replay_gross − cost + funding) = trading_delta + cost − funding
        residual = (live_net - replay_net) - (trading_delta + replay_cost - replay_funding)

    # 逐日瀑布:live 序列 × replay 曲线按 t 对齐
    curve_by_t = {int(c["t"]): c.get("equity") for c in replay_curve}
    daily = []
    for row in live_series:
        rep_eq = curve_by_t.get(int(row["t"]))
        live_eq = row["equity"]
        daily.append({"t": int(row["t"]),
                      "live_equity": live_eq,
                      "replay_equity": None if rep_eq is None else round(float(rep_eq), 6),
                      "gap": (round(live_eq - float(rep_eq), 6)
                              if live_eq is not None and rep_eq is not None else None)})

    return DegradationReport(
        strategy_id=strategy_id, venue=venue, window=dict(window),
        live_net_pnl=None if live_net is None else round(live_net, 6),
        replay_net_pnl=None if replay_net is None else round(float(replay_net), 6),
        live_gross_pnl=None if live_gross is None else round(live_gross, 6),
        replay_gross_pnl=None if replay_gross is None else round(float(replay_gross), 6),
        live_degradation_ratio=None if ratio is None else round(ratio, 6),
        degradation_ratio_reason=ratio_reason,
        cost_overrun=cost_overrun,
        cost_overrun_recorded_part=(None if cost_overrun_recorded is None
                                    else round(cost_overrun_recorded, 6)),
        slippage_bps_weighted=None if slip_bps is None else round(slip_bps, 6),
        fill_rate=fill_rate,
        n_orders=n_orders, n_fills=len(fills), n_rejects=n_rejects,
        n_tca=len(tca_rows), n_fills_without_tca=n_fills_without_tca,
        unknown_fields=tuple(unknown),
        components=DegradationComponents(
            fee_expected=round(fee_exp, 6),
            fee_actual=None,
            slippage_cost_actual=None if slip_cost is None else round(slip_cost, 6),
            slippage_vs_arrival=None if slip_arrival is None else round(slip_arrival, 6),
            funding_expected=None if funding_exp is None else round(funding_exp, 6),
            funding_actual=None,
            replay_model_cost=None if replay_cost is None else round(float(replay_cost), 6),
            replay_funding=None if replay_funding is None else round(float(replay_funding), 6),
            trading_pnl_delta=None if trading_delta is None else round(trading_delta, 6),
            position_timing_effect=None if position_timing is None else round(position_timing, 6),
            residual=None if residual is None else round(residual, 9),
        ),
        daily=tuple(daily),
    )
