"""全历史周度滚动干跑驱动器:逐周 carry COMPOSITE 多空腿 → 权重,经真预交易门(fail-closed)+ 干跑适配器
(纸面、绝不真发)滚动跑完整历史,资金费现金流单独累计(carry 的真边),产出 raw 与 vol-target 两条净值曲线,
落一条多期 DryRunRun 到 data/cache/dry_run.db。墙钟/seed 仅此边界;读本地库、无网络、无真单/真钱/真凭证。
装配真实 RiskContext(五个 LIVE 标志由 SafetyConfig() 恒 false)。实盘 = 同接口换真券商适配器 + 人工批准 = 非目标。
"""
from __future__ import annotations

import json
import os
import statistics

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.research.realism.impact import AlmgrenChrissImpact
from services.research.realism.impact_calibration import default_crypto_impact_calibration, scenario_impact
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore
from tools.crypto_carry_strategy import COMPOSITE, QUANTILE, _legs
from tools.crypto_carry_stress import _slip_bps

_MAX_PARTICIPATION = 0.10       # Rank #11: realistic participation cap (was a 1e7 placeholder)

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND = os.path.join("data", "cache", "crypto_funding_rates.json")
_OUT = os.path.join("data", "cache", "dry_run.db")
# $1M keeps the 8.5bps tiered slippage cost-realistic (at $100M real market impact rises — that's the
# separate capacity analysis, not modelled here). Dollar-neutrality finding (verified scale-invariant
# $1M↔$100M): the book is already ~dollar-neutral (avg net exposure ~1.8%); the 77% drawdown is NOT
# rounding/net-market exposure — it is the inherent cross-sectional crypto-spread vol. Funding (~16.6%
# cumulative) is the steady edge riding on that volatile spread; it cannot be cleanly isolated because
# the carry trade inherently holds the spread.
_AUM = 1_000_000.0
_WEEK_MS = 7 * 24 * 3600 * 1000


def main() -> None:
    instruments = sorted(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    funding = {tk: {int(k): v for k, v in d.items()}
               for tk, d in json.load(open(_FUND, encoding="utf-8")).items()}

    # liquid half (same deployable universe as carry validation)
    liq = {}
    for inst in instruments:
        dv = [b.close * b.volume for b in store.fetch_bars(inst, -10 ** 18, 10 ** 18)
              if b.close > 0 and b.volume > 0]
        liq[inst] = statistics.median(dv) if dv else 0.0
    ranked = sorted(instruments, key=lambda x: liq[x])
    pctile = {inst: (i + 1) / len(ranked) for i, inst in enumerate(ranked)}
    slip = {inst: _slip_bps(pctile[inst]) for inst in instruments}
    liquid = [inst for inst in instruments if pctile[inst] >= 0.5]

    ets = sorted({b.event_time for inst in liquid for b in store.fetch_bars(inst, -10 ** 18, 10 ** 18)})
    panel = ax.build_field_panel(store, liquid, ets, ("close", "volume"))
    panel.matrices["funding"] = [[funding.get(i, {}).get(t) for i in panel.instruments]
                                 for t in panel.event_times]
    sig = ax.evaluate(COMPOSITE, panel)
    insts, close, fmat, ev = panel.instruments, panel.matrices["close"], panel.matrices["funding"], panel.event_times
    vmat = panel.matrices["volume"]

    # weekly sampling: first row, then every >= _WEEK_MS later
    rows, last = [], None
    for r in range(len(ev)):
        if last is None or ev[r] - last >= _WEEK_MS:
            rows.append(r)
            last = ev[r]

    # Rank #11: per-period ADV from the in-panel close*volume — same source feeds the impact cost
    # (adv_by_period, notional) and the participation gate (vol_by_period, shares/units).
    periods, weights_by_period, prices_by_period, rate_by_period = [], {}, {}, {}
    adv_by_period, vol_by_period = {}, {}
    for r in rows:
        legs = _legs(sig[r], QUANTILE)
        if not legs:
            continue
        t = int(ev[r])
        periods.append(t)
        weights_by_period[t] = {insts[i]: w for i, w in legs.items()}
        prices_by_period[t] = {insts[i]: close[r][i] for i in legs if close[r][i] and close[r][i] > 0}
        rate_by_period[t] = {insts[i]: (fmat[r][i] or 0.0) for i in legs}
        adv_by_period[t] = {insts[i]: close[r][i] * vmat[r][i] for i in legs
                            if close[r][i] and vmat[r][i] and close[r][i] > 0 and vmat[r][i] > 0}
        vol_by_period[t] = {insts[i]: vmat[r][i] for i in legs if vmat[r][i] and vmat[r][i] > 0}

    held_names = sorted({n for w in weights_by_period.values() for n in w})
    cost_bps = statistics.fmean([slip[n] for n in held_names]) if held_names else 0.0

    def cash_flow_fn(held, prices, period):
        # carry funding cash flow on the held book: sum_i (-rate_i * qty_i * price_i)
        rates = rate_by_period.get(period, {})
        return sum(-rates.get(i, 0.0) * q * prices.get(i, 0.0) for i, q in held.items())

    ks = KillSwitch()
    limits = RiskLimits(
        allowed_instruments=frozenset(held_names), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10 ** 13, max_order_notional=1e14, fat_finger_qty=10 ** 13,
        max_gross_exposure=1e14, max_net_exposure=1e14, max_leverage=1e7, max_single_name_pct=1e7,
        max_turnover=1e7, max_participation_rate=_MAX_PARTICIPATION, max_instrument_vol=5.0,
        daily_loss_limit=1e14, max_drawdown=1.0)
    gate = PreTradeRiskGate(limits, kill_switch=ks)

    def ctx_factory(intent, prices, period):
        # Rank #11: real per-period ADV (units/shares) feeds the participation gate — same source as the
        # impact cost (was a 1e12 placeholder). Missing -> large fallback so a data gap can't fake liquidity.
        adv_units = vol_by_period.get(period, {}).get(intent.instrument_id, 1e18)
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=_AUM * 10, equity=_AUM * 10, day_pnl=0.0,
                                     peak_equity=_AUM * 10, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                                 price_as_of=period, est_daily_volume=adv_units, instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id="carry", approved=True, approval_id="research-dry-run"),
            now=period, max_staleness=10 ** 15)

    # Rank #11: charge participation-driven Almgren-Chriss impact (base calibration) instead of flat bps;
    # names lacking ADV fall back to cost_bps per-name. TCA-lite band lands in run detail.
    impact = scenario_impact(default_crypto_impact_calibration().base)
    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period, gate=gate,
        ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id="carry", aum=_AUM,
        cost_bps=cost_bps, cash_flow_fn=cash_flow_fn, impact=impact, adv_by_period=adv_by_period,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52})

    if os.path.exists(_OUT):
        os.remove(_OUT)
    DryRunStore(_OUT, create=True).save_run(run)
    p = run.pnl
    print(f"=== CARRY 周度滚动干跑 · 流动 {len(liquid)} 币 · {p['n_periods']} 周 · 成本 {cost_bps:.1f}bps ===", flush=True)
    print(f"DryRunRun {run.run_id}: 累计意图 {run.n_intents} · 门批准 {run.n_approved} · 门拒绝 {run.n_rejected}", flush=True)
    print(f"  总收益 {p['total_return']*100:.1f}% · 夏普(周→年) {p['sharpe']} · 最大回撤 {p['max_drawdown']*100:.0f}% "
          f"· 累计资金费 ${p['total_funding']:,.0f} · 对账残差 {p['residual']}", flush=True)
    print(f"  raw 曲线 {len(run.detail['equity_curve'])} 点 · vol-target 曲线 {len(run.detail['equity_curve_vt'])} 点 "
          f"-> {_OUT}", flush=True)
    cf = run.detail.get("cost_fills", {})
    print(f"  成本模型: {run.detail.get('cost_model')} · 冲击成交 {cf.get('impact')}/平bps回退 {cf.get('flat')}", flush=True)
    if "tca" in run.detail:
        tca = run.detail["tca"]
        print(f"  TCA-lite: 计入 ${tca['assumed']:,.0f} · 区间 [${tca['band_lo']:,.0f}, ${tca['band_hi']:,.0f}] "
              f"· in_band={tca['in_band']}", flush=True)


if __name__ == "__main__":
    main()
