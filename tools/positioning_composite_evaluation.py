"""Pre-registered formal evaluation of one fixed positioning composite.

The candidate is chosen before this driver observes any result: an equal-weight
cross-sectional blend of the canonical crowd and top-trader contrarian signals.
The two canonical signals are retained as controls in the same registered CSCV
selection population.  The complete three-member family is persisted before
any backtest, and every result is evaluated against the production experiment
catalog.

This is L2 research only.  A formal PASS may be handed to the independent
forward-paper admission flow, but this driver never admits, promotes, connects
to a broker, reads credentials, creates an execution intent, or submits an
order.  Testnet eligibility remains false even when the historical evaluation
passes because forward maturity and execution-risk evidence are separate gates.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.alpha.config import AlphaFactoryConfig
from services.factor_garden.canonical import (
    CROWD_CONTRA_EXPRESSION,
    TOP_CONTRA_EXPRESSION,
)
from services.intelligence.registered_candidate_batch import (
    run_registered_candidate_batch,
)
from services.research import alpha_expression as ax
from services.research.expression_leakage import compute_leakage_ok
from services.research.research_budget import ResearchBudget
from tools.cache_io import atomic_write_json
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
)


DEFAULT_OUTPUT = Path("data/cache/positioning_composite_evaluation.json")
FWD_HORIZON = 7
PERIODS_PER_YEAR = 52
DATA_SOURCE = "crypto_oi_pit_panel"
SOURCE = "tools.positioning_composite_evaluation"
SEARCH_KIND = "PREREGISTERED_POSITIONING_COMPOSITE_V1"
_COMMIT = re.compile(r"[0-9a-f]{40}")

# Fixed before evaluation.  CROWD_CONTRA is rank-scaled while TOP_CONTRA is
# z-scored, so standardize the control once before equal weighting.  The final
# division preserves scale without changing cross-sectional ordering.
POSITIONING_COMPOSITE_EXPRESSION = (
    "div",
    (
        "add",
        ("cs_zscore", CROWD_CONTRA_EXPRESSION),
        TOP_CONTRA_EXPRESSION,
    ),
    ("const", 2.0),
)

CANDIDATE_EXPRESSIONS: dict[str, tuple] = {
    "crowd_contra_control": CROWD_CONTRA_EXPRESSION,
    "top_contra_control": TOP_CONTRA_EXPRESSION,
    "positioning_consensus": POSITIONING_COMPOSITE_EXPRESSION,
}

ECONOMIC_HYPOTHESES = {
    "crowd_contra_control": (
        "Control: crowded retail positioning should mean-revert cross-sectionally."
    ),
    "top_contra_control": (
        "Control: extreme top-trader positioning should mean-revert cross-sectionally."
    ),
    "positioning_consensus": (
        "Retail and top-trader positioning describe distinct participant cohorts; "
        "equal standardized confirmation should reduce cohort-specific noise while "
        "retaining the common positioning-unwind premium after realistic costs."
    ),
}

FALSIFICATION_CRITERIA = (
    "The composite fails the immutable catalog-wide DSR threshold.",
    "The complete three-member CSCV selection population exceeds the PBO ceiling.",
    "The composite fails leakage, regime, or sequential walk-forward evidence.",
    "The composite does not improve robust evidence relative to both controls.",
)

EVALUATION_CONTRACT = {
    "driver": "positioning_composite_evaluation.v1",
    "fwd_horizon": FWD_HORIZON,
    "non_overlapping": True,
    "periods_per_year": PERIODS_PER_YEAR,
    "selection_population": tuple(CANDIDATE_EXPRESSIONS),
    "candidate_expression": ax.serialize(POSITIONING_COMPOSITE_EXPRESSION),
}


def _field_ids(expression: object) -> tuple[str, ...]:
    fields: set[str] = set()

    def visit(node: object) -> None:
        if isinstance(node, tuple):
            if len(node) == 2 and node[0] == "field" and isinstance(node[1], str):
                fields.add(node[1])
            for child in node:
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(expression)
    return tuple(sorted(fields))


def _definition(
    alias: str,
    expression: tuple,
    *,
    evaluation_contract: Mapping[str, Any] = EVALUATION_CONTRACT,
    id_prefix: str = "positioning",
) -> tuple[AlphaDefinition, str]:
    params = {
        "evaluation_contract": stable_hash(evaluation_contract),
        "expr": ax.serialize(expression),
    }
    seed = AlphaDefinition(
        alpha_id=alias,
        name=alias,
        factor_ids=_field_ids(expression),
        universe="crypto_oi_majors_pit",
        params=params,
    )
    research_id = f"{id_prefix}_{alias}_{seed.definition_hash[:16]}"
    definition = AlphaDefinition(
        alpha_id=research_id,
        name=alias,
        factor_ids=seed.factor_ids,
        universe=seed.universe,
        params=params,
    )
    if definition.definition_hash != seed.definition_hash:
        raise RuntimeError("content-addressed positioning identity drifted")
    return definition, seed.definition_hash


def build_candidate_batch(
    panel,
    *,
    fwd_horizon: int = FWD_HORIZON,
    evaluation_contract: Mapping[str, Any] = EVALUATION_CONTRACT,
    id_prefix: str = "positioning",
) -> tuple[list, dict[str, dict], dict[str, dict]]:
    """Build all controls/candidate on one PIT panel without observing results."""
    if (
        isinstance(fwd_horizon, bool)
        or not isinstance(fwd_horizon, int)
        or fwd_horizon <= 0
    ):
        raise ValueError("fwd_horizon must be a positive integer")
    if not isinstance(evaluation_contract, Mapping):
        raise TypeError("evaluation_contract must be a mapping")
    if not isinstance(id_prefix, str) or not id_prefix.strip():
        raise ValueError("id_prefix must be non-empty")
    candidates = []
    evidence: dict[str, dict[str, bool | None]] = {}
    mappings: dict[str, dict[str, Any]] = {}
    for alias, expression in CANDIDATE_EXPRESSIONS.items():
        definition, definition_hash = _definition(
            alias,
            expression,
            evaluation_contract=evaluation_contract,
            id_prefix=id_prefix,
        )
        signal = ax.evaluate(expression, panel)
        periods = ax.panel_to_periods(
            signal,
            panel,
            fwd_horizon=fwd_horizon,
            non_overlapping=True,
        )
        candidates.append((definition, periods))
        evidence[definition.alpha_id] = {
            "leakage_ok": compute_leakage_ok(expression, panel)
        }
        mappings[alias] = {
            "alias": alias,
            "research_alpha_id": definition.alpha_id,
            "definition_hash": definition_hash,
            "expression": ax.serialize(expression),
            "factor_ids": list(definition.factor_ids),
            "economic_hypothesis": ECONOMIC_HYPOTHESES[alias],
        }
    return candidates, evidence, mappings


def _evaluation_row(
    registry: object,
    search_id: str,
    run_id: object,
    mappings: Mapping[str, Mapping[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    run_id = str(run_id)
    row = registry.get_evaluation(run_id)
    if not isinstance(row, Mapping):
        return None
    links = [
        link
        for link in registry.search_evaluations(search_id)
        if isinstance(link, Mapping) and str(link.get("run_id", "")) == run_id
    ]
    if len(links) != 1:
        raise RuntimeError("evaluation has no unique immutable search binding")
    link = links[0]
    if link.get("evaluation_content_hash") != row.get("content_hash"):
        raise RuntimeError("evaluation content binding mismatch")
    research_id = str(row.get("alpha_id", ""))
    alias = next(
        (
            name
            for name, mapping in mappings.items()
            if mapping["research_alpha_id"] == research_id
        ),
        None,
    )
    if alias is None:
        raise RuntimeError("evaluation is not bound to the registered positioning family")
    mapping = mappings[alias]
    if link.get("config_hash") != mapping["definition_hash"]:
        raise RuntimeError("evaluation definition binding mismatch")
    try:
        evaluation = json.loads(str(row["evaluation_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("evaluation payload is invalid") from exc
    verified = registry.verify_evaluation(str(run_id)) is True
    return alias, {
        "run_id": str(run_id),
        "verified": verified,
        "verdict": evaluation.get("verdict"),
        "deflated_sharpe": evaluation.get("deflated_sharpe"),
        "pbo": evaluation.get("pbo"),
        "net_sharpe": evaluation.get("net_sharpe"),
        "capacity_adj_sharpe": evaluation.get("capacity_adj_sharpe"),
        "observed_sr": evaluation.get("observed_sr"),
        "n_periods": evaluation.get("n_periods"),
        "regime_profitable_frac": evaluation.get("regime_profitable_frac"),
        "reasons": list(evaluation.get("reasons") or ()),
        "catalog_trial_count": evaluation.get("catalog_trial_count"),
        "as_of": evaluation.get("as_of"),
        "dataset_version": row.get("dataset_version"),
        "content_hash": row.get("content_hash"),
    }


def _decision(evaluation: Mapping[str, Any] | None) -> dict[str, bool]:
    verified = isinstance(evaluation, Mapping) and evaluation.get("verified") is True
    verdict = evaluation.get("verdict") if verified else None
    formal_pass = verdict == "PASS"
    return {
        "formal_evaluation_pass": formal_pass,
        "human_review_required": verdict == "REQUIRES_HUMAN_REVIEW",
        "forward_admission_allowed": formal_pass,
        # Historical PASS is necessary but never sufficient for Testnet.
        "testnet_eligible": False,
    }


def _run_positioning_spec(
    *,
    output: str | Path,
    recorded_at: int | None = None,
    source_commit: str,
    panel=None,
    fwd_horizon: int,
    periods_per_year: int,
    evaluation_contract: Mapping[str, Any],
    source: str,
    search_kind: str,
    data_source: str,
    schema_version: str,
    id_prefix: str,
    parent_hypothesis: str,
    search_space: str,
    falsification_criteria: Sequence[str],
) -> dict[str, Any]:
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source_commit must be a lowercase 40-character git commit")
    if panel is None:
        from tools.crypto_oi_research import build_panel

        panel, _instruments = build_panel()
    candidates, leakage_evidence, mappings = build_candidate_batch(
        panel,
        fwd_horizon=fwd_horizon,
        evaluation_contract=evaluation_contract,
        id_prefix=id_prefix,
    )
    observed_at = int(
        recorded_at if recorded_at is not None else time.time_ns() // 1_000_000
    )
    dataset_version = field_panel_dataset_version(
        panel,
        source_versions=evaluation_contract,
    )
    search_id = make_search_id(
        source=source,
        search_kind=search_kind,
        dataset_version=dataset_version,
        recorded_at=observed_at,
        params={
            "candidate_aliases": list(CANDIDATE_EXPRESSIONS),
            "source_commit": source_commit,
        },
    )
    budget = ResearchBudget(
        max_trials=len(CANDIDATE_EXPRESSIONS),
        max_feature_count=2,
        max_parameter_dimensions=2,
        allowed_data_sources=(data_source,),
        stopping_rules={"max_trials": len(CANDIDATE_EXPRESSIONS)},
    )
    with production_experiment_registry() as registry:
        result = run_registered_candidate_batch(
            candidates,
            registry=registry,
            search_id=search_id,
            search_kind=search_kind,
            search_source=source,
            dataset_version=dataset_version,
            recorded_at=observed_at,
            periods_per_year=periods_per_year,
            config=AlphaFactoryConfig(compute_regime_evidence=True),
            evidence=leakage_evidence,
            budget=budget,
            parent_hypothesis=parent_hypothesis,
            search_space=search_space,
            data_source=data_source,
            uses_locked_oos=False,
            hypothesis_by_candidate={
                mapping["research_alpha_id"]: stable_hash(
                    {"hypothesis": mapping["economic_hypothesis"]}
                )
                for mapping in mappings.values()
            },
            code_hash=source_commit,
        )
        evaluations: dict[str, dict[str, Any]] = {}
        for run_id in result.get("recorded_run_ids", ()):
            item = _evaluation_row(registry, search_id, run_id, mappings)
            if item is not None:
                alias, evaluation = item
                evaluations[alias] = evaluation

    rows = {
        alias: {
            **mapping,
            "evaluation": evaluations.get(alias),
            "status": (
                "VERIFIED"
                if evaluations.get(alias, {}).get("verified") is True
                else "PENDING"
            ),
        }
        for alias, mapping in mappings.items()
    }
    complete = all(row["status"] == "VERIFIED" for row in rows.values())
    candidate_evaluation = evaluations.get("positioning_consensus")
    document = {
        "schema_version": schema_version,
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": observed_at,
        "source_commit": source_commit,
        "dataset_version": dataset_version,
        "search_id": search_id,
        "status": "VERIFIED" if complete else "PENDING",
        "research_budget": budget.metadata(),
        "falsification_criteria": list(falsification_criteria),
        "family": rows,
        "decision": _decision(candidate_evaluation),
        "safety": {
            "research_only": True,
            "forward_admission_performed": False,
            "testnet_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
        },
    }
    atomic_write_json(str(output), document, indent=2)
    return document


def run(
    *,
    output: str | Path = DEFAULT_OUTPUT,
    recorded_at: int | None = None,
    source_commit: str,
    panel=None,
) -> dict[str, Any]:
    return _run_positioning_spec(
        output=output,
        recorded_at=recorded_at,
        source_commit=source_commit,
        panel=panel,
        fwd_horizon=FWD_HORIZON,
        periods_per_year=PERIODS_PER_YEAR,
        evaluation_contract=EVALUATION_CONTRACT,
        source=SOURCE,
        search_kind=SEARCH_KIND,
        data_source=DATA_SOURCE,
        schema_version="positioning_composite_evaluation.v1",
        id_prefix="positioning",
        parent_hypothesis=(
            "Distinct participant-positioning cohorts may combine into a more "
            "stable cross-sectional unwind premium than either cohort alone."
        ),
        search_space=(
            "Exactly two canonical controls and one fixed equal-standardized "
            "crowd/top consensus expression; no parameter sweep."
        ),
        falsification_criteria=FALSIFICATION_CRITERIA,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args(argv)
    try:
        document = run(output=args.output, source_commit=args.source_commit)
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        print(f"positioning composite evaluation blocked: {type(exc).__name__}: {exc}")
        return 2
    candidate = document["family"]["positioning_consensus"]["evaluation"] or {}
    print(
        "positioning composite evaluation: "
        f"status={document['status']} verdict={candidate.get('verdict')} "
        f"dsr={candidate.get('deflated_sharpe')} pbo={candidate.get('pbo')}"
    )
    return 0 if document["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CANDIDATE_EXPRESSIONS",
    "DATA_SOURCE",
    "DEFAULT_OUTPUT",
    "POSITIONING_COMPOSITE_EXPRESSION",
    "_run_positioning_spec",
    "build_candidate_batch",
    "run",
]
