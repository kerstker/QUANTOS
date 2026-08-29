"""Run the authoritative L2 dry-run for the ``top_contra`` candidate.

The existing diagnostic driver intentionally stopped before the canonical
event bus and :class:`DryRunStore`.  This driver uses the same deterministic
panel, pre-trade gate, paper adapter, PnL reconciliation, and research-risk
snapshot, then persists the result through the shared dry-run outbox and
projection.  It remains strictly L2: no broker SDK, credentials, network
client, or order-submission path is reachable.
"""
from __future__ import annotations

import os
import time

from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.execution.dry_run_store import DryRunStore
from services.portfolio.crowding_gate import summarize_gate_decisions
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (
    AccountRiskState,
    MarketContext,
    RiskContext,
    StrategyApproval,
)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.risk.research_risk.dry_run_guard import evaluate_dry_run_risk
from services.risk.research_risk.domain.models import RiskConfig
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from tools.cache_io import atomic_write_json
from tools.domain_event_bus import open_domain_event_bus, save_and_project_dry_run
from tools.top_contra_dry_run_diagnostic import (
    _AUM,
    _COST_BPS,
    _prepare,
)
from tools.crypto_oi_research import build_panel


_OUT = os.path.join("data", "cache", "dry_run.db")
_REPORT = os.path.join("data", "cache", "top_contra_dry_run.json")
_WEEKLY_RISK_PROVENANCE = "RISK_CONFIG_WEEKLY_PPY_52_DURATION_UNCALIBRATED"


def _write_existing_report(record: dict) -> None:
    """Rehydrate the report for an idempotent retry without recomputing fills."""
    detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
    pnl = record.get("pnl") if isinstance(record.get("pnl"), dict) else {}
    atomic_write_json(
        _REPORT,
        {
            "schema_version": 1,
            "authoritative": True,
            "evidence_class": "CANONICAL_L2_DRY_RUN",
            "persistence": "DRY_RUN_STORE_EVENT_OUTBOX_AND_PROJECTION",
            "readiness": "L2_DRY_RUN_ONLY",
            "strategy_id": record.get("strategy_id", "top_contra"),
            "expression": "cs_zscore(neg(top_ls))",
            "run_id": record.get("run_id"),
            "timestamp": record.get("timestamp"),
            "n_periods": detail.get("n_periods"),
            "pnl": pnl,
            "research_risk": detail.get("research_risk"),
            "gate": detail.get("crowding_gate"),
            "reconciliation_residual": pnl.get("residual"),
            "recovery": "projection promoted and consumer checkpoints recovered",
        },
        indent=2,
    )


def main() -> None:
    quarantine_legacy_backtest("dry_run_top_contra_multi")
    existing_store = DryRunStore(_OUT, create=True)
    try:
        existing = [
            record
            for record in existing_store.all_runs()
            if record.get("strategy_id") == "top_contra"
            and isinstance(record.get("detail"), dict)
            and isinstance(record["detail"].get("research_risk"), dict)
            and record["detail"]["research_risk"].get("config_provenance")
            == _WEEKLY_RISK_PROVENANCE
        ]
        if existing:
            _write_existing_report(existing[-1])
            record = existing[-1]
            pnl = record.get("pnl") or {}
            print(
                f"top_contra authoritative dry-run already stored: run_id={record.get('run_id')} "
                f"sharpe={pnl.get('sharpe')} residual={pnl.get('residual')}",
                flush=True,
            )
            print(f"-> {_OUT}; {_REPORT}", flush=True)
            return
    finally:
        existing_store.close()

    panel, instruments = build_panel()
    periods, weights_by_period, prices_by_period, rate_by_period, gate_decisions = _prepare(panel)
    if not periods:
        raise RuntimeError("top_contra authoritative dry-run has no usable weekly periods")

    held_names = sorted({name for weights in weights_by_period.values() for name in weights})

    def cash_flow_fn(held, prices, period):
        rates = rate_by_period.get(period, {})
        return sum(
            -rates.get(name, 0.0) * quantity * prices.get(name, 0.0)
            for name, quantity in held.items()
        )

    kill_switch = KillSwitch()
    limits = RiskLimits(
        allowed_instruments=frozenset(held_names),
        allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10**13,
        max_order_notional=1e14,
        fat_finger_qty=10**13,
        max_gross_exposure=1e14,
        max_net_exposure=1e14,
        max_leverage=1e7,
        max_single_name_pct=1e7,
        max_turnover=1e7,
        max_participation_rate=1e7,
        max_instrument_vol=5.0,
        daily_loss_limit=1e14,
        max_drawdown=1.0,
    )
    gate = PreTradeRiskGate(limits, kill_switch=kill_switch)

    def ctx_factory(intent, prices, period):
        return RiskContext(
            account=AccountRiskState(
                account_id="paper",
                cash=_AUM * 10,
                equity=_AUM * 10,
                day_pnl=0.0,
                peak_equity=_AUM * 10,
                positions=(),
            ),
            market=MarketContext(
                instrument_id=intent.instrument_id,
                known=True,
                reference_price=prices.get(intent.instrument_id, 1.0),
                market_open=True,
                price_as_of=period,
                est_daily_volume=1e12,
                instrument_vol=0.8,
            ),
            strategy=StrategyApproval(
                strategy_id="top_contra",
                approved=True,
                approval_id="research-dry-run",
            ),
            now=period,
            max_staleness=10**15,
        )

    event_bus = open_domain_event_bus()
    store = DryRunStore(_OUT, create=True)
    try:
        run = run_multi_period_dry_run(
            periods=periods,
            weights_by_period=weights_by_period,
            prices_by_period=prices_by_period,
            gate=gate,
            ctx_factory=ctx_factory,
            adapter=DryRunAdapterEngine(),
            strategy_id="top_contra",
            aum=_AUM,
            cost_bps=_COST_BPS,
            cash_flow_fn=cash_flow_fn,
            vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52},
            event_bus=event_bus,
            publish_completion_event=False,
            research_risk_config=RiskConfig(periods_per_year=52),
        )

        crowding = summarize_gate_decisions(gate_decisions)
        run.detail["crowding_gate"] = crowding
        vt = run.detail.get("equity_curve_vt") or run.detail["equity_curve"]
        risk = evaluate_dry_run_risk(
            [point["ret"] for point in vt],
            weights_by_period.get(periods[-1], {}),
            [point["equity"] for point in vt],
            timestamps=periods,
            config=RiskConfig(periods_per_year=52),
        )
        risk["series"] = "vol_target"
        risk["config_provenance"] = _WEEKLY_RISK_PROVENANCE
        run.detail["research_risk"] = risk

        projection = save_and_project_dry_run(
            store,
            run,
            event_bus,
            now_ms=max(int(run.timestamp), int(time.time() * 1000)),
            project=False,
        )
        pnl = run.pnl
        report = {
            "schema_version": 1,
            "authoritative": True,
            "evidence_class": "CANONICAL_L2_DRY_RUN",
            "persistence": "DRY_RUN_STORE_EVENT_OUTBOX_AND_PROJECTION",
            "readiness": "L2_DRY_RUN_ONLY",
            "strategy_id": run.strategy_id,
            "expression": "cs_zscore(neg(top_ls))",
            "source": "local:data/cache/crypto_funding.db+crypto_oi_metrics.json",
            "n_instruments": len(instruments),
            "n_periods": len(periods),
            "first_period": periods[0],
            "last_period": periods[-1],
            "run_id": run.run_id,
            "run_reproducibility_hash": run.reproducibility_hash,
            "gate": crowding,
            "pnl": dict(pnl),
            "research_risk": risk,
            "projection": projection,
            "reconciliation_residual": pnl.get("residual"),
        }
        atomic_write_json(_REPORT, report, indent=2)
        print(
            f"top_contra authoritative dry-run: periods={len(periods)} "
            f"approved={run.n_approved} rejected={run.n_rejected} "
            f"raw_sharpe={pnl['sharpe']} vt_sharpe={pnl.get('sharpe_vt')} "
            f"residual={pnl['residual']}",
            flush=True,
        )
        print(f"-> {_OUT}; {_REPORT}", flush=True)
    finally:
        store.close()
        event_bus.close()


if __name__ == "__main__":
    main()
