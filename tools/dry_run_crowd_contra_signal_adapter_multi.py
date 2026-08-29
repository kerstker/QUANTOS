"""对照实验驱动器(路线图 Rank #4,2026-07-10):把可部署 book(crowd_contra)的分仓方式从
tools/dry_run_crowd_contra_multi.py 的等权重 top/bottom-30% 分位桶(tools/crypto_oi_research.py::_legs)
换成已建好但从未真正用过的信号强度 + 成本感知分仓器(services/portfolio/signal_adapter.py::
signal_to_portfolio)——审计发现:组合层的均值方差优化器很成熟,但每个 sleeve 内部的个股分倉仍是
最原始的等权重分位桶,完全繞過了已建好的分倉機器。

**刻意做成獨立的新驅動器,不改動 `tools/dry_run_crowd_contra_multi.py` 本身**:
1. `_legs()`(tools/crypto_oi_research.py)是與 `tools/live_track_advance.py`(真實 Stage 1
   測試網/Demo 實盤驅動器,牽動真實資金)**共用**的分倉原語 —— 絕不能因為一個研究實驗就靜默
   改變真實交易的分倉邏輯,那需要獨立、明確的人工拍板,不是這次任務的範圍。
2. 這是一個「重新評估分倉方式是否真的更好」的實驗,不是既定改動——保留原驅動器作為零風險基準,
   兩者的 DryRunRun 並列存在 data/cache/dry_run.db(strategy_id 不同),可直接比較 net Sharpe/
   回撤,不影響任何既有回歸測試或正式部署 book。

信號構造(CROWD_CONTRA 表達式)、資金費現金流、擁擠度風控門(z-score BLOCK,用戶已簽署校準)、
真預交易門(fail-closed)全部與基準驅動器**完全相同**——唯一變數是分倉方法:`_legs()` 的等權重
top/bottom-30% 分位桶 換成 `signal_to_portfolio()` 對**全體截面**(不只 top/bottom 30%)做
winsorize→z-score→dollar-neutral 投影→position cap 的信號強度加權分倉。無網路、無真單/真錢/真憑證。

**誠實結論(首跑,2026-07-10,233 週實跑,對賬殘差 ~5e-9)——真實但混合的結果,非單邊改善**:
raw 曲線夏普 1.368(基準 1.090,+25%)、回撤 10.2%(基準 8.85%,略差);但**可部署的 vol-target
曲線**(驅動器自己標註的「實際會部署的風險管理形式」)夏普 1.220(基準 1.118,+9%)、回撤
**13.9%**(基準 9.71%,惡化 ~43%)。集中度確實變好(HHI 0.055 vs 0.071——用了全體截面而非只
top/bottom 30%)。兩者都同樣觸發 research_risk 的 market 引擎拒絕(vol 0.298 vs 0.307,同一個
「RiskConfig 預設值未針對週度數據校準」的既有已知問題,非本次改動引入的新風險)。**結論:
signal_to_portfolio 分倉在 vol-target 口徑下用夏普小幅提升換了明顯更差的回撤,不構成足夠證據
去動 `_legs()`/真實 Stage 1 驅動器——若要繼續這個方向,下一步是調參(position cap/winsorize_k)
而非直接採用当前默认参数。**
"""
from __future__ import annotations

import os
import time

from services.portfolio.crowding_gate import evaluate_crowding_gate, summarize_gate_decisions
from services.portfolio.signal_adapter import SignalAdapterConfig, signal_to_portfolio
from services.research import alpha_expression as ax
from services.risk.research_risk.dry_run_guard import evaluate_dry_run_risk
from services.risk.research_risk.domain.models import RiskConfig
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore
from live.execution.crowding_score import crowding_zscore as _crowding_zscore  # 共用纯实现,去重
from tools.crypto_oi_research import build_panel
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.domain_event_bus import open_domain_event_bus, save_and_project_dry_run
_WEEKLY_RISK_PROVENANCE = "RISK_CONFIG_WEEKLY_PPY_52_DURATION_UNCALIBRATED"
# _crowding_rank 是纯排名数学(零 _legs()/实盘耦合)——直接复用基准驱动器的实现,避免两份
# 逻辑漂移;两个驱动器的对照实验唯一变量应只是分仓方法本身。
from tools.dry_run_crowd_contra_multi import _crowding_rank

_OUT = os.path.join("data", "cache", "dry_run.db")
_AUM = 1_000_000.0
_COST_BPS = 10.0            # 与基准驱动器相同,保持成本口径可比
_WEEK_MS = 7 * 24 * 3600 * 1000
_STRATEGY_ID = "crowd_contra_signal_adapter"   # 独立 strategy_id,与基准 "crowd_contra" 并列存在

# signal_to_portfolio 配置:gross_target/dollar_neutral 与 _legs() 的 0.5多/0.5空 口径一致,
# max_position=0.1 与 _legs() 在 QUANTILE=0.3、~14-24 标的下的隐含平均腿仓位(0.5/4~0.5/7)量级相当,
# 是一个公平的对照起点(非精细调参——调参本身是若这个实验证明有效之后的下一步)。
_ADAPTER_CFG = SignalAdapterConfig(
    winsorize_k=3.0, max_position=0.1, gross_target=1.0, dollar_neutral=True,
    half_spread_bps=0.0, max_participation=0.1, target_aum=_AUM)

_MAX_CROWDING_Z = 3.0   # 与基准驱动器相同(用户 2026-07-07 签署的校准阈值)


def main() -> None:
    quarantine_legacy_backtest("tools.dry_run_crowd_contra_signal_adapter_multi")
    panel, instruments = build_panel()
    insts = panel.instruments
    inst_idx = {name: i for i, name in enumerate(insts)}
    close = panel.matrices["close"]
    fmat = panel.matrices["funding"]
    gmat = panel.matrices["global_ls"]
    ev = panel.event_times
    sig = ax.evaluate(CROWD_CONTRA, panel)

    rows, last = [], None
    for r in range(len(ev)):
        if last is None or ev[r] - last >= _WEEK_MS:
            rows.append(r)
            last = ev[r]

    periods, weights_by_period, prices_by_period, rate_by_period = [], {}, {}, {}
    gate_decisions, prev_weights = [], None
    for r in rows:
        # signal_to_portfolio consumes the WHOLE cross-section (not a pre-filtered top/bottom-30%
        # subset) — its own winsorize/z-score/dollar-neutral-projection determines sizing.
        signal = {insts[i]: sig[r][i] for i in range(len(insts)) if sig[r][i] is not None}
        if len(signal) < 4:   # mirrors _legs()'s own len(idx) < 4 -> {} fail-soft skip
            continue
        result = signal_to_portfolio(signal, config=_ADAPTER_CFG, prev_weights=prev_weights)
        target = result["weights"]
        if not target:
            continue
        t = int(ev[r])
        target_idx = {inst_idx[name]: w for name, w in target.items()}
        crowd = {insts[i]: rank for i, rank in _crowding_rank(gmat, r, list(target_idx)).items()}
        z_map = {name: z for name in target
                 if (z := _crowding_zscore(gmat, r, inst_idx[name])) is not None}
        blocked = False
        if crowd:
            decision = evaluate_crowding_gate(
                target, crowd, average_warn_only=True,
                crowding_z_by_symbol=z_map, max_crowding_z=_MAX_CROWDING_Z)
            gate_decisions.append(decision)
            blocked = decision.status == "BLOCK"
        periods.append(t)
        idx = set(target_idx)
        if blocked and prev_weights is not None:
            weights_by_period[t] = dict(prev_weights)
            idx |= {inst_idx[n] for n in prev_weights if n in inst_idx}
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
            strategy=StrategyApproval(strategy_id=_STRATEGY_ID, approved=True, approval_id="research-dry-run"),
            now=period, max_staleness=10 ** 15)

    event_bus = open_domain_event_bus()
    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period, gate=gate,
        ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id=_STRATEGY_ID, aum=_AUM,
        cost_bps=_COST_BPS, cash_flow_fn=cash_flow_fn,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52},
        event_bus=event_bus, publish_completion_event=False,
        research_risk_config=RiskConfig(periods_per_year=52))

    crowding = summarize_gate_decisions(gate_decisions)
    run.detail["crowding_gate"] = crowding
    vt = run.detail.get("equity_curve_vt") or run.detail["equity_curve"]
    risk = evaluate_dry_run_risk(
        [c["ret"] for c in vt],
        weights_by_period.get(periods[-1], {}),
        [c["equity"] for c in vt],
        timestamps=periods,
        config=RiskConfig(periods_per_year=52),
    )
    risk["series"] = "vol_target"
    risk["config_provenance"] = _WEEKLY_RISK_PROVENANCE
    run.detail["research_risk"] = risk
    save_and_project_dry_run(
        DryRunStore(_OUT, create=True), run, event_bus,
        now_ms=max(int(run.timestamp), int(time.time() * 1000)),
        project=False,
    )
    p = run.pnl
    print(f"  拥挤门: {crowding['n_periods']} 周 · PASS {crowding['n_pass']}/WARN {crowding['n_warn']}"
          f"/BLOCK {crowding['n_block']} · 最差分 {crowding['worst_score']}", flush=True)
    print(f"  研究风控门(4真引擎/5诚实跳过): {risk['decision']} · 过 {risk['gates_passed']}/{risk['gates_run']} "
          f"· {[k for k,v in risk['engines_run'].items() if not v] or '全过'}", flush=True)
    print(f"=== CROWD_CONTRA(signal_adapter 分仓对照) · OI 大币 {len(instruments)} · {p['n_periods']} 周 "
          f"· 成本 {_COST_BPS:.0f}bps ===", flush=True)
    print(f"DryRunRun {run.run_id}: 累计意图 {run.n_intents} · 门批准 {run.n_approved} · 门拒绝 {run.n_rejected}", flush=True)
    print(f"  总收益 {p['total_return']*100:.1f}% · 夏普(周→年) {p['sharpe']} · 最大回撤 {p['max_drawdown']*100:.0f}% "
          f"· 累计资金费 ${p['total_funding']:,.0f} · 对账残差 {p['residual']}", flush=True)
    print(f"  raw 曲线 {len(run.detail['equity_curve'])} 点 · vol-target 曲线 {len(run.detail['equity_curve_vt'])} 点 "
          f"-> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
