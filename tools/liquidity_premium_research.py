"""One-shot pre-registered cross-sectional crypto liquidity-premium experiment.

The complete four-candidate family is registered before evaluation. Results are
historical diagnostics only: this tool has no promotion, broker, credential,
order, testnet, or live authority.
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
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.intelligence.registered_candidate_batch import run_registered_candidate_batch
from services.research.liquidity_premium import (
    LiquidityDataIntegrityError,
    LiquidityDecisionPlan,
    LiquidityExperimentConfig,
    build_liquidity_periods,
    find_first_eligible_decision_row,
    liquidity_signal_leakage_ok,
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


SOURCE = "tools.liquidity_premium_research"
SEARCH_KIND = "LIQUIDITY_PREMIUM_V1"
DATA_SOURCE = "crypto_funding.db"
PERIODS_PER_YEAR = 52
QUANTILE = 0.25
TARGET_AUM = 1_000_000.0
MAX_PARTICIPATION = 0.10
BASE_COST = CostModel(fee_bps=4.0, slippage_bps=4.0, impact_coef=20.0)
DOUBLE_COST_IMPACT = CostModel(
    fee_bps=8.0,
    slippage_bps=8.0,
    impact_coef=40.0,
)
LIQUIDITY_CONFIG = LiquidityExperimentConfig()
FAMILY = "trailing_dollar_adv_liquidity_premium_v1"
PRIMARY_ALIAS = "liquidity_illiquidity_premium_v1"
LARGE_CAP_ALIAS = "liquidity_large_cap_illiquidity_v1"
_COMMIT = re.compile(r"[0-9a-f]{40}")

ECONOMIC_HYPOTHESIS = (
    "Within a contemporaneously investable crypto universe, relatively lower "
    "trailing dollar liquidity earns a premium that survives conservative "
    "transaction impact and participation limits."
)

EVALUATION_CONTRACT = {
    "schema": "liquidity_premium_contract.v1",
    "source_paper": {
        "title": "Liquidity and Expected Returns: Lessons from Emerging Markets",
        "doi": "10.1093/rfs/hhm030",
        "portability": "CROSS_ASSET_PRIOR_NOT_CRYPTO_EVIDENCE",
    },
    "source_translation": (
        "Trailing median Binance quote notional is a crypto execution-liquidity "
        "proxy, not the paper's equity zero-return measure."
    ),
    "lookback_rows": LIQUIDITY_CONFIG.lookback_rows,
    "lookback_interval": "[decision-28,decision), excludes decision-day volume",
    "sample_start_rule": "earliest_decision_with_48_complete_trailing_names",
    "holding_rows": LIQUIDITY_CONFIG.holding_rows,
    "rebalance": "every_7_rows_non_overlapping_no_skips",
    "universe_size": LIQUIDITY_CONFIG.universe_size,
    "large_cap_size": LIQUIDITY_CONFIG.large_cap_size,
    "liquidity_measure": "median(close_times_base_volume)_over_prior_28_rows",
    "primary_signal": "negative_log_trailing_median_dollar_adv",
    "label": "close_t_plus_7_div_close_t_minus_1",
    "periods_per_year": PERIODS_PER_YEAR,
    "quantile": QUANTILE,
    "target_aum_usd": TARGET_AUM,
    "max_participation": MAX_PARTICIPATION,
    "cost": asdict(BASE_COST),
    "double_cost_impact": asdict(DOUBLE_COST_IMPACT),
    "candidate_aliases": (
        PRIMARY_ALIAS,
        "liquidity_liquid_sign_control_v1",
        "liquidity_hash_placebo_v1",
        LARGE_CAP_ALIAS,
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
        "holdout_fraction": 0.20,
        "min_holdout_sharpe": 0.80,
        "min_holdout_return": 0.0,
        "double_cost_impact_positive": True,
        "max_participation_at_most": MAX_PARTICIPATION,
        "must_beat_sign_control": True,
        "must_beat_hash_control": True,
        "large_cap_min_sharpe": 0.50,
        "large_cap_min_return": 0.0,
    },
}
CONTRACT_HASH = stable_hash(EVALUATION_CONTRACT)

_CANDIDATE_SPECS = (
    (
        PRIMARY_ALIAS,
        "illiquidity",
        LIQUIDITY_CONFIG.universe_size,
        "PRIMARY",
    ),
    (
        "liquidity_liquid_sign_control_v1",
        "liquidity",
        LIQUIDITY_CONFIG.universe_size,
        "SIGN_CONTROL",
    ),
    (
        "liquidity_hash_placebo_v1",
        "hash",
        LIQUIDITY_CONFIG.universe_size,
        "HASH_PLACEBO",
    ),
    (
        LARGE_CAP_ALIAS,
        "illiquidity",
        LIQUIDITY_CONFIG.large_cap_size,
        "INVESTABILITY_CONTROL",
    ),
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
            factor_ids=("trailing_median_dollar_adv", "liquidity_cross_section"),
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


def _plan_payload(plan: LiquidityDecisionPlan, panel) -> dict[str, Any]:
    return {
        "decision_time": int(panel.event_times[plan.decision_row]),
        "lookback_start_time": int(panel.event_times[plan.lookback_start]),
        "lookback_stop_time": int(panel.event_times[plan.lookback_stop - 1]),
        "universe_size": plan.universe_size,
        "ranked_universe": [
            {"instrument": instrument, "trailing_median_adv": adv}
            for instrument, adv in zip(
                plan.ranked_universe,
                plan.trailing_median_adv,
                strict=True,
            )
        ],
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
    stressed = replays[primary_id]["double_cost_impact"]
    sign = replays[aliases_to_ids["liquidity_liquid_sign_control_v1"]][
        "base_cost"
    ]
    hashed = replays[aliases_to_ids["liquidity_hash_placebo_v1"]]["base_cost"]
    large_cap = replays[aliases_to_ids[LARGE_CAP_ALIAS]]["base_cost"]
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
        "holdout_sharpe_at_least_0_80": holdout["net_sharpe"] >= 0.80,
        "holdout_return_positive": holdout["net_return"] > 0.0,
        "double_cost_impact_positive": stressed["net_return"] > 0.0,
        "participation_assessed": participation is not None,
        "max_participation_at_most_10pct": (
            participation is not None and float(participation) <= MAX_PARTICIPATION
        ),
        "beats_sign_control": primary["net_sharpe"] > sign["net_sharpe"],
        "beats_hash_control": primary["net_sharpe"] > hashed["net_sharpe"],
        "large_cap_sharpe_at_least_0_50": large_cap["net_sharpe"] >= 0.50,
        "large_cap_return_positive": large_cap["net_return"] > 0.0,
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
            "HISTORICAL_DIAGNOSTIC_PASS"
            if formal_pass
            else "REJECTED_HISTORICAL"
        ),
        "economic_candidate": economic_pass,
        "formal_pass": formal_pass,
        "failed_bars": [name for name, passed in checks.items() if not passed],
        "checks": checks,
        "forward_admission_allowed": False,
        "testnet_eligible": False,
        "live_eligible": False,
    }


def _record_preflight_rejection(
    *,
    output: Path,
    panel,
    dataset_version: str,
    generated_at_ms: int,
    source_commit: str,
    recovery_tool_commit: str,
    start_row: int,
    failure: LiquidityDataIntegrityError,
) -> dict[str, Any]:
    """Persist a data-integrity rejection without opening the trial registry."""

    document = {
        "schema_version": "liquidity_premium_preflight_rejection.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": generated_at_ms,
        "dataset_observed_through_ms": int(panel.event_times[-1]),
        "source_commit": source_commit,
        "recovery_tool_commit": recovery_tool_commit,
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
            "decision_row": failure.decision_row,
            "decision_time": int(panel.event_times[failure.decision_row]),
            "label_row": failure.label_row,
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
    recovery_tool_commit: str | None = None,
) -> dict[str, Any]:
    quarantine_legacy_backtest("tools.liquidity_premium_research.run")
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite immutable result: {output}")
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source_commit must be a lowercase 40-character git commit")
    if recovery_tool_commit is not None and (
        not isinstance(recovery_tool_commit, str)
        or _COMMIT.fullmatch(recovery_tool_commit) is None
    ):
        raise ValueError("recovery_tool_commit must identify audited recovery code")
    if recover_search_id is not None and recovery_tool_commit is None:
        raise ValueError("recover_search_id requires recovery_tool_commit")

    panel = _research_panel()
    dataset_version = field_panel_dataset_version(
        panel,
        source_versions={"contract_hash": CONTRACT_HASH, "source": DATA_SOURCE},
    )
    artifact_generated_at = time.time_ns() // 1_000_000
    definitions = candidate_definitions()
    start_row = find_first_eligible_decision_row(panel, LIQUIDITY_CONFIG)
    built_inputs: dict[
        str,
        tuple[tuple[PanelPeriod, ...], tuple[LiquidityDecisionPlan, ...]],
    ] = {}
    try:
        for definition, mode, universe_size, _role in definitions:
            built_inputs[definition.alpha_id] = build_liquidity_periods(
                panel,
                LIQUIDITY_CONFIG,
                mode=mode,
                universe_size=universe_size,
                start_row=start_row,
            )
    except LiquidityDataIntegrityError as failure:
        if recovery_tool_commit is None:
            raise
        return _record_preflight_rejection(
            output=output,
            panel=panel,
            dataset_version=dataset_version,
            generated_at_ms=artifact_generated_at,
            source_commit=source_commit,
            recovery_tool_commit=recovery_tool_commit,
            start_row=start_row,
            failure=failure,
        )
    if recover_search_id is None and recovery_tool_commit is not None:
        raise ValueError(
            "recovery_tool_commit without recover_search_id is only valid for "
            "an observed preflight data rejection"
        )
    candidates = []
    periods_by_id: dict[str, tuple[PanelPeriod, ...]] = {}
    plans_by_size: dict[int, tuple[LiquidityDecisionPlan, ...]] = {}
    leakage_by_mode_size: dict[str, bool | None] = {}
    mapping = {}
    for definition, mode, universe_size, role in definitions:
        periods, plans = built_inputs[definition.alpha_id]
        candidates.append((definition, periods))
        periods_by_id[definition.alpha_id] = periods
        plans_by_size.setdefault(universe_size, plans)
        leakage_key = f"{mode}:top{universe_size}"
        leakage_by_mode_size.setdefault(
            leakage_key,
            liquidity_signal_leakage_ok(
                panel,
                LIQUIDITY_CONFIG,
                mode=mode,
                universe_size=universe_size,
                start_row=start_row,
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
            "leakage_ok": leakage_by_mode_size[f"{mode}:top{universe_size}"]
        }
        for definition, mode, universe_size, _role in definitions
    }
    aliases_to_ids = {alias: item["alpha_id"] for alias, item in mapping.items()}
    budget = ResearchBudget(
        max_trials=4,
        max_feature_count=2,
        max_parameter_dimensions=4,
        allowed_data_sources=(DATA_SOURCE,),
        stopping_rules={"max_trials": 4},
    )
    with production_experiment_registry() as registry:
        if recover_search_id is None:
            recorded_at = artifact_generated_at
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
                    "Exactly four frozen candidates: illiquidity primary, exact "
                    "sign control, fixed-hash placebo, and top-24 investability "
                    "control. No window, horizon, universe, quantile, or cost scan."
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
            expected_identity = {
                "search_kind": SEARCH_KIND,
                "source": SOURCE,
                "dataset_version": dataset_version,
            }
            if any(identity.get(key) != value for key, value in expected_identity.items()):
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
            if len(run_ids) != 4 or len(set(run_ids)) != 4:
                raise RuntimeError("recovery search does not have exactly four evaluations")
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

    replays = {
        alpha_id: {
            "base_cost": _replay(periods, BASE_COST),
            "double_cost_impact": _replay(periods, DOUBLE_COST_IMPACT),
        }
        for alpha_id, periods in periods_by_id.items()
    }
    primary_periods = periods_by_id[aliases_to_ids[PRIMARY_ALIAS]]
    holdout_count = max(1, math.ceil(len(primary_periods) * 0.20))
    holdout_start = len(primary_periods) - holdout_count
    holdout = _replay(primary_periods[holdout_start:], BASE_COST)
    decision = judge(evaluations, replays, aliases_to_ids, holdout)

    document = {
        "schema_version": "liquidity_premium_research.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": artifact_generated_at,
        "search_recorded_at_ms": recorded_at,
        "dataset_observed_through_ms": int(panel.event_times[-1]),
        "source_commit": source_commit,
        "recovery_tool_commit": recovery_tool_commit,
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
        "leakage_evidence": leakage_by_mode_size,
        "candidates": {
            alias: {
                **meta,
                "evaluation": evaluations.get(meta["alpha_id"]),
                "replays": replays[meta["alpha_id"]],
            }
            for alias, meta in mapping.items()
        },
        "locked_period_holdout": {
            "period_count": holdout_count,
            "period_start_index": holdout_start,
            "metrics": holdout,
        },
        "decision_plans": {
            f"top{universe_size}": [
                _plan_payload(plan, panel) for plan in plans
            ]
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
    quarantine_legacy_backtest("tools.liquidity_premium_research.main")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--recover-search-id")
    parser.add_argument("--recovery-tool-commit")
    args = parser.parse_args()
    result = run(
        output=args.output,
        source_commit=args.source_commit,
        recover_search_id=args.recover_search_id,
        recovery_tool_commit=args.recovery_tool_commit,
    )
    print(
        "liquidity premium: "
        f"status={result['decision']['status']} "
        f"economic_candidate={result['decision']['economic_candidate']} "
        f"failed={','.join(result['decision']['failed_bars']) or '-'}"
    )
    print(f"evidence={args.output}")


if __name__ == "__main__":
    main()


__all__ = [
    "CONTRACT_HASH",
    "EVALUATION_CONTRACT",
    "LARGE_CAP_ALIAS",
    "PRIMARY_ALIAS",
    "candidate_definitions",
    "judge",
    "run",
]
