"""统一执行 book:把 carry 与 crowd_contra 合成**一个** book 过同一条干跑流水线(不是收益流相加,是真的一个
book:每周权重 = 0.5·carry_legs + 0.5·crowd_legs,在同一 carry 面板/网格上),拿到组合的**净-成本**真实业绩 +
容量。carry 覆盖 ~58 流动币,crowd 覆盖有 OI global_ls 的大币子集;并集上 blend。经真预交易门(fail-closed)+ 干跑
适配器纸面成交(绝不真发)→ reconcile(价格 MTM + 资金费)→ 净值曲线,追加一条 strategy_id=carry_crowd_combo 的
DryRunRun 到 dry_run.db(与 carry/trend/crowd_contra 并列)。墙钟仅此边界;读本地库、无网络、无真单/真钱/真凭证。

诚实:crowd 腿只在有 OI 数据的大币(其余 blend=0.5·carry);是 carry 面板上的组合 book,与独立 crowd(纯14大币)
不完全相同。分散故事的收益流层面结论见 combo_book_research;这里给它执行现实(整手+门+成本)。实盘=非目标。
"""
from __future__ import annotations

import json
import os

from services.research import alpha_expression as ax
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore
from tools.capacity_research import _carry_panel, _weekly_rows
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE, QUANTILE as CARRY_Q, _legs as carry_legs
from tools.crypto_oi_research import _legs as oi_legs, QUANTILE as OI_Q
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.domain_event_bus import open_domain_event_bus, save_and_project_dry_run
from tools.research_dry_run_profile import research_dry_run_limits

_OUT = os.path.join("data", "cache", "dry_run.db")
_AUM = 1_000_000.0
_COST_BPS = 9.0            # carry~8.5 / crowd~10 之间
_CACHE = os.path.join("data", "cache")


def _add_global_ls(panel):
    """给 carry 面板加 OI global_ls 矩阵(有 OI 的大币有值,其余 None),供 CROWD_CONTRA 评估。照抄 registry 配方。"""
    oi_cache = json.load(open(os.path.join(_CACHE, "crypto_oi_metrics.json"), encoding="utf-8"))
    oi_data = oi_cache.get("data", oi_cache)
    oi_by_inst = {tk: {int(k): v for k, v in d.items()} for tk, d in oi_data.items() if not tk.startswith("_")}
    panel.matrices["global_ls"] = [[(oi_by_inst.get(i, {}).get(t) or {}).get("global_ls")
                                    for i in panel.instruments] for t in panel.event_times]
    return panel


def main() -> None:
    quarantine_legacy_backtest("tools.dry_run_combo_multi")
    panel = _add_global_ls(_carry_panel())
    insts, close, fmat, ev = panel.instruments, panel.matrices["close"], panel.matrices["funding"], panel.event_times
    carry_sig = ax.evaluate(CARRY_COMPOSITE, panel)
    crowd_sig = ax.evaluate(CROWD_CONTRA, panel)

    periods, weights_by_period, prices_by_period, rate_by_period = [], {}, {}, {}
    for r in _weekly_rows(ev):
        cl = carry_legs(carry_sig[r], CARRY_Q)
        ol = oi_legs(crowd_sig[r], OI_Q)
        if not cl and not ol:
            continue
        combined = {}
        for i, w in cl.items():
            combined[i] = combined.get(i, 0.0) + 0.5 * w
        for i, w in ol.items():
            combined[i] = combined.get(i, 0.0) + 0.5 * w
        combined = {i: w for i, w in combined.items() if abs(w) > 1e-12}
        if not combined:
            continue
        t = int(ev[r])
        periods.append(t)
        weights_by_period[t] = {insts[i]: w for i, w in combined.items()}
        prices_by_period[t] = {insts[i]: close[r][i] for i in combined if close[r][i] and close[r][i] > 0}
        rate_by_period[t] = {insts[i]: (fmat[r][i] or 0.0) for i in combined}

    held_names = sorted({n for w in weights_by_period.values() for n in w})

    def cash_flow_fn(held, prices, period):
        rates = rate_by_period.get(period, {})
        return sum(-rates.get(i, 0.0) * q * prices.get(i, 0.0) for i, q in held.items())

    ks = KillSwitch()
    limits = research_dry_run_limits(frozenset(held_names))
    gate = PreTradeRiskGate(limits, kill_switch=ks)

    def ctx_factory(intent, prices, period):
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=_AUM * 10, equity=_AUM * 10, day_pnl=0.0,
                                     peak_equity=_AUM * 10, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                                 price_as_of=period, est_daily_volume=1e12, instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id="carry_crowd_combo", approved=True, approval_id="research-dry-run"),
            now=period, max_staleness=10 ** 15)

    event_bus = open_domain_event_bus()
    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period, gate=gate,
        ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id="carry_crowd_combo", aum=_AUM,
        cost_bps=_COST_BPS, cash_flow_fn=cash_flow_fn,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52},
        event_bus=event_bus, publish_completion_event=False)

    save_and_project_dry_run(
        DryRunStore(_OUT, create=True), run, event_bus, now_ms=int(run.timestamp)
    )
    p = run.pnl
    print(f"=== CARRY+CROWD 组合统一 book 周度滚动干跑 · {len(held_names)} 币 · {p['n_periods']} 周 · 成本 {_COST_BPS:.0f}bps ===", flush=True)
    print(f"DryRunRun {run.run_id}: 累计意图 {run.n_intents} · 门批准 {run.n_approved} · 门拒绝 {run.n_rejected}", flush=True)
    print(f"  净收益 {p['total_return']*100:.1f}% · 夏普(周→年) {p['sharpe']} · 最大回撤 {p['max_drawdown']*100:.0f}% "
          f"· 累计资金费 ${p['total_funding']:,.0f} · 对账残差 {p['residual']}", flush=True)
    print(f"  raw 曲线 {len(run.detail['equity_curve'])} 点 · vol-target 曲线 {len(run.detail['equity_curve_vt'])} 点 -> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
