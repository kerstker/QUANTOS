"""Automatic Alpha-family discovery -> validation -> paper shadow runner.

The driver is intentionally conservative:

* it searches only the ten frozen family specifications in
  :mod:`services.research.alpha_family_catalog`;
* all signals are evaluated from the existing PIT daily panel;
* missing fields and unavailable time scales are recorded as BLOCKED rather
  than back-filled or silently down-sampled;
* statistics use net-of-cost dollar-neutral panel returns and a durable,
  catalog-wide trial count;
* candidates that have enough clean data may enter a **paper-only shadow** run.
  This is not Alpha promotion, not live execution, and never touches a broker.

The module keeps the network boundary outside ``services/``.  Refreshing data
is a separate explicit operation; this driver consumes the cached PIT snapshot
so the report is reproducible.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

# Keep both supported entry points equivalent: ``python -m tools...`` and the
# operator-friendly ``python tools/alpha_family_discovery.py`` from the repo
# root.  The latter normally puts only ``tools/`` on sys.path.
if __package__ in {None, ""}:  # pragma: no cover - exercised by CLI smoke
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.cost import CostModel
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.data.caching_store import CachingPITStore
from services.data.persistent_store import SqlitePITStore
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.execution.dry_run_pipeline import run_multi_period_dry_run
from services.execution.dry_run_store import DryRunStore
from services.research import alpha_expression as ax
from services.research.alpha_family_catalog import AlphaFamilySpec, family_specs
from services.research.alpha_family_registry import AlphaFamilyRegistry
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.trial_provenance import trial_provenance_for_definition
from services.alpha.metrics import series_metrics
from services.alpha.panel_backtest import PanelPeriod, run_panel_backtest
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (
    AccountRiskState,
    MarketContext,
    RiskContext,
    StrategyApproval,
)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.validation.deflated_sharpe import deflated_sharpe_ratio, pbo_cscv
from services.validation.factor_statistics import compute_ic
from services.validation.performance import max_drawdown, sharpe
from services.validation.regime import label_regimes_by_vol, regime_profitable_frac
from tools.crypto_oi_research import build_panel
from tools.search_trial_registry import field_panel_dataset_version, make_search_id
from tools.domain_event_bus import open_domain_event_bus, save_and_project_dry_run


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FAMILY_DB = ROOT / "data" / "cache" / "alpha_family_registry.db"
DEFAULT_EXPERIMENT_DB = ROOT / "data" / "cache" / "experiment_registry.db"
DEFAULT_DRY_RUN_DB = ROOT / "data" / "cache" / "alpha_family_dry_run.db"
DEFAULT_REPORT = ROOT / "data" / "cache" / "alpha_family_discovery.json"
DEFAULT_MARKDOWN = ROOT / "docs" / "reports" / "alpha-family-discovery-2026-07-25.md"

FEE_PRESSURE_BPS = (0.0, 4.0, 8.0)
BASE_FEE_BPS = 4.0
SLIPPAGE_BPS = 2.0
IMPACT_COEF = 10.0
PAPER_AUM = 1_000_000.0
PAPER_LOOKBACK = 365
PAPER_Q = 0.20
PAPER_COST_BPS = 4.0
STRICT_DSR_MIN = 0.95
STRICT_PBO_MAX = 0.25
STRICT_OOS_SHARPE_MIN = 0.80
STRICT_WF_POSITIVE_FRAC = 0.75
STRICT_REGIME_POSITIVE_FRAC = 0.75
STRICT_MAX_DRAWDOWN = 0.15


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _raw_observed_sr(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        return 0.0
    sd = statistics.pstdev(returns)
    return statistics.fmean(returns) / sd if sd > 0 else 0.0


def _chunk_sharpe(returns: Sequence[float], n_chunks: int = 4, ppy: int = 365) -> tuple[list[float], float | None]:
    if len(returns) < n_chunks * 2:
        return [], None
    chunks = []
    for i in range(n_chunks):
        lo = (len(returns) * i) // n_chunks
        hi = (len(returns) * (i + 1)) // n_chunks
        block = list(returns[lo:hi])
        if len(block) < 2:
            return [], None
        chunks.append(sharpe(block, ppy))
    return chunks, sum(1 for value in chunks if value > 0) / len(chunks)


def _holdout_sharpe(returns: Sequence[float], *, ppy: int) -> float:
    if len(returns) < 8:
        return 0.0
    start = max(0, int(len(returns) * 0.75))
    return sharpe(list(returns[start:]), ppy)


def _market_proxy(periods: Sequence[PanelPeriod]) -> list[float]:
    equity, out = 1.0, []
    for period in periods:
        mean_return = statistics.fmean(period.forward_returns) if period.forward_returns else 0.0
        equity *= 1.0 + mean_return
        out.append(equity)
    return out


def _periods_for_signal(signal, panel, *, horizon: int) -> list[PanelPeriod]:
    """Build PIT periods and attach only signal-date ADV (never future volume)."""
    base = ax.panel_to_periods(signal, panel, fwd_horizon=horizon, non_overlapping=horizon > 1)
    row_by_time = {int(t): i for i, t in enumerate(panel.event_times)}
    inst_by_name = {name: i for i, name in enumerate(panel.instruments)}
    volume = panel.matrices.get("volume", [])
    close = panel.matrices.get("close", [])
    out = []
    for period in base:
        r = row_by_time.get(int(period.event_time))
        adv = []
        if r is None:
            continue
        for name in period.instruments:
            i = inst_by_name[name]
            v = volume[r][i] if r < len(volume) else None
            px = close[r][i] if r < len(close) else None
            # crypto_funding.db volume is base-unit volume; convert once to USD
            # notional.  Missing/invalid ADV stays missing and only disables the
            # impact component for that name; it is never synthesized.
            adv.append(float(v) * float(px) if _finite(v) and _finite(px) and float(v) > 0 and float(px) > 0 else 0.0)
        out.append(PanelPeriod(period.instruments, period.signals, period.forward_returns,
                               tuple(adv), period.event_time))
    return out


def _pressure_metrics(periods: Sequence[PanelPeriod], *, ppy: int) -> dict:
    cells = {}
    for fee in FEE_PRESSURE_BPS:
        result = run_panel_backtest(
            periods,
            cost=CostModel(fee_bps=fee, slippage_bps=SLIPPAGE_BPS, impact_coef=IMPACT_COEF),
            quantile=PAPER_Q,
            target_aum=PAPER_AUM,
        )
        equity = result.net_equity_curve()
        rs = list(result.period_returns)
        cells[str(int(fee))] = {
            "fee_bps": fee,
            "net_return": result.total_net_return(),
            "gross_return": result.total_gross_return(),
            "sharpe": sharpe(rs, ppy),
            "max_drawdown": max_drawdown(equity),
            "cost_drag": sum(result.costs),
            "turnover": statistics.fmean(result.turnovers) if result.turnovers else 0.0,
            "reconciliation_residual": result.reconciliation_residual(),
        }
    return {
        "cells": cells,
        "worst_sharpe": min(c["sharpe"] for c in cells.values()),
        "worst_net_return": min(c["net_return"] for c in cells.values()),
        "worst_max_drawdown": max(c["max_drawdown"] for c in cells.values()),
    }


def _pbo_by_cadence(results: Mapping[str, dict]) -> dict[str, Optional[float]]:
    groups: dict[int, list[tuple[str, dict[int, float]]]] = defaultdict(list)
    for cid, result in results.items():
        periods = result.get("periods", [])
        if len(periods) < 16:
            continue
        stream = {int(p.event_time): float(value) for p, value in zip(periods, result["returns"])}
        groups[int(result["horizon"])].append((cid, stream))
    out: dict[str, Optional[float]] = {}
    for items in groups.values():
        if len(items) < 2:
            for cid, _ in items:
                out[cid] = None
            continue
        common = set(items[0][1])
        for _, stream in items[1:]:
            common &= set(stream)
        times = sorted(common)
        if len(times) < 16:
            value = None
        else:
            # Aggregate aligned observations into eight contiguous calendar
            # blocks; pbo_cscv consumes (time-block x candidate), not raw rows.
            n_blocks = min(8, len(times) // 2)
            matrix = []
            for block in range(n_blocks):
                lo = (len(times) * block) // n_blocks
                hi = (len(times) * (block + 1)) // n_blocks
                matrix.append([
                    statistics.fmean(stream[t] for t in times[lo:hi])
                    for _, stream in items
                ])
            try:
                value = pbo_cscv(matrix)
            except (ValueError, ZeroDivisionError):
                value = None
        for cid, _ in items:
            out[cid] = value
    return out


def _candidate_definition(spec: AlphaFamilySpec) -> AlphaDefinition:
    expression = ax.serialize(spec.expression) if spec.expression is not None else ""
    return AlphaDefinition(
        alpha_id=spec.candidate_id,
        name=spec.name_zh,
        factor_ids=tuple(sorted(spec.required_fields)),
        universe="crypto_majors_pit",
        params={
            "family_id": spec.family_id,
            "expression": expression,
            "intended_frequency": spec.intended_frequency,
            "evaluation_frequency": spec.evaluation_frequency,
        },
    )


def _candidate_payload(spec: AlphaFamilySpec, definition: AlphaDefinition) -> dict:
    return {
        "candidate_id": spec.candidate_id,
        "family_id": spec.family_id,
        "name_zh": spec.name_zh,
        "complementarity": spec.complementarity,
        "intended_frequency": spec.intended_frequency,
        "evaluation_frequency": spec.evaluation_frequency,
        "required_fields": list(spec.required_fields),
        "expression": ax.serialize(spec.expression) if spec.expression is not None else None,
        "config_hash": definition.definition_hash,
        "paper_eligible": bool(spec.paper_eligible),
        "notes": spec.notes,
    }


def _build_paper_inputs(periods: Sequence[PanelPeriod], *, panel, q: float = PAPER_Q):
    row_by_time = {int(t): i for i, t in enumerate(panel.event_times)}
    inst_by_name = {name: i for i, name in enumerate(panel.instruments)}
    close = panel.matrices["close"]
    funding = panel.matrices.get("funding", [])
    weights, prices, rates = {}, {}, {}
    for period in periods:
        r = row_by_time[int(period.event_time)]
        order = sorted(range(len(period.signals)), key=lambda i: (period.signals[i], i))
        k = max(1, int(round(len(order) * q)))
        longs, shorts = order[-k:], order[:k]
        target = {}
        for i in longs:
            target[period.instruments[i]] = 0.5 / len(longs)
        for i in shorts:
            target[period.instruments[i]] = target.get(period.instruments[i], 0.0) - 0.5 / len(shorts)
        t = int(period.event_time)
        weights[t] = target
        prices[t] = {
            name: float(close[r][inst_by_name[name]])
            for name in target
            if _finite(close[r][inst_by_name[name]]) and float(close[r][inst_by_name[name]]) > 0
        }
        rates[t] = {
            name: float(funding[r][inst_by_name[name]])
            for name in target
            if funding and r < len(funding) and _finite(funding[r][inst_by_name[name]])
        }
    return [int(p.event_time) for p in periods], weights, prices, rates


def _paper_shadow_run(candidate_id: str, periods: Sequence[PanelPeriod], *, panel,
                      dry_run_db: str) -> tuple[dict, Any]:
    if not periods:
        raise ValueError("paper shadow requires periods")
    # Paper execution must have real current exchange filters.  The cached
    # public Binance USDC exchangeInfo artifact is the only accepted source;
    # no generic step/min-notional defaults are allowed.
    filter_path = ROOT / "data" / "cache" / "binance_usdc_alpha_retest.json"
    if not filter_path.exists():
        raise ValueError("Binance USDC exchange filters missing; paper shadow blocked")
    filter_doc = json.loads(filter_path.read_text(encoding="utf-8"))
    filter_generated_at = filter_doc.get("generated_at")
    if not _finite(filter_generated_at):
        raise ValueError("Binance USDC exchange filters have no generated_at; paper shadow blocked")
    if abs(int(time.time() * 1000) - int(filter_generated_at)) > 7 * 24 * 3600 * 1000:
        raise ValueError("Binance USDC exchange filters are older than seven days; paper shadow blocked")
    raw_filters = filter_doc.get("filters")
    if not isinstance(raw_filters, dict):
        raise ValueError("Binance USDC exchange filters malformed; paper shadow blocked")
    filters = {}
    for name, item in raw_filters.items():
        if not isinstance(item, dict):
            continue
        if all(_finite(item.get(key)) for key in ("step_size", "min_qty", "min_notional")):
            filters[str(name)] = (float(item["step_size"]), float(item["min_qty"]),
                                  float(item["min_notional"]))
    eligible = set(filters) & set(panel.instruments)
    if len(eligible) < 4:
        raise ValueError("fewer than four panel instruments have current Binance USDC filters")

    # Keep a bounded, current tail so automatic discovery cannot monopolize the
    # machine.  The full history remains in the evaluation artifact.
    selected = []
    for period in periods[-PAPER_LOOKBACK:]:
        idx = [i for i, name in enumerate(period.instruments) if name in eligible]
        if len(idx) < 4:
            continue
        selected.append(PanelPeriod(
            tuple(period.instruments[i] for i in idx),
            tuple(period.signals[i] for i in idx),
            tuple(period.forward_returns[i] for i in idx),
            tuple(period.adv_notional[i] for i in idx) if period.adv_notional else (),
            period.event_time,
        ))
    if not selected:
        raise ValueError("no paper periods remain after exchange-filter universe intersection")
    event_times, weights, prices, rates = _build_paper_inputs(selected, panel=panel)
    held_names = sorted({name for row in weights.values() for name in row})

    def cash_flow_fn(held, marks, period):
        row = rates.get(int(period), {})
        return sum(-row.get(name, 0.0) * qty * marks.get(name, 0.0) for name, qty in held.items())

    ks = KillSwitch()
    limits = RiskLimits(
        allowed_instruments=frozenset(held_names), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10**13, max_order_notional=1e14, fat_finger_qty=10**13,
        max_gross_exposure=1e14, max_net_exposure=1e14, max_leverage=1e7,
        max_single_name_pct=1e7, max_turnover=1e7, max_participation_rate=1e7,
        max_instrument_vol=5.0, daily_loss_limit=1e14, max_drawdown=1.0,
    )
    gate = PreTradeRiskGate(limits, kill_switch=ks)

    def ctx_factory(intent, marks, period):
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=PAPER_AUM, equity=PAPER_AUM,
                                     day_pnl=0.0, peak_equity=PAPER_AUM, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=True,
                                 reference_price=marks.get(intent.instrument_id, 1.0), market_open=True,
                                 price_as_of=int(period), est_daily_volume=1e12, instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id=f"alpha-family:{candidate_id}", approved=True,
                                      approval_id="research-paper-shadow"),
            now=int(period), max_staleness=10**15,
        )

    run = run_multi_period_dry_run(
        periods=event_times, weights_by_period=weights, prices_by_period=prices,
        gate=gate, ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(),
        strategy_id=f"alpha_family:{candidate_id}:binance_usdc_filters", aum=PAPER_AUM,
        cost_bps=PAPER_COST_BPS, cash_flow_fn=cash_flow_fn,
        vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": 2.0, "ppy": 365},
        filters=filters,
        event_bus=open_domain_event_bus(), publish_completion_event=False,
    )
    store = DryRunStore(dry_run_db, create=True)
    try:
        save_and_project_dry_run(store, run, open_domain_event_bus(),
                                 now_ms=max(int(run.timestamp), int(time.time() * 1000)), project=False)
    finally:
        store.close()
    curve = run.detail.get("equity_curve", [])
    paper_returns = [float(row["ret"]) for row in curve]
    paper_metrics = {
        "run_id": run.run_id,
        "n_periods": len(paper_returns),
        "total_return": run.pnl.get("total_return"),
        "paper_sharpe_365": sharpe(paper_returns, 365),
        "max_drawdown": max_drawdown([float(row["equity"]) for row in curve]) if curve else 0.0,
        "n_intents": run.n_intents,
        "n_approved": run.n_approved,
        "n_rejected": run.n_rejected,
        "reconciliation_residual": run.pnl.get("residual"),
        "paper_mode": "DAILY_PIT_EXECUTION_SHADOW",
        "filter_source": "data/cache/binance_usdc_alpha_retest.json",
        "filter_generated_at": int(filter_generated_at),
        "filter_universe_size": len(eligible),
        "filter_universe": sorted(eligible),
        "live_submission": False,
    }
    return paper_metrics, run


def discover(
    *,
    panel=None,
    family_db: str = str(DEFAULT_FAMILY_DB),
    experiment_db: str = str(DEFAULT_EXPERIMENT_DB),
    dry_run_db: str = str(DEFAULT_DRY_RUN_DB),
    recorded_at: Optional[int] = None,
    run_paper: bool = True,
) -> dict:
    """Run one deterministic discovery pass and persist all evidence."""
    quarantine_legacy_backtest("tools.alpha_family_discovery")
    now = int(recorded_at if recorded_at is not None else time.time() * 1000)
    panel = panel or build_panel(add_basis=True, add_intraday=True)[0]
    source_files = [
        "data/cache/crypto_funding.db", "data/cache/crypto_funding_rates.json",
        "data/cache/crypto_oi_metrics.json", "data/cache/crypto_oi_intraday.json",
        "data/cache/crypto_binance.db",
    ]
    source_versions = {}
    for name in source_files:
        path = ROOT / name
        source_versions[name] = {"size": path.stat().st_size if path.exists() else None,
                                 "mtime_ns": path.stat().st_mtime_ns if path.exists() else None}
    dataset_version = field_panel_dataset_version(panel, source_versions=source_versions)

    registry = AlphaFamilyRegistry(family_db, create=True)
    specs = family_specs()
    definitions = {spec.candidate_id: _candidate_definition(spec) for spec in specs}
    for spec in specs:
        registry.register_candidate(_candidate_payload(spec, definitions[spec.candidate_id]), recorded_at=now)

    # Durable global trial count: candidates with real expressions enter the
    # existing experiment registry.  The trial population is never reset.
    exp_store = SqliteExperimentStore(experiment_db, create=False)
    exp_registry = ExperimentRegistry(exp_store)
    catalog = AlphaCatalog()
    exp_registry.load_for_evaluation(catalog)
    search_id = make_search_id(source="alpha_family_discovery", search_kind="alpha_family_scan",
                               dataset_version=dataset_version, recorded_at=now,
                               params={"candidate_ids": [s.candidate_id for s in specs]})
    exp_registry.begin_search_attempt(search_id=search_id, search_kind="alpha_family_scan",
                                      source="tools.alpha_family_discovery", dataset_version=dataset_version,
                                      recorded_at=now)
    supported = [s for s in specs if s.expression is not None and s.evaluation_frequency != "unavailable"]
    config_hashes = [definitions[s.candidate_id].definition_hash for s in supported]
    exp_registry.register_search_candidates(search_id, config_hashes, registered_at=now)
    for spec in supported:
        exp_registry.record_trial_provenance(
            trial_provenance_for_definition(
                definitions[spec.candidate_id],
                research_session_id=search_id,
                actor_id="tools.alpha_family_discovery",
                dataset_version=dataset_version,
                recorded_at=now,
                model_name="alpha-family-catalog",
            )
        )
        catalog.register(definitions[spec.candidate_id])

    evaluated: dict[str, dict] = {}
    result_rows: dict[str, dict] = {}
    for spec in specs:
        available_fields = set(panel.matrices)
        missing = sorted(set(spec.required_fields) - available_fields)
        if spec.expression is None or spec.evaluation_frequency == "unavailable" or missing:
            result_rows[spec.candidate_id] = {
                "candidate_id": spec.candidate_id, "family_id": spec.family_id,
                "status": "DATA_UNAVAILABLE", "verdict": "BLOCKED_DATA",
                "reason": (f"missing fields: {missing}" if missing else spec.notes),
                "required_fields": list(spec.required_fields), "available_fields": sorted(available_fields),
                "dataset_version": dataset_version,
            }
            registry.record_evaluation(spec.candidate_id, result_rows[spec.candidate_id],
                                       dataset_version=dataset_version, recorded_at=now)
            continue
        try:
            horizon = 7 if spec.evaluation_frequency == "weekly" else 1
            signal = ax.evaluate(spec.expression, panel)
            periods = _periods_for_signal(signal, panel, horizon=horizon)
            if len(periods) < 16:
                raise ValueError(f"too few PIT periods: {len(periods)}")
            ic_series = [compute_ic(list(p.signals), list(p.forward_returns)).get("ic_mean") for p in periods]
            breadth = [len(p.instruments) for p in periods]
            metrics = series_metrics(ic_series, breadth, min_periods=12, min_breadth=10)
            baseline = run_panel_backtest(
                periods, cost=CostModel(fee_bps=BASE_FEE_BPS, slippage_bps=SLIPPAGE_BPS, impact_coef=IMPACT_COEF),
                quantile=PAPER_Q, target_aum=PAPER_AUM,
            )
            returns = list(baseline.period_returns)
            ppy = 52 if horizon == 7 else 365
            eq = baseline.net_equity_curve()
            folds, wf_frac = _chunk_sharpe(returns, ppy=ppy)
            oos_sharpe = _holdout_sharpe(returns, ppy=ppy)
            regime_labels = label_regimes_by_vol(_market_proxy(periods), vol_window=min(20, max(2, len(returns) // 10)), n_regimes=2)
            regime_frac = regime_profitable_frac(returns, regime_labels)
            observed = _raw_observed_sr(returns)
            evaluated[spec.candidate_id] = {
                "candidate_id": spec.candidate_id, "family_id": spec.family_id,
                "spec": spec, "periods": periods, "returns": returns, "signal": signal,
                "horizon": horizon, "metrics": metrics, "baseline": baseline,
                "observed_sr": observed, "oos_sharpe": oos_sharpe,
                "fold_sharpes": folds, "wf_positive_frac": wf_frac,
                "regime_positive_frac": regime_frac,
                "pressure": _pressure_metrics(periods, ppy=ppy),
            }
        except Exception as exc:
            result_rows[spec.candidate_id] = {
                "candidate_id": spec.candidate_id, "family_id": spec.family_id,
                "status": "RESEARCH_BLOCKED", "verdict": "FAIL",
                "reason": f"evaluation error: {type(exc).__name__}: {exc}",
                "dataset_version": dataset_version,
            }
            registry.record_evaluation(spec.candidate_id, result_rows[spec.candidate_id],
                                       dataset_version=dataset_version, recorded_at=now)

    pbo = _pbo_by_cadence(evaluated)
    # Feed current return streams to the loaded catalog before computing DSR;
    # historical alpha_state rows remain part of the global variance estimate.
    for cid, item in evaluated.items():
        catalog.set_returns(cid, item["returns"])
    trial_count = catalog.global_trial_count()
    var_sr = catalog.catalog_var_sr()
    if var_sr is None:
        var_sr = None
    paper_runs = []
    for spec in specs:
        cid = spec.candidate_id
        item = evaluated.get(cid)
        if item is None:
            continue
        pbo_value = pbo.get(cid)
        dsr = None
        if var_sr is not None:
            try:
                dsr = deflated_sharpe_ratio(item["observed_sr"], max(len(item["returns"]), 2),
                                            max(trial_count, 1), var_sr)
            except (ValueError, ZeroDivisionError):
                dsr = None
        gate_reasons = []
        if not item["metrics"]["coverage_ok"]:
            gate_reasons.append("THIN_PIT_COVERAGE")
        if dsr is None or dsr < STRICT_DSR_MIN:
            gate_reasons.append(f"DSR_BELOW_{STRICT_DSR_MIN:.2f}")
        if pbo_value is None or pbo_value > STRICT_PBO_MAX:
            gate_reasons.append(f"PBO_ABOVE_{STRICT_PBO_MAX:.2f}_OR_UNASSESSED")
        if item["oos_sharpe"] < STRICT_OOS_SHARPE_MIN:
            gate_reasons.append(f"OOS_SHARPE_BELOW_{STRICT_OOS_SHARPE_MIN:.2f}")
        if item["wf_positive_frac"] is None or item["wf_positive_frac"] < STRICT_WF_POSITIVE_FRAC:
            gate_reasons.append("WALK_FORWARD_POSITIVE_FRAC_BELOW_75PCT")
        if item["regime_positive_frac"] is None or item["regime_positive_frac"] < STRICT_REGIME_POSITIVE_FRAC:
            gate_reasons.append("REGIME_POSITIVE_FRAC_BELOW_75PCT")
        if item["pressure"]["worst_sharpe"] < STRICT_OOS_SHARPE_MIN:
            gate_reasons.append("COST_PRESSURE_SHARPE_FAILED")
        if item["pressure"]["worst_net_return"] <= 0:
            gate_reasons.append("COST_PRESSURE_NET_RETURN_NON_POSITIVE")
        if item["pressure"]["worst_max_drawdown"] > STRICT_MAX_DRAWDOWN:
            gate_reasons.append("COST_PRESSURE_MAX_DRAWDOWN_ABOVE_15PCT")
        hard_pass = not gate_reasons
        base = item["baseline"]
        result = {
            "candidate_id": cid, "family_id": spec.family_id,
            "status": "VALIDATED" if hard_pass else "REQUIRES_HUMAN_REVIEW",
            "paper_status": "NOT_RUN", "verdict": "PASS" if hard_pass else "REQUIRES_HUMAN_REVIEW",
            "gate_pass": hard_pass, "gate_reasons": gate_reasons,
            "dataset_version": dataset_version, "trial_count": trial_count,
            "catalog_var_sr": var_sr, "dsr": dsr, "pbo": pbo_value,
            "n_periods": len(item["periods"]), "mean_breadth": item["metrics"]["mean_breadth"],
            "mean_ic": item["metrics"]["mean_ic"], "icir": item["metrics"]["icir"],
            "ic_tstat": item["metrics"]["ic_tstat"], "ic_pvalue": item["metrics"]["ic_pvalue"],
            "oos_sharpe": item["oos_sharpe"], "fold_sharpes": item["fold_sharpes"],
            "wf_positive_frac": item["wf_positive_frac"], "regime_positive_frac": item["regime_positive_frac"],
            "net_return": base.total_net_return(), "gross_return": base.total_gross_return(),
            "net_sharpe": sharpe(list(base.period_returns), 52 if item["horizon"] == 7 else 365),
            "max_drawdown": max_drawdown(base.net_equity_curve()),
            "turnover": statistics.fmean(base.turnovers) if base.turnovers else 0.0,
            "cost_drag": sum(base.costs), "reconciliation_residual": base.reconciliation_residual(),
            "pressure": item["pressure"], "horizon_days": item["horizon"],
            "evaluation_frequency": spec.evaluation_frequency,
            "data_mode": "PIT_DAILY" if spec.evaluation_frequency == "daily" else "PIT_DAILY_PROXY",
        }
        # Paper is a lower-privilege observation mode. It may start after the
        # basic PIT/data gate and positive historical net result, but is never
        # called PAPER_READY until the independent forward clock has enough
        # realized periods. Daily-proxy rows are explicitly labelled.
        paper_allowed = bool(spec.paper_eligible and item["metrics"]["coverage_ok"]
                             and base.total_net_return() > 0 and item["pressure"]["worst_net_return"] > 0)
        if run_paper and paper_allowed:
            try:
                paper_metrics, run = _paper_shadow_run(cid, item["periods"], panel=panel, dry_run_db=dry_run_db)
                paper_status = "PAPER_ONLY_PENDING_FORWARD" if spec.evaluation_frequency == "daily" else "PAPER_ONLY_DAILY_PROXY"
                result["paper_status"] = paper_status
                result["paper"] = paper_metrics
                registry.record_paper_run(cid, run_id=run.run_id, status=paper_status,
                                          metrics=paper_metrics, dataset_version=dataset_version, recorded_at=now)
                paper_runs.append({"candidate_id": cid, **paper_metrics, "status": paper_status})
            except Exception as exc:
                result["paper_status"] = "PAPER_BLOCKED"
                result["paper_error"] = f"{type(exc).__name__}: {exc}"
        result_rows[cid] = result
        definition = definitions[cid]
        evaluation = AlphaEvaluation(
            alpha_id=cid, ic_mean=result["mean_ic"], icir=result["icir"], ic_tstat=result["ic_tstat"],
            ic_pvalue=result["ic_pvalue"], mean_breadth=result["mean_breadth"],
            n_periods=result["n_periods"], coverage_ok=item["metrics"]["coverage_ok"],
            net_sharpe=result["net_sharpe"] if _finite(result["net_sharpe"]) else 0.0,
            capacity_adj_sharpe=result["net_sharpe"] if _finite(result["net_sharpe"]) else 0.0,
            observed_sr=item["observed_sr"], turnover=result["turnover"],
            deflated_sharpe=max(0.0, min(1.0, float(dsr or 0.0))), pbo=max(0.0, min(1.0, float(pbo_value or 0.0))),
            catalog_trial_count=max(1, trial_count), verdict=(ValidationVerdict.PASS if hard_pass else ValidationVerdict.REQUIRES_HUMAN_REVIEW),
            reasons=tuple(gate_reasons), as_of=now,
            # The experiment registry treats a reproducibility hash as the
            # immutable evaluation identity.  Bind it to this run's evidence
            # timestamp so a later scan is a new artifact, never a mutable
            # overwrite of the prior dataset result.
            reproducibility_hash=stable_hash({"result": result, "as_of": now}),
        )
        exp_registry.record_evaluation(evaluation, dataset_version=dataset_version, recorded_ts=now,
                                       search_id=search_id, config_hash=definition.definition_hash)
        registry.record_evaluation(cid, result, dataset_version=dataset_version, recorded_at=now)

    exp_registry.finish_search_attempt(search_id, status="COMPLETED", recorded_at=now)
    exp_store.close()
    report = {
        "generated_at": now, "dataset_version": dataset_version,
        "panel": {"n_periods": len(panel.event_times), "n_instruments": len(panel.instruments),
                   "instruments": list(panel.instruments), "fields": sorted(panel.matrices)},
        "global_trial_count": trial_count, "catalog_var_sr": var_sr,
        "search_id": search_id, "family_count": len(specs),
        "candidates": [result_rows[s.candidate_id] for s in specs],
        "paper_runs": paper_runs, "paper_live_submission": False,
        "registry_db": os.path.relpath(family_db, ROOT), "dry_run_db": os.path.relpath(dry_run_db, ROOT),
        "registry_chain_valid": registry.verify_chain(), "event_count": registry.event_count(),
        "strict_gate": {"dsr_min": STRICT_DSR_MIN, "pbo_max": STRICT_PBO_MAX,
                        "oos_sharpe_min": STRICT_OOS_SHARPE_MIN,
                        "walk_forward_positive_frac": STRICT_WF_POSITIVE_FRAC,
                        "regime_positive_frac": STRICT_REGIME_POSITIVE_FRAC,
                        "max_drawdown": STRICT_MAX_DRAWDOWN},
        "safety": {"mode": "L2_DRY_RUN_ONLY", "live_trading_enabled": False,
                   "real_broker_enabled": False, "allow_order_submission": False},
    }
    registry.close()
    return report


def write_report(report: Mapping[str, Any], *, json_path: str, markdown_path: str) -> None:
    Path(json_path).parent.mkdir(parents=True, exist_ok=True)
    Path(markdown_path).parent.mkdir(parents=True, exist_ok=True)
    Path(json_path).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, default=str), encoding="utf-8")
    lines = [
        "# QUANTOS Alpha 家族自动发现与纸面观察",
        "",
        f"- 数据版本：`{report['dataset_version']}`",
        f"- PIT 面板：{report['panel']['n_instruments']} 个币种 × {report['panel']['n_periods']} 个日点",
        f"- 全局试验数：{report['global_trial_count']}（持久化，不按会话清零）",
        f"- 家族证据链：`{'PASS' if report['registry_chain_valid'] else 'BLOCK'}`，事件 {report['event_count']} 条",
        "- 运行模式：`L2_DRY_RUN_ONLY`，没有真实下单能力",
        "",
        "## 候选结果",
        "",
        "| 家族/候选 | 状态 | 净 Sharpe | 回撤 | DSR | PBO | 纸面状态 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in report["candidates"]:
        lines.append(
            f"| {row['family_id']}/{row['candidate_id']} | {row.get('status')} | "
            f"{row.get('net_sharpe','—') if row.get('net_sharpe') is not None else '—'} | "
            f"{row.get('max_drawdown','—') if row.get('max_drawdown') is not None else '—'} | "
            f"{row.get('dsr','—') if row.get('dsr') is not None else '—'} | "
            f"{row.get('pbo','—') if row.get('pbo') is not None else '—'} | {row.get('paper_status','—')} |"
        )
    lines += ["", "## 解释", "",
              "`PAPER_ONLY_PENDING_FORWARD` 只表示已通过数据/执行影子门并进入独立纸面观察，不能替代至少 8 个有效前向周期。",
              "`PIT_DAILY_PROXY` 明确表示目标是分钟/实时但当前只用日线代理；它不会被写成已验证的分钟 Alpha。",
              "缺少期权、跨所同步、真实期限结构或事件发布时间的数据，候选保持 `DATA_UNAVAILABLE`，不使用合成输入。",
              "",
              "## 纸面运行",
              ""]
    for row in report.get("paper_runs", []):
        lines.append(f"- `{row['candidate_id']}`: {row['status']}，run `{row['run_id']}`，周期 {row['n_periods']}，净收益 {row.get('total_return')}，残差 {row.get('reconciliation_residual')}。")
    Path(markdown_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(report: Mapping[str, Any], *, json_path: str, markdown_path: str) -> None:
    """写入可读的简体中文报告；JSON 保留完整、机器可审计的证据。"""
    Path(json_path).parent.mkdir(parents=True, exist_ok=True)
    Path(markdown_path).parent.mkdir(parents=True, exist_ok=True)
    Path(json_path).write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    lines = [
        "# QUANTOS Alpha 家族自动发现与纸面观察",
        "",
        f"- 数据版本：`{report['dataset_version']}`",
        f"- PIT 面板：{report['panel']['n_instruments']} 个币种 × {report['panel']['n_periods']} 个日点",
        f"- 全局试验数：{report['global_trial_count']}（持久化，不按会话清零）",
        f"- 家族证据链：`{'PASS' if report['registry_chain_valid'] else 'BLOCK'}`，事件 {report['event_count']} 条",
        "- 运行模式：`L2_DRY_RUN_ONLY`，无真实下单能力",
        "",
        "## 候选结果",
        "",
        "| 家族/候选 | 状态 | 净 Sharpe | 最大回撤 | DSR | PBO | 纸面状态 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in report["candidates"]:
        def fmt(value: Any) -> str:
            return "—" if value is None else str(value)

        lines.append(
            f"| {row['family_id']}/{row['candidate_id']} | {row.get('status')} | "
            f"{fmt(row.get('net_sharpe'))} | {fmt(row.get('max_drawdown'))} | "
            f"{fmt(row.get('dsr'))} | {fmt(row.get('pbo'))} | {fmt(row.get('paper_status'))} |"
        )
    lines.extend([
        "",
        "## 解释",
        "",
        "`PAPER_ONLY_PENDING_FORWARD` 表示已通过数据和执行影子门，但仍需至少 8 个有效前向周期；历史回放不能替代前向证据。",
        "`PAPER_ONLY_DAILY_PROXY` 明确表示目标是分钟/实时，但当前只使用日线代理，不得写成已验证的目标频率 Alpha。",
        "缺少期权、跨交易所、期限结构或事件时点输入时保持 `DATA_UNAVAILABLE`，不使用合成默认值。",
        "",
        "## 纸面运行",
        "",
    ])
    for row in report.get("paper_runs", []):
        lines.append(
            f"- `{row['candidate_id']}`：{row['status']}，run `{row['run_id']}`，"
            f"周期 {row['n_periods']}，净收益 {row.get('total_return')}，"
            f"对账残差 {row.get('reconciliation_residual')}。"
        )
    Path(markdown_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family-db", default=str(DEFAULT_FAMILY_DB))
    parser.add_argument("--experiment-db", default=str(DEFAULT_EXPERIMENT_DB))
    parser.add_argument("--dry-run-db", default=str(DEFAULT_DRY_RUN_DB))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--markdown", default=str(DEFAULT_MARKDOWN))
    parser.add_argument("--no-paper", action="store_true")
    args = parser.parse_args(argv)
    report = discover(family_db=args.family_db, experiment_db=args.experiment_db,
                      dry_run_db=args.dry_run_db, run_paper=not args.no_paper)
    write_report(report, json_path=args.report, markdown_path=args.markdown)
    for row in report["candidates"]:
        print(f"{row['candidate_id']}: {row.get('status')} paper={row.get('paper_status', '—')}")
    print(f"-> {args.report}")
    print(f"-> {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
