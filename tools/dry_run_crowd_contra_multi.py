"""全历史周度滚动干跑驱动器:crowd_contra = cs_rank(neg(global_ls))(反着做拥挤的散户多空仓位)逐周截面多空腿
→ 权重,经真预交易门(fail-closed)+ 干跑适配器(纸面、绝不真发)滚动跑完整历史,资金费现金流单独累计(持永续
的真实现金流),产出 raw 与 vol-target 两条净值曲线,追加一条 DryRunRun 到 data/cache/dry_run.db(与 carry/trend
共存)。墙钟/seed 仅此边界;读本地库、无网络、无真单/真钱/真凭证。crowd_contra 已过 8 项证伪(4/5 机器检验),
这是它从未经过的执行现实检验——证伪已过 ≠ 执行现实必过。实盘 = 同接口换真券商适配器 + 人工批准 = 非目标。
"""
from __future__ import annotations

import os

from services.portfolio.crowding_gate import evaluate_crowding_gate, summarize_gate_decisions
from services.research import alpha_expression as ax
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore
from tools.crypto_oi_research import build_panel, _legs, QUANTILE
from tools.crypto_oi_verify import CROWD_CONTRA

_OUT = os.path.join("data", "cache", "dry_run.db")
_AUM = 1_000_000.0
_COST_BPS = 10.0            # 平 10bps:大币偏保守,对上证伪里 net@10bps 扛过那条证据
_WEEK_MS = 7 * 24 * 3600 * 1000


def _crowding_rank(gmat, r: int, idx, lookback: int = 4) -> dict:
    """Rank #10a crowding evidence: trailing-window mean global_ls per instrument, cross-sectionally
    ranked to [0,1] (higher = more crowded-long). Scale-free, so the gate's thresholds apply."""
    means = {}
    for i in idx:
        window = [gmat[rr][i] for rr in range(max(0, r - lookback + 1), r + 1) if gmat[rr][i] is not None]
        if window:
            means[i] = sum(window) / len(window)
    if len(means) < 2:
        return {}
    ordered = sorted(means, key=lambda i: means[i])
    return {i: k / (len(ordered) - 1) for k, i in enumerate(ordered)}


def main() -> None:
    panel, instruments = build_panel()
    insts = panel.instruments
    close = panel.matrices["close"]
    fmat = panel.matrices["funding"]
    gmat = panel.matrices["global_ls"]
    ev = panel.event_times
    sig = ax.evaluate(CROWD_CONTRA, panel)

    # weekly sampling: first row, then every >= _WEEK_MS later
    rows, last = [], None
    for r in range(len(ev)):
        if last is None or ev[r] - last >= _WEEK_MS:
            rows.append(r)
            last = ev[r]

    # Rank #10a: the crowding gate is now LOAD-BEARING on this book — evaluated every week from real
    # global_ls evidence, recorded in run detail, and (on a genuine concentration/invalid BLOCK) it
    # skips that week's rebalance (carry prior holdings). crowd_contra is dollar-neutral + diffuse, so
    # by construction it lands in WARN (crowded names ARE the edge) and never over-concentrates — the
    # gate documents the crowding profile without vetoing the thesis; a concentrated book WOULD block.
    periods, weights_by_period, prices_by_period, rate_by_period = [], {}, {}, {}
    gate_decisions, prev_weights = [], None
    for r in rows:
        legs = _legs(sig[r], QUANTILE)
        if not legs:
            continue
        t = int(ev[r])
        target = {insts[i]: w for i, w in legs.items()}
        crowd = {insts[i]: rank for i, rank in _crowding_rank(gmat, r, list(legs)).items()}
        blocked = False
        if crowd:
            decision = evaluate_crowding_gate(target, crowd)
            gate_decisions.append(decision)
            blocked = decision.status == "BLOCK"
        periods.append(t)
        # legs-only prices/rates preserve the exact historical funding semantics (only the current
        # book's names accrue funding); the BLOCK path additionally prices the carried names so a
        # skipped rebalance is still MTM/funded correctly. BLOCK never fires for this diffuse book,
        # so non-block weeks are byte-identical to the pre-wiring run (regression-safe).
        idx = set(legs)
        if blocked and prev_weights is not None:
            weights_by_period[t] = dict(prev_weights)        # skip rebalance: carry prior book
            idx |= {i for i in range(len(insts)) if insts[i] in prev_weights}
        else:
            weights_by_period[t] = target
            prev_weights = target
        prices_by_period[t] = {insts[i]: close[r][i] for i in idx if close[r][i] and close[r][i] > 0}
        rate_by_period[t] = {insts[i]: (fmat[r][i] or 0.0) for i in idx}

    held_names = sorted({n for w in weights_by_period.values() for n in w})

    def cash_flow_fn(held, prices, period):
        rates = rate_by_period.get(period, {})
        return sum(-rates.get(i, 0.0) * q * prices.get(i, 0.0) for i, q in held.items())

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
            strategy=StrategyApproval(strategy_id="crowd_contra", approved=True, approval_id="research-dry-run"),
            now=period, max_staleness=10 ** 15)

    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period, gate=gate,
        ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id="crowd_contra", aum=_AUM,
        cost_bps=_COST_BPS, cash_flow_fn=cash_flow_fn,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52})

    # Rank #10a: surface the crowding-gate profile read-only in the run detail (-> console dry-run page)
    crowding = summarize_gate_decisions(gate_decisions)
    run.detail["crowding_gate"] = crowding
    DryRunStore(_OUT, create=not os.path.exists(_OUT)).save_run(run)   # append alongside carry + trend
    p = run.pnl
    print(f"  拥挤门: {crowding['n_periods']} 周 · PASS {crowding['n_pass']}/WARN {crowding['n_warn']}"
          f"/BLOCK {crowding['n_block']} · 最差分 {crowding['worst_score']}", flush=True)
    print(f"=== CROWD_CONTRA 周度滚动干跑 · OI 大币 {len(instruments)} · {p['n_periods']} 周 · 成本 {_COST_BPS:.0f}bps ===", flush=True)
    print(f"DryRunRun {run.run_id}: 累计意图 {run.n_intents} · 门批准 {run.n_approved} · 门拒绝 {run.n_rejected}", flush=True)
    print(f"  总收益 {p['total_return']*100:.1f}% · 夏普(周→年) {p['sharpe']} · 最大回撤 {p['max_drawdown']*100:.0f}% "
          f"· 累计资金费 ${p['total_funding']:,.0f} · 对账残差 {p['residual']}", flush=True)
    print(f"  raw 曲线 {len(run.detail['equity_curve'])} 点 · vol-target 曲线 {len(run.detail['equity_curve_vt'])} 点 "
          f"-> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
