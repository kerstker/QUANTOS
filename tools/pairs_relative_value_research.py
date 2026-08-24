"""One-shot, pre-registered Gatev-style pairs relative-value experiment.

The complete three-candidate family is frozen in EVALUATION_CONTRACT and enters
the durable global trial catalog as one aligned batch.  Results are historical
diagnostics only; this tool has no broker, order, live, or promotion authority.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
import os
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
from services.research.pairs_relative_value import (
    PairCyclePlan,
    PairExperimentConfig,
    build_pair_periods,
    find_first_eligible_formation_start,
    pair_signal_leakage_ok,
)
from services.research.research_budget import ResearchBudget
from tools.cache_io import atomic_write_json
from tools.ic_panel_registry import _build as _build_pit_panel, _load_json
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
)


SOURCE = "tools.pairs_relative_value_research"
SEARCH_KIND = "PAIRS_RELATIVE_VALUE_V1"
DATA_SOURCE = "crypto_funding.db"
PERIODS_PER_YEAR = 365
QUANTILE = 0.30
TARGET_AUM = 1_000_000.0
BASE_COST = CostModel(fee_bps=4.0, slippage_bps=2.0, impact_coef=10.0)
DOUBLE_BASE_COST = CostModel(fee_bps=8.0, slippage_bps=4.0, impact_coef=10.0)
PAIR_CONFIG = PairExperimentConfig()
FAMILY = "distance_selected_pairs_relative_value_v1"
PRIMARY_ALIAS = "pairs_distance_convergence_v1"
_COMMIT = re.compile(r"[0-9a-f]{40}")

ECONOMIC_HYPOTHESIS = (
    "Pairs selected by formation-only normalized-price distance exhibit "
    "out-of-formation convergence in crypto after realistic costs."
)

EVALUATION_CONTRACT = {
    "schema": "pairs_relative_value_contract.v1",
    "source_paper": {
        "title": "Pairs Trading: Performance of a Relative-Value Arbitrage Rule",
        "doi": "10.3386/w7032",
        "portability": "CROSS_ASSET_PRIOR_NOT_CRYPTO_EVIDENCE",
    },
    "formation_rows": 180,
    "sample_start_rule": "earliest_formation_window_with_24_complete_instruments",
    "trade_rows": 90,
    "universe_size": 24,
    "pair_count": 10,
    "formation_liquidity": "median_daily_close_times_volume",
    "selection": "normalized_path_squared_distance_greedy_disjoint",
    "spread": "left_normalized_price_minus_right_normalized_price",
    "signal": "negative_formation_zscore",
    "label": "0.5_times_left_return_minus_right_return_next_day",
    "cadence": "daily_non_overlapping_cycles",
    "periods_per_year": PERIODS_PER_YEAR,
    "quantile": QUANTILE,
    "target_aum_usd": TARGET_AUM,
    "cost": asdict(BASE_COST),
    "double_base_cost": asdict(DOUBLE_BASE_COST),
    "candidate_aliases": (
        PRIMARY_ALIAS,
        "pairs_distance_continuation_v1",
        "pairs_hash_convergence_v1",
    ),
    "parameter_scan": False,
    "historical_diagnostic_only": True,
    "historical_pass_is_not_forward_or_live_authority": True,
    "decision_bars": {
        "evaluator_verdict": "PASS",
        "min_net_sharpe": 1.0,
        "min_net_return": 0.0,
        "min_dsr": 0.95,
        "max_pbo": 0.25,
        "max_drawdown": 0.15,
        "holdout_cycle_fraction": 0.20,
        "min_holdout_sharpe": 0.80,
        "min_holdout_return": 0.0,
        "double_base_cost_positive": True,
        "must_beat_sign_control": True,
        "must_beat_hash_control": True,
    },
}
CONTRACT_HASH = stable_hash(EVALUATION_CONTRACT)

_CANDIDATE_SPECS = (
    (PRIMARY_ALIAS, "distance", "convergence", "PRIMARY"),
    ("pairs_distance_continuation_v1", "distance", "continuation", "SIGN_CONTROL"),
    ("pairs_hash_convergence_v1", "hash", "convergence", "SELECTION_CONTROL"),
)


def candidate_definitions() -> tuple[tuple[AlphaDefinition, str, str, str], ...]:
    out = []
    for alias, selection, direction, role in _CANDIDATE_SPECS:
        params = {
            "contract_hash": CONTRACT_HASH,
            "direction": direction,
            "family": FAMILY,
            "selection": selection,
        }
        seed = AlphaDefinition(
            alpha_id=alias,
            name=alias,
            factor_ids=("formation_distance_pair", "formation_spread_zscore"),
            universe="binance_spot_formation_liquid_top24",
            params=params,
        )
        definition = AlphaDefinition(
            alpha_id=f"{alias}_{seed.definition_hash[:16]}",
            name=alias,
            factor_ids=seed.factor_ids,
            universe=seed.universe,
            params=params,
        )
        out.append((definition, selection, direction, role))
    return tuple(out)


def _research_panel():
    instruments = sorted(_load_json("crypto_funding_snapshot.json")["instruments"])
    return _build_pit_panel(
        "crypto_funding.db", instruments, fields=("close", "volume")
    )


def _sharpe(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    sigma = statistics.pstdev(values)
    return 0.0 if sigma == 0.0 else statistics.fmean(values) / sigma * math.sqrt(PERIODS_PER_YEAR)


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
    return {
        "n_periods": len(returns),
        "net_sharpe": _sharpe(returns),
        "net_return": math.prod(1.0 + value for value in returns) - 1.0,
        "max_drawdown": _max_drawdown(returns),
        "mean_turnover": statistics.fmean(result.turnovers) if result.turnovers else 0.0,
        "total_cost": math.fsum(result.costs),
        "reconciliation_residual": result.reconciliation_residual(),
        "period_returns": list(returns),
    }


def _cycle_payload(plan: PairCyclePlan, panel) -> dict[str, Any]:
    return {
        "formation_start_time": int(panel.event_times[plan.formation_start]),
        "formation_stop_time": int(panel.event_times[plan.formation_stop - 1]),
        "trade_start_time": int(panel.event_times[plan.trade_start]),
        "trade_stop_time": int(panel.event_times[plan.trade_stop]),
        "selection": plan.selection,
        "liquid_universe": list(plan.liquid_universe),
        "pairs": [
            {
                "pair_id": stat.pair_id,
                "formation_distance": stat.formation_distance,
                "spread_mean": stat.spread_mean,
                "spread_std": stat.spread_std,
            }
            for stat in plan.statistics
        ],
    }


def _stored_evaluation(
    registry,
    search_id: str,
    run_id: str,
    definitions: Mapping[str, AlphaDefinition],
) -> tuple[str, dict[str, Any]]:
    row = registry.get_evaluation(str(run_id))
    if not isinstance(row, Mapping) or registry.verify_evaluation(str(run_id)) is not True:
        raise RuntimeError("stored evaluation failed immutable verification")
    alpha_id = str(row.get("alpha_id", ""))
    definition = definitions.get(alpha_id)
    if definition is None:
        raise RuntimeError("stored evaluation is not part of the frozen family")
    links = [
        item
        for item in registry.search_evaluations(search_id)
        if isinstance(item, Mapping) and str(item.get("run_id", "")) == str(run_id)
    ]
    if (
        len(links) != 1
        or links[0].get("config_hash") != definition.definition_hash
        or links[0].get("evaluation_content_hash") != row.get("content_hash")
    ):
        raise RuntimeError("stored evaluation has no unique exact-definition binding")
    try:
        payload = json.loads(str(row["evaluation_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("stored evaluation payload is invalid") from exc
    return alpha_id, {
        "run_id": str(run_id),
        "content_hash": row.get("content_hash"),
        "verified": True,
        "verdict": payload.get("verdict"),
        "reasons": list(payload.get("reasons") or ()),
        "net_sharpe": payload.get("net_sharpe"),
        "deflated_sharpe": payload.get("deflated_sharpe"),
        "pbo": payload.get("pbo"),
        "n_periods": payload.get("n_periods"),
        "catalog_trial_count": payload.get("catalog_trial_count"),
        "catalog_trial_set_hash": payload.get("catalog_trial_set_hash"),
        "catalog_evidence_hash": payload.get("catalog_evidence_hash"),
        "pbo_evidence_hash": payload.get("pbo_evidence_hash"),
        "reproducibility_hash": payload.get("reproducibility_hash"),
    }


def judge(
    evaluations: Mapping[str, Mapping[str, Any]],
    replays: Mapping[str, Mapping[str, Any]],
    aliases_to_ids: Mapping[str, str],
    holdout: Mapping[str, Any],
) -> dict[str, Any]:
    primary_id = aliases_to_ids[PRIMARY_ALIAS]
    evaluation = evaluations.get(primary_id) or {}
    primary = replays[primary_id]
    doubled = primary["double_base_cost"]
    sign = replays[aliases_to_ids["pairs_distance_continuation_v1"]]["base_cost"]
    hashed = replays[aliases_to_ids["pairs_hash_convergence_v1"]]["base_cost"]
    checks = {
        "immutable_evaluation_verified": evaluation.get("verified") is True,
        "evaluator_verdict_pass": evaluation.get("verdict") == "PASS",
        "net_sharpe_at_least_1": primary["base_cost"]["net_sharpe"] >= 1.0,
        "net_return_positive": primary["base_cost"]["net_return"] > 0.0,
        "dsr_at_least_0_95": float(evaluation.get("deflated_sharpe") or 0.0) >= 0.95,
        "pbo_at_most_0_25": float(evaluation.get("pbo") or 1.0) <= 0.25,
        "max_drawdown_at_most_0_15": primary["base_cost"]["max_drawdown"] <= 0.15,
        "holdout_sharpe_at_least_0_80": holdout["net_sharpe"] >= 0.80,
        "holdout_return_positive": holdout["net_return"] > 0.0,
        "double_base_cost_positive": doubled["net_return"] > 0.0,
        "beats_sign_control": primary["base_cost"]["net_sharpe"] > sign["net_sharpe"],
        "beats_hash_control": primary["base_cost"]["net_sharpe"] > hashed["net_sharpe"],
    }
    economic_keys = tuple(name for name in checks if name not in {
        "immutable_evaluation_verified", "evaluator_verdict_pass", "dsr_at_least_0_95", "pbo_at_most_0_25"
    })
    economic_pass = all(checks[name] for name in economic_keys)
    formal_pass = all(checks.values())
    return {
        "status": "HISTORICAL_DIAGNOSTIC_PASS" if formal_pass else "REJECTED_HISTORICAL",
        "economic_candidate": economic_pass,
        "formal_pass": formal_pass,
        "failed_bars": [name for name, passed in checks.items() if not passed],
        "checks": checks,
        "forward_admission_allowed": False,
        "testnet_eligible": False,
        "live_eligible": False,
    }


def run(*, output: str | Path, source_commit: str) -> dict[str, Any]:
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
    # Registry STARTED events form a monotonic production clock.  Dataset time
    # belongs in dataset_version/sample metadata; using the final market bar as
    # the event clock rolls back as soon as any newer research run exists.
    recorded_at = time.time_ns() // 1_000_000
    definitions = candidate_definitions()
    start_row = find_first_eligible_formation_start(panel, PAIR_CONFIG)
    candidates = []
    plans_by_selection: dict[str, tuple[PairCyclePlan, ...]] = {}
    periods_by_id: dict[str, tuple[PanelPeriod, ...]] = {}
    leakage_by_selection: dict[str, bool | None] = {}
    mapping = {}
    for definition, selection, direction, role in definitions:
        periods, plans = build_pair_periods(
            panel,
            PAIR_CONFIG,
            selection=selection,
            direction=direction,
            start_row=start_row,
        )
        candidates.append((definition, periods))
        periods_by_id[definition.alpha_id] = periods
        plans_by_selection.setdefault(selection, plans)
        leakage_by_selection.setdefault(
            selection,
            pair_signal_leakage_ok(
                panel, PAIR_CONFIG, selection=selection, start_row=start_row
            ),
        )
        mapping[definition.name] = {
            "alpha_id": definition.alpha_id,
            "definition_hash": definition.definition_hash,
            "selection": selection,
            "direction": direction,
            "role": role,
        }
    evidence = {
        definition.alpha_id: {"leakage_ok": leakage_by_selection[selection]}
        for definition, selection, _direction, _role in definitions
    }
    aliases_to_ids = {alias: item["alpha_id"] for alias, item in mapping.items()}
    search_id = make_search_id(
        source=SOURCE,
        search_kind=SEARCH_KIND,
        dataset_version=dataset_version,
        recorded_at=recorded_at,
        params={"contract_hash": CONTRACT_HASH, "source_commit": source_commit},
    )
    budget = ResearchBudget(
        max_trials=3,
        max_feature_count=2,
        max_parameter_dimensions=4,
        allowed_data_sources=(DATA_SOURCE,),
        stopping_rules={"max_trials": 3},
    )
    with production_experiment_registry() as registry:
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
            config=AlphaFactoryConfig(compute_regime_evidence=True),
            cost=BASE_COST,
            evidence=evidence,
            budget=budget,
            parent_hypothesis=ECONOMIC_HYPOTHESIS,
            search_space=(
                "Exactly three frozen candidates: distance convergence, its exact "
                "sign control, and fixed-hash-pair convergence control. No scan."
            ),
            data_source=DATA_SOURCE,
            uses_locked_oos=False,
            hypothesis_by_candidate={
                definition.alpha_id: stable_hash({"hypothesis": ECONOMIC_HYPOTHESIS, "role": role})
                for definition, _selection, _direction, role in definitions
            },
            code_hash=source_commit,
        )
        definition_by_id = {definition.alpha_id: definition for definition, *_ in definitions}
        evaluations = dict(
            _stored_evaluation(registry, search_id, str(run_id), definition_by_id)
            for run_id in batch.get("recorded_run_ids", ())
        )
        trial_count_after = registry._store.trial_count()

    replays = {
        alpha_id: {
            "base_cost": _replay(periods, BASE_COST),
            "double_base_cost": _replay(periods, DOUBLE_BASE_COST),
        }
        for alpha_id, periods in periods_by_id.items()
    }
    primary_periods = periods_by_id[aliases_to_ids[PRIMARY_ALIAS]]
    primary_cycles = plans_by_selection["distance"]
    holdout_cycles = max(1, math.ceil(len(primary_cycles) * 0.20))
    holdout_start = (len(primary_cycles) - holdout_cycles) * PAIR_CONFIG.trade_rows
    holdout = _replay(primary_periods[holdout_start:], BASE_COST)
    decision = judge(evaluations, replays, aliases_to_ids, holdout)

    document = {
        "schema_version": "pairs_relative_value_research.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": recorded_at,
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
        "trial_registry": {
            "trial_count_before": trial_count_before,
            "trial_count_after": trial_count_after,
            "trial_count_delta": trial_count_after - trial_count_before,
            "registered_candidate_count": batch.get("registered_candidate_count"),
            "recorded_run_ids": list(batch.get("recorded_run_ids", ())),
            "budget_declaration": batch.get("budget_declaration"),
        },
        "leakage_evidence": leakage_by_selection,
        "candidates": {
            alias: {
                **meta,
                "evaluation": evaluations.get(meta["alpha_id"]),
                "replays": replays[meta["alpha_id"]],
            }
            for alias, meta in mapping.items()
        },
        "locked_cycle_holdout": {
            "cycle_count": holdout_cycles,
            "period_start_index": holdout_start,
            "metrics": holdout,
        },
        "cycles": {
            selection: [_cycle_payload(plan, panel) for plan in plans]
            for selection, plans in plans_by_selection.items()
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
    args = parser.parse_args()
    result = run(output=args.output, source_commit=args.source_commit)
    print(
        "pairs relative value: "
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
    "candidate_definitions",
    "judge",
    "run",
]
