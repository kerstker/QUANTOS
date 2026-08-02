"""Persist the canonical L2 dry-run for ``carry_oi_majors``.

The legacy carry driver is a print-only diagnostic and deletes event/cache
files before running.  This driver is the non-destructive authoritative path:
it reads the cached PIT funding panel, runs the real pre-trade gate + impact
model + paper adapter, commits the run and research-risk event to the shared
dry-run outbox, and leaves projection catch-up to the verified batch rebuild
workflow.  No broker, credentials, or live order path is reachable.
"""
from __future__ import annotations

import json
import os
import statistics
import time

from services.data.caching_store import CachingPITStore
from services.data.persistent_store import SqlitePITStore
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore
from services.execution.impact_calibration import (
    default_crypto_impact_calibration,
    scenario_impact,
)
from services.research import alpha_expression as ax
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
from tools.crypto_carry_strategy import COMPOSITE, QUANTILE, _legs
from tools.crypto_carry_stress import _slip_bps
from tools.domain_event_bus import open_domain_event_bus, save_and_project_dry_run


_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND = os.path.join("data", "cache", "crypto_funding_rates.json")
_OUT = os.path.join("data", "cache", "dry_run.db")
_REPORT = os.path.join("data", "cache", "carry_oi_majors_dry_run.json")
_AUM = 1_000_000.0
_MAX_PARTICIPATION = 0.10
_WEEK_MS = 7 * 24 * 3600 * 1000
_WEEKLY_RISK_PROVENANCE = "RISK_CONFIG_WEEKLY_PPY_52_DURATION_UNCALIBRATED"


def _write_existing_report(record: dict) -> None:
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
            "strategy_id": record.get("strategy_id", "carry"),
            "alpha_id": "carry_oi_majors",
            "expression": "composite negative funding carry",
            "run_id": record.get("run_id"),
            "timestamp": record.get("timestamp"),
            "n_periods": detail.get("n_periods"),
            "pnl": pnl,
            "research_risk": detail.get("research_risk"),
            "reconciliation_residual": pnl.get("residual"),
        },
        indent=2,
    )


def _prepare() -> tuple[list[int], dict, dict, dict, dict, dict, float, int]:
    instruments = sorted(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    raw_store = SqlitePITStore(_DB, create=False)
    store = CachingPITStore(raw_store)
    funding = {
        ticker: {int(timestamp): value for timestamp, value in rows.items()}
        for ticker, rows in json.load(open(_FUND, encoding="utf-8")).items()
    }

    liquidity: dict[str, float] = {}
    for instrument in instruments:
        notionals = [
            bar.close * bar.volume
            for bar in store.fetch_bars(instrument, -10**18, 10**18)
            if bar.close > 0 and bar.volume > 0
        ]
        liquidity[instrument] = statistics.median(notionals) if notionals else 0.0
    ranked = sorted(instruments, key=liquidity.get)
    pctile = {instrument: (index + 1) / len(ranked) for index, instrument in enumerate(ranked)}
    slip = {instrument: _slip_bps(pctile[instrument]) for instrument in instruments}
    liquid = [instrument for instrument in instruments if pctile[instrument] >= 0.5]

    event_times = sorted(
        {
            bar.event_time
            for instrument in liquid
            for bar in store.fetch_bars(instrument, -10**18, 10**18)
        }
    )
    panel = ax.build_field_panel(store, liquid, event_times, ("close", "volume"))
    panel.matrices["funding"] = [
        [funding.get(instrument, {}).get(timestamp) for instrument in panel.instruments]
        for timestamp in panel.event_times
    ]
    signal = ax.evaluate(COMPOSITE, panel)
    instruments = panel.instruments
    close = panel.matrices["close"]
    funding_matrix = panel.matrices["funding"]
    volume = panel.matrices["volume"]

    rows: list[int] = []
    last: int | None = None
    for row, timestamp in enumerate(panel.event_times):
        if last is None or timestamp - last >= _WEEK_MS:
            rows.append(row)
            last = timestamp

    periods: list[int] = []
    weights_by_period: dict[int, dict[str, float]] = {}
    prices_by_period: dict[int, dict[str, float]] = {}
    rate_by_period: dict[int, dict[str, float]] = {}
    adv_by_period: dict[int, dict[str, float]] = {}
    volume_by_period: dict[int, dict[str, float]] = {}
    for row in rows:
        legs = _legs(signal[row], QUANTILE)
        if not legs:
            continue
        timestamp = int(panel.event_times[row])
        periods.append(timestamp)
        weights_by_period[timestamp] = {
            instruments[index]: weight for index, weight in legs.items()
        }
        prices_by_period[timestamp] = {
            instruments[index]: close[row][index]
            for index in legs
            if close[row][index] and close[row][index] > 0
        }
        rate_by_period[timestamp] = {
            instruments[index]: funding_matrix[row][index] or 0.0
            for index in legs
        }
        adv_by_period[timestamp] = {
            instruments[index]: close[row][index] * volume[row][index]
            for index in legs
            if close[row][index]
            and volume[row][index]
            and close[row][index] > 0
            and volume[row][index] > 0
        }
        volume_by_period[timestamp] = {
            instruments[index]: volume[row][index]
            for index in legs
            if volume[row][index] and volume[row][index] > 0
        }

    held_names = sorted({name for weights in weights_by_period.values() for name in weights})
    cost_bps = statistics.fmean([slip[name] for name in held_names]) if held_names else 0.0
    return (
        periods,
        weights_by_period,
        prices_by_period,
        rate_by_period,
        adv_by_period,
        volume_by_period,
        cost_bps,
        len(liquid),
    )


def main() -> None:
    existing_store = DryRunStore(_OUT, create=True)
    try:
        existing = [
            record
            for record in existing_store.all_runs()
            if record.get("strategy_id") == "carry"
            and isinstance(record.get("detail"), dict)
            and isinstance(record["detail"].get("research_risk"), dict)
            and record["detail"]["research_risk"].get("config_provenance")
            == _WEEKLY_RISK_PROVENANCE
        ]
        if existing:
            _write_existing_report(existing[-1])
            record = existing[-1]
            print(
                f"carry authoritative dry-run already stored: run_id={record.get('run_id')} "
                f"sharpe={(record.get('pnl') or {}).get('sharpe')}",
                flush=True,
            )
            print(f"-> {_OUT}; {_REPORT}", flush=True)
            return
    finally:
        existing_store.close()

    (
        periods,
        weights_by_period,
        prices_by_period,
        rate_by_period,
        adv_by_period,
        volume_by_period,
        cost_bps,
        n_liquid,
    ) = _prepare()
    if not periods:
        raise RuntimeError("carry authoritative dry-run has no usable weekly periods")
    held_names = sorted({name for weights in weights_by_period.values() for name in weights})

    def cash_flow_fn(held, prices, period):
        rates = rate_by_period.get(period, {})
        return sum(
            -rates.get(name, 0.0) * quantity * prices.get(name, 0.0)
            for name, quantity in held.items()
        )

    gate = PreTradeRiskGate(
        RiskLimits(
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
            max_participation_rate=_MAX_PARTICIPATION,
            max_instrument_vol=5.0,
            daily_loss_limit=1e14,
            max_drawdown=1.0,
        ),
        kill_switch=KillSwitch(),
    )

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
                est_daily_volume=volume_by_period.get(period, {}).get(
                    intent.instrument_id, 1e18
                ),
                instrument_vol=0.8,
            ),
            strategy=StrategyApproval(
                strategy_id="carry",
                approved=True,
                approval_id="research-dry-run",
            ),
            now=period,
            max_staleness=10**15,
        )

    run = None
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
            strategy_id="carry",
            aum=_AUM,
            cost_bps=cost_bps,
            cash_flow_fn=cash_flow_fn,
            impact=scenario_impact(default_crypto_impact_calibration().base),
            adv_by_period=adv_by_period,
            vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 3.0, "ppy": 52},
            event_bus=event_bus,
            publish_completion_event=False,
            research_risk_config=RiskConfig(periods_per_year=52),
        )
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
            # `run.timestamp` is the historical last market period; the
            # dispatcher/consumer clock must use current wall time so a
            # strategy whose period ends earlier cannot move the shared
            # consumer lease backwards after another strategy has committed.
            now_ms=max(int(run.timestamp), int(time.time() * 1000)),
            project=False,
        )
        pnl = run.pnl
        atomic_write_json(
            _REPORT,
            {
                "schema_version": 1,
                "authoritative": True,
                "evidence_class": "CANONICAL_L2_DRY_RUN",
                "persistence": "DRY_RUN_STORE_EVENT_OUTBOX",
                "projection": projection,
                "readiness": "L2_DRY_RUN_ONLY",
                "strategy_id": "carry",
                "alpha_id": "carry_oi_majors",
                "expression": "composite negative funding carry",
                "n_liquid_instruments": n_liquid,
                "n_periods": len(periods),
                "first_period": periods[0],
                "last_period": periods[-1],
                "run_id": run.run_id,
                "run_reproducibility_hash": run.reproducibility_hash,
                "pnl": dict(pnl),
                "research_risk": risk,
                "reconciliation_residual": pnl.get("residual"),
            },
            indent=2,
        )
        print(
            f"carry authoritative dry-run: periods={len(periods)} "
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
