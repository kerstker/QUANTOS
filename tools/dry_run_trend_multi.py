"""第二个策略过同一条多期干跑流水线 —— 证明流水线是「策略无关的执行骨架」,不是只为 carry 写的。

信号 = 日线趋势 COMPOSITE(dist_from_high{7,14,30} + momentum7 的截面 z-score 均值,见 crypto_daily_composite),
读 data/cache/crypto_daily.db。逐周构造美元中性多空腿 → 经同一个 run_multi_period_dry_run(每周增量调仓单过真
预交易门 fail-closed → 干跑适配器纸面成交 → reconcile 价格 MTM)→ 净值曲线。与 carry 的关键差异:**无资金费现金流**
(cash_flow_fn=None)—— 趋势的边在价格本身,不在 funding。落 DryRunRun 到 dry_run.db(append,与 carry 共存)。
墙钟仅此边界;读本地库、无网络、无真单/真钱/真凭证;实盘 = 换适配器 + 人工批准 = 非目标。
"""
from __future__ import annotations

import json
import math
import os
import statistics

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research.signals import SIGNAL_OPS
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore

_DAILY_DB = os.path.join("data", "cache", "crypto_daily.db")
_SNAP = os.path.join("data", "cache", "crypto_research_snapshot.json")
_OUT = os.path.join("data", "cache", "dry_run.db")
_AUM = 1_000_000.0
_COMPOSITE = (("dist_from_high", 7), ("dist_from_high", 14), ("dist_from_high", 30), ("momentum", 7))
_Q = 0.2
_COST_BPS = 10.0  # representative liquid-crypto one-way (the daily tool swept 3–70bps; 10 is mid-realistic)


def _legs_and_prices(store, instruments, t):
    """PIT:用 ≤t 的收盘算 COMPOSITE(截面 z-score 均值)→ 美元中性多空腿 + 当期价。"""
    rows = []  # (inst, [sub values], price)
    for inst in instruments:
        closes = [b.close for b in sorted(store.as_of(inst, t), key=lambda b: b.event_time)]
        now_bar = store.latest_as_of(inst, t)
        if not closes or now_bar is None or now_bar.close <= 0:
            continue
        subs, ok = [], True
        for op, w in _COMPOSITE:
            s = SIGNAL_OPS[op](closes, w)
            if s is None or not math.isfinite(s):
                ok = False
                break
            subs.append(float(s))
        if ok:
            rows.append((inst, subs, now_bar.close))
    if len(rows) < 4:
        return {}, {}
    comp = {r[0]: 0.0 for r in rows}
    for k in range(len(_COMPOSITE)):
        vals = [r[1][k] for r in rows]
        mu, sd = statistics.fmean(vals), statistics.pstdev(vals)
        for r in rows:
            comp[r[0]] += ((r[1][k] - mu) / sd if sd > 0 else 0.0)
    comp = {i: v / len(_COMPOSITE) for i, v in comp.items()}
    order = sorted(comp, key=lambda i: comp[i])
    k = max(1, int(round(len(order) * _Q)))
    longs, shorts = order[-k:], order[:k]
    weights = {}
    for i in longs:
        weights[i] = 0.5 / len(longs)
    for i in shorts:
        weights[i] = weights.get(i, 0.0) - 0.5 / len(shorts)
    prices = {r[0]: r[2] for r in rows}
    return weights, prices


def main() -> None:
    snap = json.load(open(_SNAP, encoding="utf-8"))
    instruments = sorted(c["instrument_id"] for c in snap.get("meta", {}).get("coins", []))
    store = CachingPITStore(SqlitePITStore(_DAILY_DB, create=False))
    event_times = sorted({b.event_time for inst in instruments for b in store.fetch_bars(inst, 0, 10 ** 14)})
    weekly = event_times[::7]  # weekly rebalance

    periods, weights_by_period, prices_by_period = [], {}, {}
    for t in weekly:
        w, px = _legs_and_prices(store, instruments, t)
        if not w:
            continue
        periods.append(int(t))
        weights_by_period[int(t)] = {i: w[i] for i in w}
        prices_by_period[int(t)] = {i: px[i] for i in w if px.get(i)}

    held_names = sorted({n for w in weights_by_period.values() for n in w})

    ks = KillSwitch()
    limits = RiskLimits(
        allowed_instruments=frozenset(held_names), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10 ** 13, max_order_notional=1e14, fat_finger_qty=10 ** 13,
        max_gross_exposure=1e14, max_net_exposure=1e14, max_leverage=1e7, max_single_name_pct=1e7,
        max_turnover=1e7, max_participation_rate=1e7, max_instrument_vol=5.0, daily_loss_limit=1e14,
        max_drawdown=1.0)
    gate = PreTradeRiskGate(limits, kill_switch=ks)

    def ctx_factory(intent, prices, period):
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=_AUM * 10, equity=_AUM * 10, day_pnl=0.0,
                                     peak_equity=_AUM * 10, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                                 price_as_of=period, est_daily_volume=1e12, instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id="trend", approved=True, approval_id="research-dry-run"),
            now=period, max_staleness=10 ** 15)

    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period, gate=gate,
        ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id="trend", aum=_AUM,
        cost_bps=_COST_BPS, cash_flow_fn=None,  # trend = price edge, NO funding cash flow
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52})

    DryRunStore(_OUT, create=not os.path.exists(_OUT)).save_run(run)  # append alongside carry
    p = run.pnl
    print(f"=== TREND(日线 dist_from_high+momentum 复合)周度滚动干跑 · {len(instruments)} 币 · "
          f"{p['n_periods']} 周 · 成本 {_COST_BPS:.0f}bps ===", flush=True)
    print(f"DryRunRun {run.run_id}: 累计意图 {run.n_intents} · 门批准 {run.n_approved} · 门拒绝 {run.n_rejected}", flush=True)
    print(f"  总收益 {p['total_return']*100:.1f}% · 夏普(周→年) {p['sharpe']} · 最大回撤 {p['max_drawdown']*100:.0f}% "
          f"· 净敞口 {p['avg_net_exposure_pct']*100:.1f}% · 对账残差 {p['residual']}", flush=True)
    print(f"  (无资金费:cash_flow_fn=None;趋势的边在价格)· raw 曲线 {len(run.detail['equity_curve'])} 点 -> {_OUT}",
          flush=True)


if __name__ == "__main__":
    main()
