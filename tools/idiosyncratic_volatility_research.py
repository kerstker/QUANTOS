"""One-shot pre-registered crypto idiosyncratic-volatility experiment.

All five frozen candidates are constructed and data-preflighted before the
production experiment registry is opened.  The result is an L2 historical
diagnostic only; this tool has no promotion, broker, credential, order,
testnet, or live authority.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import math
from pathlib import Path
import re
import statistics
import time
from typing import Any, Mapping, Sequence

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.cost import CostModel
from quantos.contracts.hashing import stable_hash
from services.alpha.config import AlphaFactoryConfig
from services.alpha.panel_backtest import PanelPeriod, run_panel_backtest
from services.intelligence.registered_candidate_batch import run_registered_candidate_batch
from services.research.idiosyncratic_volatility import (
    ForcedExitEvent,
    IvolDataIntegrityError,
    IvolDecisionPlan,
    IvolExperimentConfig,
    adverse_terminal_stress_periods,
    build_ivol_periods,
    find_contiguous_sample_stop_row,
    find_first_eligible_decision_row,
    ivol_signal_leakage_ok,
)
from services.research.research_budget import ResearchBudget
from tools.cache_io import atomic_write_json
from tools.ic_panel_registry import _build as _build_pit_panel, _load_json
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
    stored_search_evaluation,
)


SOURCE = "tools.idiosyncratic_volatility_research"
SEARCH_KIND = "CRYPTO_IDIOSYNCRATIC_VOLATILITY_V1"
DATA_SOURCE = "crypto_funding.db"
PERIODS_PER_YEAR = 52
QUANTILE = 0.25
TARGET_AUM = 1_000_000.0
MAX_PARTICIPATION = 0.10
TERMINAL_ADVERSE_RETURN = 0.20
BASE_COST = CostModel(fee_bps=4.0, slippage_bps=4.0, impact_coef=20.0)
DOUBLE_COST_IMPACT = CostModel(
    fee_bps=8.0,
    slippage_bps=8.0,
    impact_coef=40.0,
)
IVOL_CONFIG = IvolExperimentConfig()
FAMILY = "crypto_cross_sectional_idiosyncratic_volatility_v1"
PRIMARY_ALIAS = "ivol_high_residual_v1"
LIQUID_ALIAS = "ivol_high_residual_liquid_top30_v1"
_COMMIT = re.compile(r"[0-9a-f]{40}")

ECONOMIC_HYPOTHESIS = (
    "Among contemporaneously liquid crypto perpetual contracts, high trailing "
    "market-residual volatility predicts positive next-week cross-sectional "
    "returns distinct from total volatility and robust to costs and terminal "
    "contract events."
)

EVALUATION_CONTRACT = {
    "schema": "crypto_idiosyncratic_volatility_contract.v1",
    "source_paper": {
        "title": "Is idiosyncratic volatility priced in cryptocurrency markets?",
        "authors": ("Wei Zhang", "Yingying Li"),
        "journal": "Research in International Business and Finance",
        "year": 2020,
        "doi": "10.1016/j.ribaf.2020.101252",
        "portability": "DIRECT_CRYPTO_PEER_REVIEWED",
    },
    "source_translation": (
        "Daily Binance perpetual closes replace the paper's broader crypto "
        "sample; the experiment therefore tests, rather than assumes, portability."
    ),
    "return_lookback_rows": IVOL_CONFIG.return_lookback_rows,
    "return_window": "28 completed daily returns ending at decision-minus-1",
    "market_factor": "equal_weight_return_of_decision_time_liquid_universe",
    "regression": "per_asset_OLS_with_intercept_and_one_market_factor",
    "ivol_measure": "population_std_of_28_daily_OLS_residuals",
    "liquidity_measure": "median(close_times_base_volume)_over_prior_28_rows",
    "sample_start_rule": "earliest_decision_with_60_complete_trailing_names",
    "sample_end_rule": (
        "exclusive_first_scheduled_decision_with_fewer_than_60_complete_names"
    ),
    "holding_rows": IVOL_CONFIG.holding_rows,
    "rebalance": "every_7_rows_non_overlapping_no_skips",
    "universe_size": IVOL_CONFIG.universe_size,
    "liquid_size": IVOL_CONFIG.liquid_size,
    "primary_signal": "high_idiosyncratic_volatility",
    "label": "close_t_plus_7_div_close_t_minus_1_or_terminal_forced_exit",
    "terminal_exit_policy": {
        "classification": (
            "missing t+7 is terminal only when no later positive close exists; "
            "otherwise reject as a non-terminal data gap"
        ),
        "base": "force_exit_at_last_positive_close_in_decision_to_t_plus_7",
        "no_post_decision_trade": "REJECT_DATA_INTEGRITY",
        "adverse_stress_return": TERMINAL_ADVERSE_RETURN,
        "stress_direction": "subtract_from_long_return_add_to_short_return",
    },
    "periods_per_year": PERIODS_PER_YEAR,
    "quantile": QUANTILE,
    "target_aum_usd": TARGET_AUM,
    "max_participation": MAX_PARTICIPATION,
    "cost": asdict(BASE_COST),
    "double_cost_impact": asdict(DOUBLE_COST_IMPACT),
    "candidate_aliases": (
        PRIMARY_ALIAS,
        "ivol_low_sign_control_v1",
        "ivol_total_vol_control_v1",
        "ivol_hash_placebo_v1",
        LIQUID_ALIAS,
    ),
    "parameter_scan": False,
    "historical_diagnostic_only": True,
    "historical_pass_is_not_forward_or_live_authority": True,
    "decision_bars": {
        "full_evaluator_evidence": True,
        "evaluator_verdict": "PASS",
        "min_net_sharpe": 1.0,
        "min_net_return": 0.0,
        "min_dsr": 0.95,
        "max_pbo": 0.25,
        "max_drawdown": 0.15,
        "suffix_diagnostic_fraction": 0.20,
        "min_suffix_sharpe": 0.80,
        "min_suffix_return": 0.0,
        "double_cost_impact_positive": True,
        "terminal_adverse_stress_positive": True,
        "max_participation_at_most": MAX_PARTICIPATION,
        "must_beat_sign_control": True,
        "must_beat_hash_control": True,
        "must_beat_total_volatility_control": True,
        "liquid_subset_min_sharpe": 0.50,
        "liquid_subset_min_return": 0.0,
    },
}
CONTRACT_HASH = stable_hash(EVALUATION_CONTRACT)

_CANDIDATE_SPECS = (
    (PRIMARY_ALIAS, "idiosyncratic", IVOL_CONFIG.universe_size, "PRIMARY"),
    (
        "ivol_low_sign_control_v1",
        "low_idiosyncratic",
        IVOL_CONFIG.universe_size,
        "SIGN_CONTROL",
    ),
    (
        "ivol_total_vol_control_v1",
        "total",
        IVOL_CONFIG.universe_size,
        "TOTAL_VOLATILITY_CONTROL",
    ),
    (
        "ivol_hash_placebo_v1",
        "hash",
        IVOL_CONFIG.universe_size,
        "HASH_PLACEBO",
    ),
    (LIQUID_ALIAS, "idiosyncratic", IVOL_CONFIG.liquid_size, "INVESTABILITY_CONTROL"),
)


def candidate_definitions() -> tuple[tuple[AlphaDefinition, str, int, str], ...]:
    definitions = []
    for alias, mode, universe_size, role in _CANDIDATE_SPECS:
        params = {
            "contract_hash": CONTRACT_HASH,
            "family": FAMILY,
            "mode": mode,
            "universe_size": str(universe_size),
        }
        seed = AlphaDefinition(
            alpha_id=alias,
            name=alias,
            factor_ids=("market_residual_volatility_28d", "trailing_dollar_adv"),
            universe=f"binance_usdm_trailing_adv_top{universe_size}",
            params=params,
        )
        definition = AlphaDefinition(
            alpha_id=f"{alias}_{seed.definition_hash[:16]}",
            name=alias,
            factor_ids=seed.factor_ids,
            universe=seed.universe,
            params=params,
        )
        definitions.append((definition, mode, universe_size, role))
    return tuple(definitions)


def _research_panel():
    instruments = sorted(_load_json("crypto_funding_snapshot.json")["instruments"])
    return _build_pit_panel(
        DATA_SOURCE,
        instruments,
        fields=("close", "volume"),
    )


def _sharpe(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    sigma = statistics.pstdev(values)
    return (
        0.0
        if sigma == 0.0
        else statistics.fmean(values) / sigma * math.sqrt(PERIODS_PER_YEAR)
    )


def _max_drawdown(values: Sequence[float]) -> float:
    equity = peak = 1.0
    worst = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = max(worst, 1.0 - equity / peak)
    return worst


def _replay(periods: Sequence[PanelPeriod], cost: CostModel) -> dict[str, Any]:
    result = run_panel_backtest(
        periods,
        cost=cost,
        quantile=QUANTILE,
        target_aum=TARGET_AUM,
    )
    returns = result.period_returns
    participation_assessed = bool(result.max_participations) and all(
        value is not None for value in result.max_participations
    )
    max_participation = (
        max(float(value) for value in result.max_participations if value is not None)
        if participation_assessed
        else None
    )
    return {
        "n_periods": len(returns),
        "net_sharpe": _sharpe(returns),
        "net_return": math.prod(1.0 + value for value in returns) - 1.0,
        "max_drawdown": _max_drawdown(returns),
        "mean_turnover": (
            statistics.fmean(result.turnovers) if result.turnovers else 0.0
        ),
        "total_cost": math.fsum(result.costs),
        "participation_assessed": participation_assessed,
        "max_participation": max_participation,
        "reconciliation_residual": result.reconciliation_residual(),
        "period_returns": list(returns),
    }


def judge(
    evaluations: Mapping[str, Mapping[str, Any]],
    replays: Mapping[str, Mapping[str, Any]],
    aliases_to_ids: Mapping[str, str],
    holdout: Mapping[str, Any],
) -> dict[str, Any]:
    primary_id = aliases_to_ids[PRIMARY_ALIAS]
    evaluation = evaluations.get(primary_id) or {}
    primary = replays[primary_id]["base_cost"]
    stressed_cost = replays[primary_id]["double_cost_impact"]
    stressed_terminal = replays[primary_id]["terminal_adverse_stress"]
    sign = replays[aliases_to_ids["ivol_low_sign_control_v1"]]["base_cost"]
    total = replays[aliases_to_ids["ivol_total_vol_control_v1"]]["base_cost"]
    hashed = replays[aliases_to_ids["ivol_hash_placebo_v1"]]["base_cost"]
    liquid = replays[aliases_to_ids[LIQUID_ALIAS]]["base_cost"]
    participation = primary.get("max_participation")
    checks = {
        "immutable_evaluation_verified": evaluation.get("verified") is True,
        "full_evidence_verified": evaluation.get("full_evidence_verified") is True,
        "evaluator_verdict_pass": evaluation.get("verdict") == "PASS",
        "net_sharpe_at_least_1": primary["net_sharpe"] >= 1.0,
        "net_return_positive": primary["net_return"] > 0.0,
        "dsr_at_least_0_95": float(evaluation.get("deflated_sharpe") or 0.0) >= 0.95,
        "pbo_at_most_0_25": float(evaluation.get("pbo") or 1.0) <= 0.25,
        "max_drawdown_at_most_0_15": primary["max_drawdown"] <= 0.15,
        "suffix_sharpe_at_least_0_80": holdout["net_sharpe"] >= 0.80,
        "suffix_return_positive": holdout["net_return"] > 0.0,
        "double_cost_impact_positive": stressed_cost["net_return"] > 0.0,
        "terminal_adverse_stress_positive": stressed_terminal["net_return"] > 0.0,
        "participation_assessed": participation is not None,
        "max_participation_at_most_10pct": (
            participation is not None and float(participation) <= MAX_PARTICIPATION
        ),
        "beats_sign_control": primary["net_sharpe"] > sign["net_sharpe"],
        "beats_hash_control": primary["net_sharpe"] > hashed["net_sharpe"],
        "beats_total_volatility_control": primary["net_sharpe"] > total["net_sharpe"],
        "liquid_subset_sharpe_at_least_0_50": liquid["net_sharpe"] >= 0.50,
        "liquid_subset_return_positive": liquid["net_return"] > 0.0,
    }
    formal_only = {
        "immutable_evaluation_verified",
        "full_evidence_verified",
        "evaluator_verdict_pass",
        "dsr_at_least_0_95",
        "pbo_at_most_0_25",
    }
    economic_pass = all(
        passed for name, passed in checks.items() if name not in formal_only
    )
    formal_pass = all(checks.values())
    return {
        "status": (
            "HISTORICAL_DIAGNOSTIC_PASS" if formal_pass else "REJECTED_HISTORICAL"
        ),
        "economic_candidate": economic_pass,
        "formal_pass": formal_pass,
        "failed_bars": [name for name, passed in checks.items() if not passed],
        "checks": checks,
        "forward_admission_allowed": False,
        "testnet_eligible": False,
        "live_eligible": False,
    }


def _plan_payload(plan: IvolDecisionPlan, panel) -> dict[str, Any]:
    return {
        "decision_time": int(panel.event_times[plan.decision_row]),
        "lookback_start_time": int(panel.event_times[plan.lookback_start]),
        "lookback_stop_time": int(panel.event_times[plan.lookback_stop - 1]),
        "universe_size": plan.universe_size,
        "ranked_universe": [
            {
                "instrument": instrument,
                "trailing_median_adv": adv,
                "idiosyncratic_volatility": ivol,
                "total_volatility": total,
            }
            for instrument, adv, ivol, total in zip(
                plan.ranked_universe,
                plan.trailing_median_adv,
                plan.idiosyncratic_volatility,
                plan.total_volatility,
                strict=True,
            )
        ],
    }


def _forced_payload(event: ForcedExitEvent, panel) -> dict[str, Any]:
    return {
        "instrument": event.instrument,
        "decision_time": int(panel.event_times[event.decision_row]),
        "label_time": int(panel.event_times[event.label_row]),
        "forced_exit_time": int(panel.event_times[event.exit_row]),
        "exit_adv_notional": event.exit_adv_notional,
        "reason": "TERMINAL_CONTRACT_WITHIN_HOLDING_WINDOW",
    }


def _record_preflight_rejection(
    *,
    output: Path,
    panel,
    dataset_version: str,
    generated_at_ms: int,
    source_commit: str,
    start_row: int,
    failure: IvolDataIntegrityError,
) -> dict[str, Any]:
    document = {
        "schema_version": "crypto_idiosyncratic_volatility_preflight_rejection.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": generated_at_ms,
        "dataset_observed_through_ms": int(panel.event_times[-1]),
        "source_commit": source_commit,
        "preflight_id": "preflight-"
        + stable_hash(
            {
                "contract_hash": CONTRACT_HASH,
                "dataset_version": dataset_version,
                "failure": {
                    "reason_code": failure.reason_code,
                    "instrument": failure.instrument,
                    "decision_row": failure.decision_row,
                    "label_row": failure.label_row,
                },
                "source_commit": source_commit,
            }
        )[:24],
        "search_id": None,
        "search_kind": SEARCH_KIND,
        "dataset_version": dataset_version,
        "contract_hash": CONTRACT_HASH,
        "evaluation_contract": EVALUATION_CONTRACT,
        "economic_hypothesis": ECONOMIC_HYPOTHESIS,
        "sample_start": {
            "row": start_row,
            "event_time": int(panel.event_times[start_row]),
            "rule": EVALUATION_CONTRACT["sample_start_rule"],
        },
        "preflight_failure": {
            "reason_code": failure.reason_code,
            "instrument": failure.instrument,
            "decision_time": int(panel.event_times[failure.decision_row]),
            "label_time": int(panel.event_times[failure.label_row]),
            "detail": str(failure),
            "policy": "FAIL_CLOSED_NO_SKIP_NO_IMPUTATION_NO_REGISTRATION",
        },
        "return_statistics_computed": False,
        "trial_registry": {
            "mutation_attempted": False,
            "registered_candidate_count": 0,
            "recorded_run_ids": [],
        },
        "decision": {
            "status": "REJECTED_DATA_INTEGRITY",
            "economic_candidate": False,
            "formal_pass": False,
            "failed_bars": ["complete_selected_holding_returns"],
            "forward_admission_allowed": False,
            "testnet_eligible": False,
            "live_eligible": False,
        },
        "safety": {
            "public_historical_market_data_only": True,
            "credentials": False,
            "broker_connectivity": False,
            "order_submission": False,
            "registry_mutation": False,
            "human_approval_still_required": True,
        },
    }
    atomic_write_json(str(output), document, indent=2)
    return document


def run(
    *,
    output: str | Path,
    source_commit: str,
    recover_search_id: str | None = None,
) -> dict[str, Any]:
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite immutable result: {output}")
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source_commit must be a lowercase 40-character git commit")

    panel = _research_panel()
    dataset_version = field_panel_dataset_version(
        panel,
        source_versions={"contract_hash": CONTRACT_HASH, "source": DATA_SOURCE},
    )
    generated_at_ms = time.time_ns() // 1_000_000
    definitions = candidate_definitions()
    start_row = find_first_eligible_decision_row(panel, IVOL_CONFIG)
    stop_row = find_contiguous_sample_stop_row(panel, start_row, IVOL_CONFIG)
    built: dict[
        str,
        tuple[
            tuple[PanelPeriod, ...],
            tuple[IvolDecisionPlan, ...],
            tuple[ForcedExitEvent, ...],
        ],
    ] = {}
    try:
        for definition, mode, universe_size, _role in definitions:
            built[definition.alpha_id] = build_ivol_periods(
                panel,
                IVOL_CONFIG,
                mode=mode,
                universe_size=universe_size,
                start_row=start_row,
                stop_row=stop_row,
            )
    except IvolDataIntegrityError as failure:
        return _record_preflight_rejection(
            output=output,
            panel=panel,
            dataset_version=dataset_version,
            generated_at_ms=generated_at_ms,
            source_commit=source_commit,
            start_row=start_row,
            failure=failure,
        )

    candidates = []
    periods_by_id: dict[str, tuple[PanelPeriod, ...]] = {}
    plans_by_size: dict[int, tuple[IvolDecisionPlan, ...]] = {}
    forced_by_id: dict[str, tuple[ForcedExitEvent, ...]] = {}
    leakage: dict[str, bool | None] = {}
    mapping: dict[str, dict[str, Any]] = {}
    for definition, mode, universe_size, role in definitions:
        periods, plans, forced = built[definition.alpha_id]
        candidates.append((definition, periods))
        periods_by_id[definition.alpha_id] = periods
        plans_by_size.setdefault(universe_size, plans)
        forced_by_id[definition.alpha_id] = forced
        key = f"{mode}:top{universe_size}"
        leakage.setdefault(
            key,
            ivol_signal_leakage_ok(
                panel,
                IVOL_CONFIG,
                mode=mode,
                universe_size=universe_size,
                start_row=start_row,
                stop_row=stop_row,
            ),
        )
        mapping[definition.name] = {
            "alpha_id": definition.alpha_id,
            "definition_hash": definition.definition_hash,
            "mode": mode,
            "universe_size": universe_size,
            "role": role,
        }
    evidence = {
        definition.alpha_id: {
            "leakage_ok": leakage[f"{mode}:top{universe_size}"]
        }
        for definition, mode, universe_size, _role in definitions
    }
    aliases_to_ids = {alias: item["alpha_id"] for alias, item in mapping.items()}
    budget = ResearchBudget(
        max_trials=5,
        max_feature_count=2,
        max_parameter_dimensions=4,
        allowed_data_sources=(DATA_SOURCE,),
        stopping_rules={"max_trials": 5},
    )
    with production_experiment_registry() as registry:
        if recover_search_id is None:
            recorded_at = generated_at_ms
            search_id = make_search_id(
                source=SOURCE,
                search_kind=SEARCH_KIND,
                dataset_version=dataset_version,
                recorded_at=recorded_at,
                params={"contract_hash": CONTRACT_HASH, "source_commit": source_commit},
            )
            trial_count_before = registry._store.trial_count()
            batch = run_registered_candidate_batch(
                candidates,
                registry=registry,
                search_id=search_id,
                search_kind=SEARCH_KIND,
                search_source=SOURCE,
                dataset_version=dataset_version,
                recorded_at=recorded_at,
                periods_per_year=PERIODS_PER_YEAR,
                quantile=QUANTILE,
                target_aum=TARGET_AUM,
                config=AlphaFactoryConfig(compute_regime_evidence=True),
                cost=BASE_COST,
                evidence=evidence,
                budget=budget,
                parent_hypothesis=ECONOMIC_HYPOTHESIS,
                search_space=(
                    "Exactly five frozen candidates: high residual-volatility "
                    "primary, exact sign control, total-volatility control, fixed "
                    "hash placebo, and top-30 liquid investability control. No "
                    "window, horizon, universe, quantile, or cost scan."
                ),
                data_source=DATA_SOURCE,
                uses_locked_oos=False,
                hypothesis_by_candidate={
                    definition.alpha_id: stable_hash(
                        {"hypothesis": ECONOMIC_HYPOTHESIS, "role": role}
                    )
                    for definition, _mode, _size, role in definitions
                },
                code_hash=source_commit,
            )
            recovered = False
        else:
            search_id = str(recover_search_id)
            identity = registry.search_identity(search_id)
            expected = {
                "search_kind": SEARCH_KIND,
                "source": SOURCE,
                "dataset_version": dataset_version,
            }
            if any(identity.get(key) != value for key, value in expected.items()):
                raise RuntimeError("recovery search identity does not match frozen experiment")
            events = registry.search_attempt_events(search_id)
            starts = [event for event in events if event.get("status") == "STARTED"]
            if len(starts) != 1 or not events or events[-1].get("status") != "COMPLETED":
                raise RuntimeError("recovery requires exactly one completed search attempt")
            recorded_at = int(starts[0]["recorded_at"])
            expected_search_id = make_search_id(
                source=SOURCE,
                search_kind=SEARCH_KIND,
                dataset_version=dataset_version,
                recorded_at=recorded_at,
                params={"contract_hash": CONTRACT_HASH, "source_commit": source_commit},
            )
            if expected_search_id != search_id:
                raise RuntimeError("recovery search id is not bound to commit and contract")
            expected_hashes = {
                definition.definition_hash for definition, *_rest in definitions
            }
            observed_hashes = {
                str(row.get("config_hash", ""))
                for row in registry.search_candidates(search_id)
            }
            if observed_hashes != expected_hashes:
                raise RuntimeError("recovery candidate set differs from frozen family")
            links = registry.search_evaluations(search_id)
            run_ids = [str(link.get("run_id", "")) for link in links]
            if len(run_ids) != 5 or len(set(run_ids)) != 5:
                raise RuntimeError("recovery search does not have exactly five evaluations")
            batch = {
                "recorded_run_ids": run_ids,
                "registered_candidate_count": len(observed_hashes),
                "budget_declaration": None,
            }
            trial_count_before = None
            recovered = True
        definition_by_id = {
            definition.alpha_id: definition for definition, *_rest in definitions
        }
        evaluations = dict(
            stored_search_evaluation(
                registry,
                search_id,
                str(run_id),
                definition_by_id,
            )
            for run_id in batch.get("recorded_run_ids", ())
        )
        trial_count_after = registry._store.trial_count()

    replays = {}
    for alpha_id, periods in periods_by_id.items():
        terminal_stress = adverse_terminal_stress_periods(
            periods,
            forced_by_id[alpha_id],
            quantile=QUANTILE,
            adverse_return=TERMINAL_ADVERSE_RETURN,
        )
        replays[alpha_id] = {
            "base_cost": _replay(periods, BASE_COST),
            "double_cost_impact": _replay(periods, DOUBLE_COST_IMPACT),
            "terminal_adverse_stress": _replay(terminal_stress, BASE_COST),
        }
    primary_id = aliases_to_ids[PRIMARY_ALIAS]
    primary_periods = periods_by_id[primary_id]
    suffix_count = max(1, math.ceil(len(primary_periods) * 0.20))
    suffix_start = len(primary_periods) - suffix_count
    suffix = _replay(primary_periods[suffix_start:], BASE_COST)
    decision = judge(evaluations, replays, aliases_to_ids, suffix)

    document = {
        "schema_version": "crypto_idiosyncratic_volatility_research.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": generated_at_ms,
        "search_recorded_at_ms": recorded_at,
        "dataset_observed_through_ms": int(panel.event_times[-1]),
        "source_commit": source_commit,
        "search_id": search_id,
        "search_kind": SEARCH_KIND,
        "dataset_version": dataset_version,
        "contract_hash": CONTRACT_HASH,
        "evaluation_contract": EVALUATION_CONTRACT,
        "economic_hypothesis": ECONOMIC_HYPOTHESIS,
        "sample_start": {
            "row": start_row,
            "event_time": int(panel.event_times[start_row]),
            "rule": EVALUATION_CONTRACT["sample_start_rule"],
        },
        "sample_end": {
            "exclusive_row": stop_row,
            "first_excluded_event_time": int(panel.event_times[stop_row]),
            "last_included_decision_time": int(
                panel.event_times[stop_row - IVOL_CONFIG.holding_rows]
            ),
            "rule": EVALUATION_CONTRACT["sample_end_rule"],
            "later_recovery_not_reentered": True,
        },
        "trial_registry": {
            "trial_count_before": trial_count_before,
            "trial_count_after": trial_count_after,
            "trial_count_delta": (
                None
                if trial_count_before is None
                else trial_count_after - trial_count_before
            ),
            "recovered_from_completed_search": recovered,
            "registered_candidate_count": batch.get("registered_candidate_count"),
            "recorded_run_ids": list(batch.get("recorded_run_ids", ())),
            "budget_declaration": batch.get("budget_declaration"),
        },
        "leakage_evidence": leakage,
        "candidates": {
            alias: {
                **meta,
                "evaluation": evaluations.get(meta["alpha_id"]),
                "replays": replays[meta["alpha_id"]],
                "forced_exits": [
                    _forced_payload(event, panel)
                    for event in forced_by_id[meta["alpha_id"]]
                ],
            }
            for alias, meta in mapping.items()
        },
        "suffix_diagnostic": {
            "not_claimed_locked_or_blind": True,
            "period_count": suffix_count,
            "period_start_index": suffix_start,
            "metrics": suffix,
        },
        "decision_plans": {
            f"top{universe_size}": [_plan_payload(plan, panel) for plan in plans]
            for universe_size, plans in plans_by_size.items()
        },
        "decision": decision,
        "safety": {
            "public_historical_market_data_only": True,
            "credentials": False,
            "broker_connectivity": False,
            "order_submission": False,
            "historical_pass_is_not_forward_evidence": True,
            "human_approval_still_required": True,
        },
    }
    atomic_write_json(str(output), document, indent=2)
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--recover-search-id")
    args = parser.parse_args()
    result = run(
        output=args.output,
        source_commit=args.source_commit,
        recover_search_id=args.recover_search_id,
    )
    print(
        stable_hash(
            {
                "artifact": str(args.output),
                "decision": result.get("decision"),
                "search_id": result.get("search_id"),
            }
        )
    )


if __name__ == "__main__":
    main()


__all__ = [
    "CONTRACT_HASH",
    "EVALUATION_CONTRACT",
    "LIQUID_ALIAS",
    "PRIMARY_ALIAS",
    "candidate_definitions",
    "judge",
    "run",
]
