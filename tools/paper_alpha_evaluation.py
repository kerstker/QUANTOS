"""Evaluate the three deployed paper Alphas under immutable research identities.

Deployment aliases such as ``crowd_contra`` are intentionally stable, while
their research definitions may evolve.  The experiment registry is
append-only and correctly refuses to overwrite an existing alias with a
different definition.  This driver therefore derives a versioned research
identity from each exact definition hash, evaluates the complete current pool,
and publishes the verified deployment-to-research mapping consumed by the
paper research-risk producer.

The driver is L2 research only.  It has no broker, credential, order, approval,
or live-trading path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.alpha.config import AlphaFactoryConfig
from services.intelligence.registered_candidate_batch import (
    run_registered_candidate_batch,
)
from services.research import alpha_expression as ax
from services.research.expression_leakage import compute_leakage_ok
from tools.cache_io import atomic_write_json
from tools.crypto_oi_verify import CROWD_CONTRA, TOP_CONTRA
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
)


DEFAULT_OUTPUT = Path("evidence/paper_alpha_evaluations.json")
FWD_HORIZON = 7
PERIODS_PER_YEAR = 52
DATASET_CONTRACT = {
    "driver": "paper_alpha_evaluation.v1",
    "fwd_horizon": FWD_HORIZON,
    "non_overlapping": True,
    "periods_per_year": PERIODS_PER_YEAR,
}
PANEL_BACKTEST_CONTRACT = "sorted_cost_accumulation.v1"


def _paper_specs() -> tuple[tuple[str, str, object, tuple[str, ...]], ...]:
    # Deferred import keeps module import free of data/cache access.
    from tools.crypto_oi_research import CARRY

    return (
        (
            "carry_oi_majors",
            "Carry - funding-rate composite",
            CARRY,
            ("funding",),
        ),
        (
            "crowd_contra",
            "Crowd Contra - fade crowded retail positioning",
            CROWD_CONTRA,
            ("global_ls",),
        ),
        (
            "top_contra",
            "Top Contra - fade crowded top-trader positioning",
            TOP_CONTRA,
            ("top_ls",),
        ),
    )


def _research_identity(
    deployed_alpha_id: str,
    *,
    expression: object,
    factor_ids: tuple[str, ...],
) -> tuple[str, str]:
    evaluation_contract = stable_hash(
        {
            **DATASET_CONTRACT,
            "panel_backtest_contract": PANEL_BACKTEST_CONTRACT,
        }
    )
    definition = AlphaDefinition(
        alpha_id=deployed_alpha_id,
        name=deployed_alpha_id,
        factor_ids=factor_ids,
        universe="cs_majors",
        params={
            "evaluation_contract": evaluation_contract,
            "expr": ax.serialize(expression),
        },
    )
    digest = definition.definition_hash
    return f"paper_{deployed_alpha_id}_{digest[:16]}", digest


def build_paper_candidates(panel) -> tuple[list, dict[str, dict], dict[str, dict]]:
    """Build exact versioned candidates plus their deployed alias mapping."""
    candidates = []
    evidence: dict[str, dict[str, bool | None]] = {}
    mappings: dict[str, dict[str, str]] = {}
    for deployed, name, expression, factor_ids in _paper_specs():
        research_id, definition_hash = _research_identity(
            deployed,
            expression=expression,
            factor_ids=factor_ids,
        )
        evaluation_contract = stable_hash(
            {
                **DATASET_CONTRACT,
                "panel_backtest_contract": PANEL_BACKTEST_CONTRACT,
            }
        )
        definition = AlphaDefinition(
            alpha_id=research_id,
            name=name,
            factor_ids=factor_ids,
            universe="cs_majors",
            params={
                "evaluation_contract": evaluation_contract,
                "expr": ax.serialize(expression),
            },
        )
        matrix = ax.evaluate(expression, panel)
        periods = ax.panel_to_periods(
            matrix,
            panel,
            fwd_horizon=FWD_HORIZON,
            non_overlapping=True,
        )
        candidates.append((definition, periods))
        evidence[research_id] = {
            "leakage_ok": compute_leakage_ok(expression, panel)
        }
        mappings[deployed] = {
            "deployed_alpha_id": deployed,
            "research_alpha_id": research_id,
            "definition_hash": definition_hash,
        }
    by_id = {definition.alpha_id: definition for definition, _ in candidates}
    for mapping in mappings.values():
        definition = by_id[mapping["research_alpha_id"]]
        if definition.definition_hash != mapping["definition_hash"]:
            raise ValueError("paper research identity is not bound to its definition")
    return candidates, evidence, mappings


def evaluation_dataset_version(panel) -> str:
    return field_panel_dataset_version(panel, source_versions=DATASET_CONTRACT)


def run(
    *,
    output: str | Path = DEFAULT_OUTPUT,
    recorded_at: int | None = None,
) -> dict[str, Any]:
    from tools.crypto_oi_research import build_panel

    observed_at = int(recorded_at if recorded_at is not None else time.time_ns() // 1_000_000)
    panel, _ = build_panel()
    candidates, leakage_evidence, mappings = build_paper_candidates(panel)
    dataset_version = evaluation_dataset_version(panel)
    search_id = make_search_id(
        source="tools.paper_alpha_evaluation",
        search_kind="PAPER_ALPHA_EXACT_EVALUATION",
        dataset_version=dataset_version,
        recorded_at=observed_at,
        params=DATASET_CONTRACT,
    )

    with production_experiment_registry() as registry:
        result = run_registered_candidate_batch(
            candidates,
            registry=registry,
            search_id=search_id,
            search_kind="PAPER_ALPHA_EXACT_EVALUATION",
            search_source="tools.paper_alpha_evaluation",
            dataset_version=dataset_version,
            recorded_at=observed_at,
            periods_per_year=PERIODS_PER_YEAR,
            config=AlphaFactoryConfig(compute_regime_evidence=True),
            evidence=leakage_evidence,
        )
        rows_by_research_id: dict[str, dict[str, Any]] = {}
        for run_id in result.get("recorded_run_ids", ()):
            row = registry.get_evaluation(str(run_id))
            if not isinstance(row, dict):
                continue
            research_id = str(row.get("alpha_id", ""))
            try:
                evaluation = json.loads(str(row["evaluation_json"]))
            except (KeyError, TypeError, json.JSONDecodeError):
                evaluation = {}
            rows_by_research_id[research_id] = {
                "run_id": str(run_id),
                "verified": registry.verify_evaluation(str(run_id)),
                "verdict": evaluation.get("verdict"),
                "deflated_sharpe": evaluation.get("deflated_sharpe"),
                "pbo": evaluation.get("pbo"),
                "as_of": evaluation.get("as_of"),
                "reasons": list(evaluation.get("reasons") or ()),
                "dataset_version": row.get("dataset_version"),
                "content_hash": row.get("content_hash"),
            }

    deployed_rows: dict[str, dict[str, Any]] = {}
    for deployed, mapping in mappings.items():
        evaluation = rows_by_research_id.get(mapping["research_alpha_id"])
        deployed_rows[deployed] = {
            **mapping,
            "evaluation": evaluation,
            "status": (
                "VERIFIED"
                if isinstance(evaluation, dict) and evaluation.get("verified") is True
                else "PENDING"
            ),
        }
    complete = all(row["status"] == "VERIFIED" for row in deployed_rows.values())
    document = {
        "schema_version": "paper_alpha_evaluations.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": observed_at,
        "dataset_version": dataset_version,
        "search_id": search_id,
        "status": "VERIFIED" if complete else "PENDING",
        "alphas": deployed_rows,
        "safety": {
            "paper_only": True,
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
        },
    }
    atomic_write_json(str(output), document, indent=2)
    return document


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the three L2 paper Alphas and verify immutable evidence."
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    document = run(output=args.output)
    print(
        "paper alpha evaluation: "
        f"status={document['status']} "
        f"verified={sum(row['status'] == 'VERIFIED' for row in document['alphas'].values())}/3"
    )
    return 0 if document["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
