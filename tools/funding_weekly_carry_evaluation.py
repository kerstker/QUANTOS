"""One-shot formal evaluation of fixed weekly funding carry.

This is a single pre-registered hypothesis, not a parameter search.  Every
Monday 00:00 UTC settlement, the signal ranks the trailing 21 already-settled
funding rates.  The book holds to the next Monday and realizes exactly the next
21 funding cash flows plus mark-price movement, paying ordinary execution
costs on actual weekly turnover.

The driver reads public historical data and writes research evidence only.  A
historical PASS may admit a candidate to the separate forward-paper workflow;
it never grants Testnet, broker, credential, promotion, or order authority.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - direct CLI compatibility
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.execution.backtest_authority import quarantine_legacy_backtest
from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.cost import CostModel
from quantos.contracts.hashing import stable_hash
from services.alpha.config import AlphaFactoryConfig
from services.alpha.panel_backtest import (
    PanelPeriod,
    _run_panel_backtest_legacy_diagnostic as run_panel_backtest,
)
from services.intelligence.registered_candidate_batch import (
    run_registered_candidate_batch,
)
from services.research.funding_settlement import (
    SettlementObservation,
    build_weekly_carry_periods,
)
from services.research.research_budget import ResearchBudget
from tools.cache_io import atomic_write_json
from tools.funding_settlement_evaluation import load_history
from tools.search_trial_registry import (
    make_search_id,
    production_experiment_registry,
)


DEFAULT_SOURCE = Path("data/cache/binance_funding_settlement_history.json")
DEFAULT_OUTPUT = Path("data/cache/funding_weekly_carry_evaluation.json")
SOURCE = "tools.funding_weekly_carry_evaluation"
SEARCH_KIND = "PREREGISTERED_WEEKLY_FUNDING_CARRY_V1"
DATA_SOURCE = "binance_funding_settlement_history.v1"
PERIODS_PER_YEAR = 52
EVALUATION_QUANTILE = 0.20
MIN_ROWS_PER_INSTRUMENT = 365 * 3
BASE_COST = CostModel(fee_bps=4.0, slippage_bps=2.0, impact_coef=0.0)
DOUBLE_COST = CostModel(fee_bps=8.0, slippage_bps=4.0, impact_coef=0.0)
_COMMIT = re.compile(r"[0-9a-f]{40}")


ECONOMIC_HYPOTHESIS = (
    "Cross-sectional funding premia persist over a full week, allowing a "
    "Monday-rebalanced low-funding-long/high-funding-short book to collect "
    "21 exact settlements with sufficiently low turnover to survive costs."
)
FALSIFICATION_CRITERIA = (
    "The single fixed candidate fails catalog-wide DSR.",
    "Leakage, regime, or sequential walk-forward evidence fails.",
    "The full-history net result is non-positive at twice fixed costs.",
    "No verified formal PASS exists for forward-paper admission.",
)
EVALUATION_CONTRACT = {
    "driver": "funding_weekly_carry_evaluation.v1",
    "signal": "negative_mean_of_trailing_21_settled_funding_rates",
    "decision_mark": "completed_first_1h_mark_bar_after_monday_00_utc_settlement",
    "label": "next_monday_mark_return_minus_next_21_settled_funding_rates",
    "formation_settlements": 21,
    "holding_settlements": 21,
    "rebalance": "MONDAY_00_UTC",
    "periods_per_year": PERIODS_PER_YEAR,
    "entry_quantile": EVALUATION_QUANTILE,
    "cost": {"fee_bps": 4.0, "slippage_bps": 2.0, "impact_coef": 0.0},
    "double_cost": {"fee_bps": 8.0, "slippage_bps": 4.0, "impact_coef": 0.0},
    "selection_population": ["weekly_carry"],
    "parameter_scan": False,
    "shared_calendar": True,
    "historical_data_grants_testnet": False,
}


def _definition() -> tuple[AlphaDefinition, str]:
    params = {
        "evaluation_contract": stable_hash(EVALUATION_CONTRACT),
        "schedule": "monday_00_utc",
    }
    seed = AlphaDefinition(
        alpha_id="weekly_carry",
        name="weekly_carry",
        factor_ids=(
            "settled_funding_rate_7d",
            "post_settlement_mark_price",
        ),
        universe="binance_usdm_14_liquid_perpetuals",
        params=params,
    )
    research_id = f"funding_weekly_carry_{seed.definition_hash[:16]}"
    definition = AlphaDefinition(
        alpha_id=research_id,
        name="weekly_carry",
        factor_ids=seed.factor_ids,
        universe=seed.universe,
        params=params,
    )
    if definition.definition_hash != seed.definition_hash:
        raise RuntimeError("content-addressed weekly funding identity drifted")
    return definition, seed.definition_hash


def build_candidate_batch(
    history: Mapping[str, Sequence[SettlementObservation]],
) -> tuple[
    list[tuple[AlphaDefinition, tuple[PanelPeriod, ...]]],
    dict[str, dict[str, bool]],
    dict[str, Any],
]:
    periods = build_weekly_carry_periods(history)
    definition, definition_hash = _definition()
    return (
        [(definition, periods)],
        {definition.alpha_id: {"leakage_ok": True}},
        {
            "alias": "weekly_carry",
            "research_alpha_id": definition.alpha_id,
            "definition_hash": definition_hash,
            "economic_hypothesis": ECONOMIC_HYPOTHESIS,
            "period_count": len(periods),
        },
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
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        drawdown = max(drawdown, 1.0 - equity / peak)
    return drawdown


def _pressure(periods: Sequence[PanelPeriod]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, cost in (("1.0x", BASE_COST), ("2.0x", DOUBLE_COST)):
        replay = run_panel_backtest(
            periods,
            cost=cost,
            quantile=EVALUATION_QUANTILE,
        )
        result[label] = {
            "net_sharpe": _sharpe(replay.period_returns),
            "net_return": math.prod(
                1.0 + value for value in replay.period_returns
            )
            - 1.0,
            "max_drawdown": _max_drawdown(replay.period_returns),
            "mean_turnover": statistics.fmean(replay.turnovers),
            "total_cost": sum(replay.costs),
            "reconciliation_residual": replay.reconciliation_residual(),
        }
    return result


def _evaluation_row(
    registry: object,
    search_id: str,
    run_id: object,
    mapping: Mapping[str, Any],
) -> dict[str, Any] | None:
    run_id = str(run_id)
    row = registry.get_evaluation(run_id)
    if not isinstance(row, Mapping):
        return None
    links = [
        link
        for link in registry.search_evaluations(search_id)
        if isinstance(link, Mapping) and str(link.get("run_id", "")) == run_id
    ]
    if len(links) != 1 or links[0].get("evaluation_content_hash") != row.get(
        "content_hash"
    ):
        raise RuntimeError("evaluation has no unique immutable search binding")
    if (
        str(row.get("alpha_id", "")) != mapping["research_alpha_id"]
        or links[0].get("config_hash") != mapping["definition_hash"]
    ):
        raise RuntimeError("evaluation definition binding mismatch")
    try:
        evaluation = json.loads(str(row["evaluation_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("evaluation payload is invalid") from exc
    return {
        "run_id": run_id,
        "verified": registry.verify_evaluation(run_id) is True,
        "verdict": evaluation.get("verdict"),
        "deflated_sharpe": evaluation.get("deflated_sharpe"),
        "pbo": evaluation.get("pbo"),
        "net_sharpe": evaluation.get("net_sharpe"),
        "observed_sr": evaluation.get("observed_sr"),
        "n_periods": evaluation.get("n_periods"),
        "regime_profitable_frac": evaluation.get("regime_profitable_frac"),
        "catalog_trial_count": evaluation.get("catalog_trial_count"),
        "reasons": list(evaluation.get("reasons") or ()),
        "content_hash": row.get("content_hash"),
    }


def _decision(
    evaluation: Mapping[str, Any] | None,
    pressure: Mapping[str, Any],
) -> dict[str, Any]:
    stress = pressure.get("2.0x") or {}
    passed = bool(
        evaluation is not None
        and evaluation.get("verified") is True
        and evaluation.get("verdict") == "PASS"
        and isinstance(stress.get("net_return"), (int, float))
        and not isinstance(stress.get("net_return"), bool)
        and float(stress["net_return"]) > 0.0
    )
    return {
        "selected_alias": "weekly_carry" if passed else None,
        "formal_evaluation_pass": passed,
        "double_cost_positive": bool(
            isinstance(stress.get("net_return"), (int, float))
            and not isinstance(stress.get("net_return"), bool)
            and float(stress["net_return"]) > 0.0
        ),
        "forward_admission_allowed": passed,
        "testnet_eligible": False,
    }


def _run_legacy_diagnostic(
    *,
    source_snapshot: str | Path = DEFAULT_SOURCE,
    output: str | Path = DEFAULT_OUTPUT,
    source_commit: str,
    recorded_at: int | None = None,
    min_rows_per_instrument: int = MIN_ROWS_PER_INSTRUMENT,
) -> dict[str, Any]:
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source_commit must be a lowercase 40-character git commit")
    history, source_metadata = load_history(
        source_snapshot,
        min_rows_per_instrument=min_rows_per_instrument,
    )
    candidates, leakage_evidence, mapping = build_candidate_batch(history)
    periods = candidates[0][1]
    pressure = _pressure(periods)
    observed_at = int(
        recorded_at if recorded_at is not None else time.time_ns() // 1_000_000
    )
    dataset_version = stable_hash(
        {
            "evaluation_contract": EVALUATION_CONTRACT,
            "source_dataset_hash": source_metadata["dataset_hash"],
            "source_artifact_sha256": source_metadata["artifact_sha256"],
        }
    )
    search_id = make_search_id(
        source=SOURCE,
        search_kind=SEARCH_KIND,
        dataset_version=dataset_version,
        recorded_at=observed_at,
        params={
            "candidate_aliases": ["weekly_carry"],
            "source_commit": source_commit,
        },
    )
    budget = ResearchBudget(
        max_trials=1,
        max_feature_count=2,
        max_parameter_dimensions=2,
        allowed_data_sources=(DATA_SOURCE,),
        stopping_rules={"max_trials": 1},
    )
    with production_experiment_registry() as registry:
        result = run_registered_candidate_batch(
            candidates,
            registry=registry,
            search_id=search_id,
            search_kind=SEARCH_KIND,
            search_source=SOURCE,
            dataset_version=dataset_version,
            recorded_at=observed_at,
            periods_per_year=PERIODS_PER_YEAR,
            quantile=EVALUATION_QUANTILE,
            config=AlphaFactoryConfig(compute_regime_evidence=True),
            cost=BASE_COST,
            evidence=leakage_evidence,
            budget=budget,
            parent_hypothesis=ECONOMIC_HYPOTHESIS,
            search_space=(
                "Exactly one Monday-UTC weekly carry candidate; fixed trailing and "
                "holding windows of 21 settlements; no parameter, direction, "
                "weekday, universe, quantile, or cost scan."
            ),
            data_source=DATA_SOURCE,
            uses_locked_oos=False,
            hypothesis_by_candidate={
                mapping["research_alpha_id"]: stable_hash(
                    {"hypothesis": ECONOMIC_HYPOTHESIS}
                )
            },
            code_hash=source_commit,
        )
        evaluation = None
        for run_id in result.get("recorded_run_ids", ()):
            item = _evaluation_row(registry, search_id, run_id, mapping)
            if item is not None:
                if evaluation is not None:
                    raise RuntimeError("weekly carry produced multiple evaluations")
                evaluation = item

    decision = _decision(evaluation, pressure)
    document = {
        "schema_version": "funding_weekly_carry_evaluation.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": observed_at,
        "search_id": search_id,
        "search_kind": SEARCH_KIND,
        "dataset_version": dataset_version,
        "source": source_metadata,
        "evaluation_contract": EVALUATION_CONTRACT,
        "falsification_criteria": list(FALSIFICATION_CRITERIA),
        "candidate": {
            **mapping,
            "pressure": pressure,
            "evaluation": evaluation,
            "status": (
                "VERIFIED"
                if evaluation is not None and evaluation.get("verified") is True
                else "PENDING"
            ),
        },
        "decision": decision,
        "safety": {
            "public_market_data_only": True,
            "credentials": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
            "historical_pass_is_not_testnet_authority": True,
        },
    }
    atomic_write_json(str(output), document, indent=2)
    return document


def run(*args, **kwargs) -> dict[str, Any]:
    quarantine_legacy_backtest("tools.funding_weekly_carry_evaluation.run")


def main() -> None:
    quarantine_legacy_backtest("tools.funding_weekly_carry_evaluation.main")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    result = run(
        source_snapshot=args.source,
        output=args.output,
        source_commit=args.source_commit,
    )
    print(
        "funding weekly carry evaluation: "
        f"pass={result['decision']['formal_evaluation_pass']} "
        f"selected={result['decision']['selected_alias']}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "EVALUATION_CONTRACT",
    "build_candidate_batch",
    "load_history",
    "run",
]
