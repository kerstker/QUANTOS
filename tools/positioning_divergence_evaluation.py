"""Pre-registered formal evaluation of one top-vs-retail positioning family.

The hypothesis is fixed before this driver observes any result: top-account
positioning can contain informed cross-sectional direction while the broader
account population is a crowding signal to fade.  The complete selection pool
contains those two component controls and their equal-standardized composite.
There is no parameter or sign scan.  The production Paper quantile (30%) is
hash-bound into the evaluation contract and passed explicitly to the evaluator.

This is L2 research only.  A verified formal PASS may enter the independent
forward-paper admission flow, but this driver never admits, promotes, reads
credentials, connects to a broker, creates an execution intent, or submits an
order.  Historical evidence alone never grants Testnet eligibility.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.alpha.config import AlphaFactoryConfig
from services.execution.backtest_authority import quarantine_legacy_backtest
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


DEFAULT_OUTPUT = Path("data/cache/positioning_divergence_evaluation.json")
FWD_HORIZON = 7
PERIODS_PER_YEAR = 52
EVALUATION_QUANTILE = 0.3
DATA_SOURCE = "crypto_oi_pit_panel"
SOURCE = "tools.positioning_divergence_evaluation"
SEARCH_KIND = "PREREGISTERED_TOP_VS_RETAIL_DIVERGENCE_V1"
_COMMIT = re.compile(r"[0-9a-f]{40}")

RETAIL_CONTRA_EXPRESSION = (
    "cs_zscore",
    ("neg", ("field", "global_ls")),
)
TOP_FOLLOW_EXPRESSION = (
    "cs_zscore",
    ("field", "top_ls"),
)
POSITIONING_DIVERGENCE_EXPRESSION = (
    "div",
    (
        "add",
        RETAIL_CONTRA_EXPRESSION,
        TOP_FOLLOW_EXPRESSION,
    ),
    ("const", 2.0),
)

CANDIDATE_EXPRESSIONS: dict[str, tuple] = {
    "retail_contra_control": RETAIL_CONTRA_EXPRESSION,
    "top_follow_control": TOP_FOLLOW_EXPRESSION,
    "top_vs_retail_divergence": POSITIONING_DIVERGENCE_EXPRESSION,
}

ECONOMIC_HYPOTHESES = {
    "retail_contra_control": (
        "Control: broad-account positioning is a crowding signal that should "
        "mean-revert cross-sectionally."
    ),
    "top_follow_control": (
        "Control: top-account positioning may contain informed cross-sectional "
        "direction that persists over the registered holding horizon."
    ),
    "top_vs_retail_divergence": (
        "Top-account and broad-account long/short ratios describe different "
        "participant cohorts; following standardized top positioning while fading "
        "standardized broad-account crowding should isolate informed disagreement."
    ),
}

FALSIFICATION_CRITERIA = (
    "No family member passes the immutable catalog-wide DSR threshold.",
    "The complete three-member CSCV selection population exceeds the PBO ceiling.",
    "A candidate fails leakage, regime, or sequential walk-forward evidence.",
    "The pre-registered PASS-only selector has no verified candidate to select.",
)

EVALUATION_CONTRACT = {
    "driver": "positioning_divergence_evaluation.v1",
    "fwd_horizon": FWD_HORIZON,
    "non_overlapping": True,
    "periods_per_year": PERIODS_PER_YEAR,
    "quantile": EVALUATION_QUANTILE,
    "return_basis": "PRICE",
    "selection_population": list(CANDIDATE_EXPRESSIONS),
    "selection_rule": (
        "highest_net_sharpe_among_verified_formal_pass_then_alias_order.v1"
    ),
    "candidate_expression": ax.serialize(POSITIONING_DIVERGENCE_EXPRESSION),
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


def _definition(alias: str, expression: tuple) -> tuple[AlphaDefinition, str]:
    params = {
        "evaluation_contract": stable_hash(EVALUATION_CONTRACT),
        "expr": ax.serialize(expression),
    }
    seed = AlphaDefinition(
        alpha_id=alias,
        name=alias,
        factor_ids=_field_ids(expression),
        universe="crypto_oi_majors_pit",
        params=params,
    )
    research_id = f"positioning_divergence_{alias}_{seed.definition_hash[:16]}"
    definition = AlphaDefinition(
        alpha_id=research_id,
        name=alias,
        factor_ids=seed.factor_ids,
        universe=seed.universe,
        params=params,
    )
    if definition.definition_hash != seed.definition_hash:
        raise RuntimeError("content-addressed positioning-divergence identity drifted")
    return definition, seed.definition_hash


def build_candidate_batch(panel) -> tuple[list, dict[str, dict], dict[str, dict]]:
    """Build the complete fixed family on one PIT panel before results exist."""
    candidates = []
    evidence: dict[str, dict[str, bool | None]] = {}
    mappings: dict[str, dict[str, Any]] = {}
    for alias, expression in CANDIDATE_EXPRESSIONS.items():
        definition, definition_hash = _definition(alias, expression)
        signal = ax.evaluate(expression, panel)
        periods = ax.panel_to_periods(
            signal,
            panel,
            fwd_horizon=FWD_HORIZON,
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
            "quantile": EVALUATION_QUANTILE,
            "return_basis": "PRICE",
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
        raise RuntimeError("evaluation is not bound to the registered divergence family")
    mapping = mappings[alias]
    if link.get("config_hash") != mapping["definition_hash"]:
        raise RuntimeError("evaluation definition binding mismatch")
    try:
        evaluation = json.loads(str(row["evaluation_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("evaluation payload is invalid") from exc
    return alias, {
        "run_id": run_id,
        "verified": registry.verify_evaluation(run_id) is True,
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


def _decision(evaluations: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    pass_rows: list[tuple[float, int, str]] = []
    human_review_required = False
    alias_order = {alias: index for index, alias in enumerate(CANDIDATE_EXPRESSIONS)}
    for alias, evaluation in evaluations.items():
        if not isinstance(evaluation, Mapping) or evaluation.get("verified") is not True:
            continue
        verdict = evaluation.get("verdict")
        human_review_required = human_review_required or verdict == "REQUIRES_HUMAN_REVIEW"
        net_sharpe = evaluation.get("net_sharpe")
        if verdict != "PASS" or not isinstance(net_sharpe, (int, float)):
            continue
        if isinstance(net_sharpe, bool) or not math.isfinite(float(net_sharpe)):
            continue
        pass_rows.append((-float(net_sharpe), alias_order.get(alias, len(alias_order)), alias))
    selected_alias = min(pass_rows)[2] if pass_rows else None
    formal_pass = selected_alias is not None
    return {
        "selected_alias": selected_alias,
        "formal_evaluation_pass": formal_pass,
        "human_review_required": human_review_required,
        "forward_admission_allowed": formal_pass,
        "testnet_eligible": False,
    }


def _run_legacy_diagnostic(
    *,
    output: str | Path = DEFAULT_OUTPUT,
    recorded_at: int | None = None,
    source_commit: str,
    panel=None,
) -> dict[str, Any]:
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source_commit must be a lowercase 40-character git commit")
    if panel is None:
        from tools.crypto_oi_research import build_panel

        panel, _instruments = build_panel()
    candidates, leakage_evidence, mappings = build_candidate_batch(panel)
    observed_at = int(
        recorded_at if recorded_at is not None else time.time_ns() // 1_000_000
    )
    dataset_version = field_panel_dataset_version(
        panel,
        source_versions=EVALUATION_CONTRACT,
    )
    search_id = make_search_id(
        source=SOURCE,
        search_kind=SEARCH_KIND,
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
        allowed_data_sources=(DATA_SOURCE,),
        stopping_rules={"max_trials": len(CANDIDATE_EXPRESSIONS)},
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
            evidence=leakage_evidence,
            budget=budget,
            parent_hypothesis=(
                "Relative positioning between top and broad account cohorts may "
                "isolate informed disagreement from retail crowding."
            ),
            search_space=(
                "Exactly one retail-contrarian control, one top-following control, "
                "and their fixed equal-standardized composite at quantile 0.30; "
                "no parameter or sign sweep."
            ),
            data_source=DATA_SOURCE,
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
    document = {
        "schema_version": "positioning_divergence_evaluation.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": observed_at,
        "source_commit": source_commit,
        "dataset_version": dataset_version,
        "search_id": search_id,
        "status": "VERIFIED" if complete else "PENDING",
        "evaluation_contract": EVALUATION_CONTRACT,
        "research_budget": budget.metadata(),
        "falsification_criteria": list(FALSIFICATION_CRITERIA),
        "family": rows,
        "decision": _decision(evaluations),
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


def run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    quarantine_legacy_backtest("tools.positioning_divergence_evaluation.run")


def main(argv: Sequence[str] | None = None) -> int:
    quarantine_legacy_backtest("tools.positioning_divergence_evaluation.main")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args(argv)
    try:
        document = run(output=args.output, source_commit=args.source_commit)
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        print(f"positioning divergence evaluation blocked: {type(exc).__name__}: {exc}")
        return 2
    selected = document["decision"]["selected_alias"]
    print(
        "positioning divergence evaluation: "
        f"status={document['status']} selected={selected} "
        f"formal_pass={document['decision']['formal_evaluation_pass']}"
    )
    return 0 if document["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CANDIDATE_EXPRESSIONS",
    "DATA_SOURCE",
    "DEFAULT_OUTPUT",
    "EVALUATION_CONTRACT",
    "EVALUATION_QUANTILE",
    "POSITIONING_DIVERGENCE_EXPRESSION",
    "RETAIL_CONTRA_EXPRESSION",
    "TOP_FOLLOW_EXPRESSION",
    "build_candidate_batch",
    "run",
]
