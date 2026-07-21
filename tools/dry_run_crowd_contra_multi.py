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
from services.risk.research_risk.dry_run_guard import evaluate_dry_run_risk
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (AccountRiskState, MarketContext, RiskContext,
                                             StrategyApproval)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore
from tools.cache_io import atomic_write_json
from live.execution.crowding_score import crowding_zscore as _crowding_zscore  # 共用纯实现,去重
from tools.crypto_oi_research import build_panel, _legs, QUANTILE
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.domain_event_bus import open_domain_event_bus, save_and_project_dry_run

_OUT = os.path.join("data", "cache", "dry_run.db")
_REPORT = os.path.join("data", "cache", "crowd_contra_dry_run.json")
_AUM = 1_000_000.0
_COST_BPS = 10.0            # 平 10bps:大币偏保守,对上证伪里 net@10bps 扛过那条证据
_WEEK_MS = 7 * 24 * 3600 * 1000


# 绝对拥挤 z-score BLOCK 阈值(**用户 2026-07-07 签署**,校准见 docs/engineering/specs/
# 2026-07-07-crowding-gate-calibration.md):被 short 名字的 global_ls 时序 z > 3.0 -> BLOCK(挤压风险)。
# 实证:short-leg MAX z 在 -0.5..5.87 变化,p90≈2.9,z*=3.0 flag 最拥挤的 ~10% 周。
_MAX_CROWDING_Z = 3.0


def _existing_crowd_run(path: str = _OUT) -> dict | None:
    """Return the append-only crowd run when this driver has already completed.

    ``save_and_project_dry_run`` correctly rejects binding a second research-risk
    event to the same deterministic run.  The operator flow is intentionally
    retryable, so an existing record is a successful idempotent result rather
    than a reason to recompute and collide with the outbox.
    """
    if not os.path.isfile(path):
        return None
    store = None
    try:
        store = DryRunStore(path, create=False)
        records = [
            record
            for record in store.all_runs()
            if isinstance(record, dict) and record.get("strategy_id") == "crowd_contra"
        ]
        return records[-1] if records else None
    except Exception:
        # A corrupt or incomplete store is not treated as a valid idempotent
        # result; the normal driver path will fail closed and expose the error.
        return None
    finally:
        if store is not None:
            store.close()


def _write_existing_report(record: dict) -> None:
    """Rehydrate the canonical artifact when a retry reuses the append-only run."""
    detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
    pnl = record.get("pnl") if isinstance(record.get("pnl"), dict) else {}
    atomic_write_json(
        _REPORT,
        {
            "schema_version": 1,
            "authoritative": True,
            "evidence_class": "CANONICAL_L2_DRY_RUN",
            "persistence": "DRY_RUN_STORE_EVENT_OUTBOX",
            "projection": "PROMOTION_REQUIRES_VERIFIED_BATCH_REBUILD",
            "readiness": "L2_DRY_RUN_ONLY",
            "strategy_id": record.get("strategy_id", "crowd_contra"),
            "alpha_id": "crowd_contra",
            "expression": "cs_rank(neg(global_ls))",
            "run_id": record.get("run_id"),
            "timestamp": record.get("timestamp"),
            "n_periods": detail.get("n_periods"),
            "pnl": pnl,
            "research_risk": detail.get("research_risk"),
            "gate": detail.get("crowding_gate"),
            "reconciliation_residual": pnl.get("residual"),
        },
        indent=2,
    )


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
    existing = _existing_crowd_run()
    if existing is not None:
        _write_existing_report(existing)
        pnl = existing.get("pnl") if isinstance(existing.get("pnl"), dict) else {}
        print(
            f"crowd_contra dry-run already stored: run_id={existing.get('run_id')} "
            f"sharpe={pnl.get('sharpe')} residual={pnl.get('residual')}",
            flush=True,
        )
        print(f"-> {_OUT}", flush=True)
        return

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

    # Rank #10a: the crowding gate is LOAD-BEARING on this book — evaluated every week from real global_ls
    # evidence, recorded in run detail, and on a genuine BLOCK it skips that week's rebalance (carry prior
    # holdings). **2026-07-07 校准修正(用户签 WARN-only + z-score@3.0)**:rank 平均分是结构性常数(恒 0.500),
    # 现 average_warn_only=True 封顶 WARN(诊断);真正的 BLOCK 由**绝对 z-score 判据**给出——被 short 的拥挤多头
    # 其 global_ls 时序 z > 3.0(挤压风险,实测 21/233≈9% 周触发)-> 跳本周调仓(128.9%→123.8%,回撤 9.8%→8.9%)。
    # 集中度判据对本分散 book 仍从不触发。
    periods, weights_by_period, prices_by_period, rate_by_period = [], {}, {}, {}
    gate_decisions, prev_weights = [], None
    for r in rows:
        legs = _legs(sig[r], QUANTILE)
        if not legs:
            continue
        t = int(ev[r])
        target = {insts[i]: w for i, w in legs.items()}
        crowd = {insts[i]: rank for i, rank in _crowding_rank(gmat, r, list(legs)).items()}
        # 绝对 z-score(正确统计量):每名 global_ls 时序 z,喂 z-score BLOCK 判据(用户签 z*=3.0)。
        z_map = {insts[i]: z for i in legs if (z := _crowding_zscore(gmat, r, i)) is not None}
        blocked = False
        if crowd:
            # average_warn_only=True:rank 平均分是结构性常数(校准文档),诊断非 BLOCK 门;真正的 BLOCK
            # 由绝对 z-score 判据给出(被 short 的拥挤多头 z>3.0 -> 挤压风险 -> 跳本周调仓、carry 持仓)。
            decision = evaluate_crowding_gate(
                target, crowd, average_warn_only=True,
                crowding_z_by_symbol=z_map, max_crowding_z=_MAX_CROWDING_Z)
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

    event_bus = open_domain_event_bus()
    run = run_multi_period_dry_run(
        periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period, gate=gate,
        ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id="crowd_contra", aum=_AUM,
        cost_bps=_COST_BPS, cash_flow_fn=cash_flow_fn,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52},
        event_bus=event_bus, publish_completion_event=False)

    # Rank #10a: surface the crowding-gate profile read-only in the run detail (-> console dry-run page)
    crowding = summarize_gate_decisions(gate_decisions)
    run.detail["crowding_gate"] = crowding
    # Rank #13: run the honest 4-engine research-risk gate over the DEPLOYABLE (vol-targeted) series —
    # that is the risk-managed form that would actually be deployed; the 5 engines the seam can't
    # honestly feed are SKIPPED, not faked. Decision recorded read-only in run detail.
    vt = run.detail.get("equity_curve_vt") or run.detail["equity_curve"]
    risk = evaluate_dry_run_risk([c["ret"] for c in vt], weights_by_period.get(periods[-1], {}),
                                 [c["equity"] for c in vt], timestamps=periods)
    risk["series"] = "vol_target"
    # honest caveat: RiskConfig defaults (max_var_95=0.05 / max_cvar_95=0.08) are daily-tight, not
    # calibrated for weekly returns — this is a diagnostic record, NOT a promotion veto. Threshold
    # calibration by frequency is a research task (cf. R-1). The gate is load-bearing (runs + records
    # honestly, 5 engines SKIPPED not faked); promotion enforcement (guard_to_paper) is a follow-up.
    risk["config_provenance"] = "RISK_CONFIG_DEFAULTS_UNCALIBRATED_FOR_WEEKLY"
    run.detail["research_risk"] = risk
    store = DryRunStore(_OUT, create=True)
    try:
        save_and_project_dry_run(
            store, run, event_bus, now_ms=int(run.timestamp)
        )
        _write_existing_report(run.as_record())
    finally:
        store.close()
        event_bus.close()
    p = run.pnl
    print(f"  拥挤门: {crowding['n_periods']} 周 · PASS {crowding['n_pass']}/WARN {crowding['n_warn']}"
          f"/BLOCK {crowding['n_block']} · 最差分 {crowding['worst_score']}", flush=True)
    print(f"  研究风控门(4真引擎/5诚实跳过): {risk['decision']} · 过 {risk['gates_passed']}/{risk['gates_run']} "
          f"· {[k for k,v in risk['engines_run'].items() if not v] or '全过'}", flush=True)
    print(f"=== CROWD_CONTRA 周度滚动干跑 · OI 大币 {len(instruments)} · {p['n_periods']} 周 · 成本 {_COST_BPS:.0f}bps ===", flush=True)
    print(f"DryRunRun {run.run_id}: 累计意图 {run.n_intents} · 门批准 {run.n_approved} · 门拒绝 {run.n_rejected}", flush=True)
    print(f"  总收益 {p['total_return']*100:.1f}% · 夏普(周→年) {p['sharpe']} · 最大回撤 {p['max_drawdown']*100:.0f}% "
          f"· 累计资金费 ${p['total_funding']:,.0f} · 对账残差 {p['residual']}", flush=True)
    print(f"  raw 曲线 {len(run.detail['equity_curve'])} 点 · vol-target 曲线 {len(run.detail['equity_curve_vt'])} 点 "
          f"-> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
