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
from collections.abc import Mapping
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
from services.research.carry_pnl import funding_adjusted_periods
from services.research.expression_leakage import compute_leakage_ok
from tools.cache_io import atomic_write_json
from tools.crypto_oi_verify import CROWD_CONTRA, TOP_CONTRA
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
)


DEFAULT_OUTPUT = Path("evidence/paper_alpha_evaluations.json")
DEFAULT_FORWARD_OUTPUT = Path("data/cache/forward_alpha_evaluations.json")
FWD_HORIZON = 7
PERIODS_PER_YEAR = 52
DATASET_CONTRACT = {
    "driver": "paper_alpha_evaluation.v1",
    "fwd_horizon": FWD_HORIZON,
    "non_overlapping": True,
    "periods_per_year": PERIODS_PER_YEAR,
}
PANEL_BACKTEST_CONTRACT = "sorted_cost_accumulation.v1"
PAPER_RETURN_BASIS = {
    "carry_oi_majors": "PRICE_PLUS_FUNDING",
    "crowd_contra": "PRICE",
    "top_contra": "PRICE",
}
PAPER_EVALUATION_CONTRACT = {
    **DATASET_CONTRACT,
    "funding_accrual": "complete_rows_after_decision_through_horizon.v1",
    "panel_backtest_contract": PANEL_BACKTEST_CONTRACT,
    "return_basis_by_alpha": PAPER_RETURN_BASIS,
}
FORWARD_EVALUATION_CONTRACT = "forward_candidate_independent_evaluation.v1"
FORWARD_PERIOD_SAMPLING = "global_panel_calendar_non_overlapping.v1"


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
        PAPER_EVALUATION_CONTRACT
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
        evaluation_contract = stable_hash(PAPER_EVALUATION_CONTRACT)
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
        return_basis = PAPER_RETURN_BASIS.get(deployed)
        if return_basis == "PRICE_PLUS_FUNDING":
            periods = funding_adjusted_periods(
                matrix,
                panel,
                fwd_horizon=FWD_HORIZON,
                non_overlapping=True,
            )
        elif return_basis == "PRICE":
            periods = ax.panel_to_periods(
                matrix,
                panel,
                fwd_horizon=FWD_HORIZON,
                non_overlapping=True,
            )
        else:
            raise ValueError(f"unknown paper return basis for {deployed}")
        candidates.append((definition, periods))
        evidence[research_id] = {
            "leakage_ok": compute_leakage_ok(expression, panel)
        }
        mappings[deployed] = {
            "deployed_alpha_id": deployed,
            "research_alpha_id": research_id,
            "definition_hash": definition_hash,
            "return_basis": return_basis,
        }
    by_id = {definition.alpha_id: definition for definition, _ in candidates}
    for mapping in mappings.values():
        definition = by_id[mapping["research_alpha_id"]]
        if definition.definition_hash != mapping["definition_hash"]:
            raise ValueError("paper research identity is not bound to its definition")
    return candidates, evidence, mappings


def evaluation_dataset_version(panel) -> str:
    return field_panel_dataset_version(
        panel,
        source_versions=PAPER_EVALUATION_CONTRACT,
    )


def forward_evaluation_dataset_version(panel) -> str:
    return field_panel_dataset_version(panel, source_versions=DATASET_CONTRACT)


def _field_ids(expression: object) -> tuple[str, ...]:
    """Return the exact field dependencies encoded in a DSL expression."""
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
    return tuple(sorted(fields)) or ("expression_only",)


def _forward_specs() -> tuple[tuple[str, object, tuple[str, ...], float, int], ...]:
    """Enumerate the exact forward universe, including verified factory rows."""
    from tools.forward_track_advance import _ALPHAS, _alpha_spec, _registered_alphas

    registered, _dynamic_ids = _registered_alphas()
    baseline_ids = {item[0] for item in _paper_specs()}
    specs: list[tuple[str, object, tuple[str, ...], float, int]] = []
    for deployed, entry in registered.items():
        if deployed in baseline_ids:
            continue
        spec = _alpha_spec(entry)
        expression = spec.get("expr")
        if expression is None:
            raise RuntimeError(f"forward candidate {deployed} has no expression")
        specs.append(
            (
                str(deployed),
                expression,
                _field_ids(expression),
                float(spec["quantile"]),
                int(spec["cadence_days"]),
            )
        )
    # Keep the static source import in the function body visible to the
    # governance scanner and fail closed if a future registry implementation
    # accidentally drops a declared static variant.
    if not _ALPHAS:
        raise RuntimeError("forward candidate registry is empty")
    return tuple(specs)


def _forward_research_identity(
    deployed_alpha_id: str,
    *,
    expression: object,
    factor_ids: tuple[str, ...],
    quantile: float,
    cadence_days: int,
) -> tuple[str, str]:
    evaluation_contract = stable_hash(
        {
            "driver": FORWARD_EVALUATION_CONTRACT,
            **DATASET_CONTRACT,
            "panel_backtest_contract": PANEL_BACKTEST_CONTRACT,
            "period_sampling": FORWARD_PERIOD_SAMPLING,
            "quantile": quantile,
            "cadence_days": cadence_days,
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
            "quantile": str(quantile),
            "cadence_days": str(cadence_days),
        },
    )
    return f"forward_{deployed_alpha_id}_{definition.definition_hash[:16]}", definition.definition_hash


def build_forward_candidates(panel) -> tuple[list, dict[str, dict], dict[str, dict]]:
    """Build all non-baseline forward candidates with immutable identities."""
    # The default period builder advances the non-overlap cursor only after a
    # candidate has enough valid names.  That is correct for a single alpha but
    # makes a selection pool use different calendars when a challenger has
    # sparse data.  Build daily labels once, then keep the same global panel-row
    # anchors for every candidate.  Missing candidate rows still drop out;
    # labels never change and no future row is introduced.
    global_anchors = {
        int(panel.event_times[row])
        for row in range(0, max(0, len(panel.event_times) - FWD_HORIZON), FWD_HORIZON)
    }
    candidates = []
    evidence: dict[str, dict[str, bool | None]] = {}
    mappings: dict[str, dict[str, Any]] = {}
    for deployed, expression, factor_ids, quantile, cadence_days in _forward_specs():
        research_id, definition_hash = _forward_research_identity(
            deployed,
            expression=expression,
            factor_ids=factor_ids,
            quantile=quantile,
            cadence_days=cadence_days,
        )
        evaluation_contract = stable_hash(
            {
                "driver": FORWARD_EVALUATION_CONTRACT,
                **DATASET_CONTRACT,
                "panel_backtest_contract": PANEL_BACKTEST_CONTRACT,
                "period_sampling": FORWARD_PERIOD_SAMPLING,
                "quantile": quantile,
                "cadence_days": cadence_days,
            }
        )
        definition = AlphaDefinition(
            alpha_id=research_id,
            name=deployed,
            factor_ids=factor_ids,
            universe="cs_majors",
            params={
                "evaluation_contract": evaluation_contract,
                "expr": ax.serialize(expression),
                "quantile": str(quantile),
                "cadence_days": str(cadence_days),
            },
        )
        matrix = ax.evaluate(expression, panel)
        periods = ax.panel_to_periods(
            matrix,
            panel,
            fwd_horizon=FWD_HORIZON,
            non_overlapping=False,
        )
        periods = [
            period for period in periods if period.event_time in global_anchors
        ]
        candidates.append((definition, periods))
        evidence[research_id] = {
            "leakage_ok": compute_leakage_ok(expression, panel)
        }
        mappings[deployed] = {
            "deployed_alpha_id": deployed,
            "research_alpha_id": research_id,
            "definition_hash": definition_hash,
            "quantile": quantile,
            "cadence_days": cadence_days,
            "factor_ids": list(factor_ids),
        }
    return candidates, evidence, mappings


def _forward_period_counts(
    path: str | Path = "data/cache/forward_track_status.json",
) -> dict[str, int | None]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    rows = document.get("alphas") if isinstance(document, Mapping) else None
    if not isinstance(rows, Mapping):
        return {}
    result: dict[str, int | None] = {}
    for alpha_id, row in rows.items():
        value = row.get("n_forward_periods") if isinstance(row, Mapping) else None
        result[str(alpha_id)] = (
            int(value)
            if isinstance(value, int) and not isinstance(value, bool)
            else None
        )
    return result


def run(
    *,
    output: str | Path = DEFAULT_OUTPUT,
    recorded_at: int | None = None,
) -> dict[str, Any]:
    from tools.crypto_oi_research import build_panel

    panel, _ = build_panel()
    candidates, leakage_evidence, mappings = build_paper_candidates(panel)
    dataset_version = evaluation_dataset_version(panel)
    # Freeze the authority clock only after every expensive input has been
    # assembled.  Candidate/leakage construction can take tens of seconds;
    # taking the timestamp before it lets another scheduled research process
    # legitimately advance the global STARTED clock and makes this batch look
    # like a rollback.  Explicit caller time remains exact for deterministic
    # tests and replay.
    observed_at = int(
        recorded_at if recorded_at is not None else time.time_ns() // 1_000_000
    )
    search_id = make_search_id(
        source="tools.paper_alpha_evaluation",
        search_kind="PAPER_ALPHA_EXACT_EVALUATION",
        dataset_version=dataset_version,
        recorded_at=observed_at,
        params=PAPER_EVALUATION_CONTRACT,
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


def run_forward(
    *,
    output: str | Path = DEFAULT_FORWARD_OUTPUT,
    recorded_at: int | None = None,
    forward_status: str | Path = "data/cache/forward_track_status.json",
) -> dict[str, Any]:
    """Evaluate every forward challenger through the independent registry."""
    from tools.crypto_oi_research import build_panel

    panel, _ = build_panel()
    candidates, leakage_evidence, mappings = build_forward_candidates(panel)
    dataset_version = forward_evaluation_dataset_version(panel)
    observed_at = int(
        recorded_at if recorded_at is not None else time.time_ns() // 1_000_000
    )
    search_id = make_search_id(
        source="tools.paper_alpha_evaluation",
        search_kind=FORWARD_EVALUATION_CONTRACT,
        dataset_version=dataset_version,
        recorded_at=observed_at,
        params={**DATASET_CONTRACT, "candidate_count": len(candidates)},
    )

    with production_experiment_registry() as registry:
        result = run_registered_candidate_batch(
            candidates,
            registry=registry,
            search_id=search_id,
            search_kind=FORWARD_EVALUATION_CONTRACT,
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
            if not isinstance(row, Mapping):
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

    forward_periods = _forward_period_counts(forward_status)
    deployed_rows: dict[str, dict[str, Any]] = {}
    for deployed, mapping in mappings.items():
        evaluation = rows_by_research_id.get(mapping["research_alpha_id"])
        verified = isinstance(evaluation, Mapping) and evaluation.get("verified") is True
        deployed_rows[deployed] = {
            **mapping,
            "forward_periods": forward_periods.get(deployed),
            "evaluation": evaluation,
            "status": "VERIFIED" if verified else "PENDING",
        }
    complete = bool(deployed_rows) and all(
        row["status"] == "VERIFIED" for row in deployed_rows.values()
    )
    document = {
        "schema_version": "forward_alpha_evaluations.v1",
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
        description="Evaluate L2 paper Alphas and forward challengers with immutable evidence."
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--forward-output",
        default=str(DEFAULT_FORWARD_OUTPUT),
        help="publish independent evaluation evidence for every forward challenger",
    )
    args = parser.parse_args(argv)
    document = run(output=args.output)
    forward_document = run_forward(output=args.forward_output)
    print(
        "paper alpha evaluation: "
        f"status={document['status']} "
        f"verified={sum(row['status'] == 'VERIFIED' for row in document['alphas'].values())}/3"
    )
    print(
        "forward challenger evaluation: "
        f"status={forward_document['status']} "
        f"verified={sum(row['status'] == 'VERIFIED' for row in forward_document['alphas'].values())}/"
        f"{len(forward_document['alphas'])}"
    )
    return 0 if document["status"] == "VERIFIED" and forward_document["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
