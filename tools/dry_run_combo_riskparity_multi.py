"""风险平衡多策略统一执行 book(Rank #1 修补):把 carry 与 crowd_contra 合成**一个**book,但每周按**风险平衡**
(rolling 逆波动、PIT trailing 窗口)分配两个 sleeve 的权重 —— 不是 naive 权重 blend(那被 carry 按币数主导、
只到 VT 1.20)。每周 w = s_carry·carry_legs + s_crowd·crowd_legs,s 由**仅过去**的 sleeve 逐周收益逆波动定。
过同一条干跑流水线(门+净成本+资金费+vol-target),第 5 条策略 carry_crowd_risk_parity 落 dry_run.db。

目标:把收益流层面已验证的分散(corr -0.04 → Sharpe 1.95/回撤8%)在**净-成本单一执行 book** 里兑现。
诚实:sleeve 风险权重用 trailing 窗口(无前视);crowd 腿只在有 OI 的大币;数字为准——分散可能兑现也可能被
整手/成本/单一 book 现实吃掉部分。纯离线、无网络、无真单;实盘=非目标。
"""
from __future__ import annotations

import os

from services.research import alpha_expression as ax
from services.research.book_combine import rolling_inverse_vol_weights
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore
from tools.capacity_research import _carry_panel, _weekly_rows
from tools.dry_run_combo_multi import _add_global_ls
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE, QUANTILE as CARRY_Q, _legs as carry_legs_fn
from tools.crypto_oi_research import _legs as oi_legs_fn, QUANTILE as OI_Q
from tools.crypto_oi_verify import CROWD_CONTRA

_OUT = os.path.join("data", "cache", "dry_run.db")
_AUM = 1_000_000.0
_COST_BPS = 9.0
_LOOKBACK = 8          # trailing 周数用于 sleeve 逆波动
_MIN_HIST = 4


def _sleeve_return(legs, r, rn, close, fmat, idx_of):
    """一个 sleeve 在 [r, rn) 的 gross 收益:Σ w_i·(close[rn]/close[r]-1) + 资金费(-rate·w)。legs=index→w。"""
    g = 0.0
    for i, w in legs.items():
        p0, p1 = close[r][i], close[rn][i]
        if p0 and p1 and p0 > 0:
            g += w * (p1 / p0 - 1.0)
        fr = fmat[r][i]
        if fr is not None:
            g += -fr * w
    return g


def main() -> None:
    panel = _add_global_ls(_carry_panel())
    insts, close, fmat, ev = panel.instruments, panel.matrices["close"], panel.matrices["funding"], panel.event_times
    carry_sig = ax.evaluate(CARRY_COMPOSITE, panel)
    crowd_sig = ax.evaluate(CROWD_CONTRA, panel)

    # 逐周(有 forward 的行)预算 carry/crowd 各自 legs + sleeve 逐周 gross 收益(供 PIT 风险权重)
    rows = _weekly_rows(ev)
    seq = []  # (r, carry_legs, crowd_legs)
    for r in rows:
        cl = carry_legs_fn(carry_sig[r], CARRY_Q)
        ol = oi_legs_fn(crowd_sig[r], OI_Q)
        if cl or ol:
            seq.append((r, cl, ol))
    carry_sret, crowd_sret = [], []
    for k in range(len(seq) - 1):
        r, cl, ol = seq[k]
        rn = seq[k + 1][0]
        carry_sret.append(_sleeve_return(cl, r, rn, close, fmat, None))
        crowd_sret.append(_sleeve_return(ol, r, rn, close, fmat, None))

    periods, weights_by_period, prices_by_period, rate_by_period = [], {}, {}, {}
    sleeve_wlog = []
    for k in range(len(seq)):
        r, cl, ol = seq[k]
        # PIT 风险平衡:仅用 [0,k) 的 sleeve 收益定权重(k=0 或历史不足 → 等权)
        s = rolling_inverse_vol_weights([carry_sret, crowd_sret], end_idx=k, lookback=_LOOKBACK, min_history=_MIN_HIST)
        sc, sq = s[0], s[1]
        sleeve_wlog.append((sc, sq))
        combined = {}
        for i, w in cl.items():
            combined[i] = combined.get(i, 0.0) + sc * w
        for i, w in ol.items():
            combined[i] = combined.get(i, 0.0) + sq * w
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
            strategy=StrategyApproval(strategy_id="carry_crowd_risk_parity", approved=True, approval_id="research-dry-run"),
            now=period, max_staleness=10 ** 15)

    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period, gate=gate,
        ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id="carry_crowd_risk_parity", aum=_AUM,
        cost_bps=_COST_BPS, cash_flow_fn=cash_flow_fn,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52})

    DryRunStore(_OUT, create=not os.path.exists(_OUT)).save_run(run)   # 第 5 条策略,与前 4 并列
    p = run.pnl
    avg_sc = sum(a for a, _ in sleeve_wlog) / len(sleeve_wlog) if sleeve_wlog else 0.0
    avg_sq = sum(b for _, b in sleeve_wlog) / len(sleeve_wlog) if sleeve_wlog else 0.0
    print(f"=== CARRY+CROWD 风险平衡统一 book · {len(held_names)} 币 · {p['n_periods']} 周 · 成本 {_COST_BPS:.0f}bps ===", flush=True)
    print(f"  平均 sleeve 权重: carry {avg_sc:.2f} / crowd {avg_sq:.2f}(rolling 逆波动,PIT)", flush=True)
    print(f"DryRunRun {run.run_id}: 意图 {run.n_intents} · 门批准 {run.n_approved} · 拒绝 {run.n_rejected}", flush=True)
    print(f"  RAW 净收益 {p['total_return']*100:.1f}% · 夏普(RAW) {p['sharpe']} · 回撤(RAW) {p['max_drawdown']*100:.0f}% "
          f"· 资金费 ${p['total_funding']:,.0f} · 残差 {p['residual']}", flush=True)


if __name__ == "__main__":
    main()
