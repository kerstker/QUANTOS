"""端到端干跑驱动器:把 carry 组合(最新一期 COMPOSITE 信号的多空腿 × vol-target 杠杆)从信号一路跑到
纸面成交 + 平均成本对账 + 哈希链审计,落库一次可复现的 DryRunRun。

这是把 services/execution/dry_run_pipeline 串起来的"系统级"入口:它经过真正的预交易门(运行时 chokepoint,
fail-closed)+ kill-switch + 干跑适配器(纸面、绝不真发)。墙钟/seed 只在此边界。读本地库、无网络、无真单/真钱、
无真凭证;装配真实 RiskContext(五个 LIVE 标志由 SafetyConfig() 恒 false)。实盘 = 同接口换真券商适配器 +
人工批准 = 非目标。
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time

from quantos.contracts.config import SafetyConfig
from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.dry_run_pipeline import run_dry_run_pipeline
from services.execution.dry_run_store import DryRunStore
from tools.crypto_carry_strategy import COMPOSITE, QUANTILE, _legs
from tools.crypto_carry_stress import _run_carry, _slip_bps
from tools.crypto_carry_voltarget import LEV_CAP, LOOKBACK, TARGET_ANN_VOL

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND_CACHE = os.path.join("data", "cache", "crypto_funding_rates.json")
_OUT = os.path.join("data", "cache", "dry_run.db")
_AUM = 1_000_000.0
_INITIAL_CAPITAL = 1_000_000.0


def _vol_target_leverage(net) -> float:
    """最新一期的 vol-target 杠杆标量(同 crypto_carry_voltarget 公式):min(cap, 目标日波动/尾随实现波动)。"""
    if len(net) < LOOKBACK + 1:
        return 1.0
    target_daily = TARGET_ANN_VOL / math.sqrt(252)
    sd = statistics.pstdev(net[-LOOKBACK:]) or 1e-9
    return min(LEV_CAP, target_daily / sd)


def main() -> None:
    instruments = sorted(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    funding = {tk: {int(k): v for k, v in d.items()}
               for tk, d in json.load(open(_FUND_CACHE, encoding="utf-8")).items()}

    # 流动性分层(同 carry 验证):取流动半区作为可部署宇宙
    liq = {}
    for inst in instruments:
        dv = [b.close * b.volume for b in store.fetch_bars(inst, -10 ** 18, 10 ** 18)
              if b.close > 0 and b.volume > 0]
        liq[inst] = statistics.median(dv) if dv else 0.0
    ranked = sorted(instruments, key=lambda x: liq[x])
    pctile = {inst: (i + 1) / len(ranked) for i, inst in enumerate(ranked)}
    slip = {inst: _slip_bps(pctile[inst]) for inst in instruments}
    liquid = [inst for inst in instruments if pctile[inst] >= 0.5]

    now_ms = int(time.time() * 1000)  # 墙钟仅此边界

    # 最新一期 carry 多空腿 × vol-target 杠杆 -> 目标权重
    ets = sorted({b.event_time for inst in liquid for b in store.fetch_bars(inst, -10 ** 18, 10 ** 18)})
    panel = ax.build_field_panel(store, liquid, ets, ("close", "volume"))
    panel.matrices["funding"] = [[funding.get(i, {}).get(t) for i in panel.instruments]
                                 for t in panel.event_times]
    sig = ax.evaluate(COMPOSITE, panel)
    insts, close = panel.instruments, panel.matrices["close"]
    legs = _legs(sig[-1], QUANTILE) or _legs(sig[-2], QUANTILE)
    net, _times = _run_carry(store, funding, liquid, slip, now_ms)
    lev = _vol_target_leverage(net)
    weights = {insts[i]: lev * w for i, w in legs.items()}
    prices = {insts[i]: close[-1][i] for i in legs if close[-1][i] and close[-1][i] > 0}
    held = [k for k in weights if k in prices]
    cost_bps = statistics.fmean([slip[k] for k in held]) if held else 0.0

    # 真实 RiskContext 装配(LIVE 全 false;门会用自己的 kill-switch/safety 覆盖 ctx 的)
    ks = KillSwitch()
    limits = RiskLimits(
        allowed_instruments=frozenset(held), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10 ** 12, max_order_notional=1e13, fat_finger_qty=10 ** 12,
        max_gross_exposure=1e13, max_net_exposure=1e13, max_leverage=1e6, max_single_name_pct=1e6,
        max_turnover=1e6, max_participation_rate=1e6, max_instrument_vol=5.0, daily_loss_limit=1e13,
        max_drawdown=1.0)
    gate = PreTradeRiskGate(limits, kill_switch=ks)

    def ctx_factory(intent):
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=_AUM * 5, equity=_AUM * 5, day_pnl=0.0,
                                     peak_equity=_AUM * 5, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=prices.get(intent.instrument_id, 1.0), market_open=True,
                                 price_as_of=now_ms, est_daily_volume=1e12, instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id="carry", approved=True, approval_id="research-dry-run"),
            now=now_ms, max_staleness=10 ** 15)

    run = run_dry_run_pipeline(
        weights=weights, prices=prices, current_positions={}, aum=_AUM, gate=gate,
        ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id="carry", timestamp=now_ms,
        marks=prices, initial_capital=_INITIAL_CAPITAL, cost_bps=cost_bps)

    if os.path.exists(_OUT):
        os.remove(_OUT)
    DryRunStore(_OUT, create=True).save_run(run)

    print(f"=== CARRY 端到端干跑 · 流动 {len(liquid)} 币 · vol-target 杠杆 {lev:.2f}x · 成本 {cost_bps:.1f}bps ===",
          flush=True)
    print(f"DryRunRun {run.run_id}: 意图 {run.n_intents} · 门批准 {run.n_approved} · 门拒绝 {run.n_rejected} · "
          f"纸面成交 {run.detail.get('n_fills')}", flush=True)
    print(f"  PnL {run.pnl} · 审计根 {run.audit_root[:16]} -> {_OUT}", flush=True)
    if run.rejected_reasons:
        print(f"  门拒绝原因(前 10): {run.rejected_reasons[:10]}", flush=True)


if __name__ == "__main__":
    main()
