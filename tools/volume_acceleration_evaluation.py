"""Confirmatory formal evaluation of a frozen legacy volume-finalist family.

The selected subject and all 14 controls are copied from one immutable source
search before this replay observes a result.  The production evaluator supplies
the PBO, leakage, regime, walk-forward, catalog DSR, and provenance evidence
that the legacy evaluations lacked.  This L2 research driver has no credential,
broker, intent, promotion, or order path.
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
from quantos.contracts.cost import CostModel
from quantos.contracts.hashing import stable_hash
from services.alpha.config import AlphaFactoryConfig
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


DAY_MS = 86_400_000
DEFAULT_OUTPUT = Path("data/cache/volume_acceleration_evaluation.json")
SOURCE_SEARCH_ID = "search-36ffa5e2daf9f2a6f6ab8dc5"
SUBJECT_ALIAS = "volume_acceleration_30_7"
FWD_HORIZON = 1
PERIODS_PER_YEAR = 252
QUANTILE = 0.30
DATA_SOURCE = "crypto_oi_pit_panel"
SOURCE = "tools.volume_acceleration_evaluation"
SEARCH_KIND = "CONFIRMATORY_VOLUME_ACCELERATION_FINALISTS_V1"
EVALUATION_COST = CostModel(
    fee_bps=4.0,
    slippage_bps=2.0,
    impact_coef=0.0,
)
_COMMIT = re.compile(r"[0-9a-f]{40}")


FINALIST_EXPRESSIONS: dict[str, tuple] = {
    "volume_demeaned_delta7_scaled": (
        "cs_scale",
        ("ts_delta", ("cs_demean", ("field", "volume")), 7),
    ),
    "volume_delta7_double_demean": (
        "cs_demean",
        ("cs_demean", ("ts_delta", ("field", "volume"), 7)),
    ),
    "volume_delta7_scaled": (
        "cs_scale",
        ("ts_delta", ("field", "volume"), 7),
    ),
    "volume_demeaned_delta7_rank": (
        "cs_rank",
        ("ts_delta", ("cs_demean", ("field", "volume")), 7),
    ),
    "volume_delta7_demean": (
        "cs_demean",
        ("ts_delta", ("field", "volume"), 7),
    ),
    "volume_delta7_demean_rank": (
        "cs_rank",
        ("cs_demean", ("ts_delta", ("field", "volume"), 7)),
    ),
    "volume_delta7_winsorized_zscaled": (
        "cs_scale",
        (
            "cs_zscore",
            ("cs_winsorize", ("ts_delta", ("field", "volume"), 7)),
        ),
    ),
    SUBJECT_ALIAS: (
        "cs_zscore",
        (
            "cs_scale",
            ("ts_delta", ("ts_delta", ("field", "volume"), 30), 7),
        ),
    ),
    "volume_demeaned_double_delta7_rank": (
        "cs_rank",
        (
            "ts_delta",
            ("ts_delta", ("cs_demean", ("field", "volume")), 7),
            7,
        ),
    ),
    "volume_acceleration_30_7_scaled": (
        "cs_scale",
        ("ts_delta", ("ts_delta", ("field", "volume"), 30), 7),
    ),
    "volume_delta7_zscale": (
        "cs_zscore",
        ("cs_scale", ("ts_delta", ("field", "volume"), 7)),
    ),
    "volume_delta7_zscore": (
        "cs_zscore",
        ("ts_delta", ("field", "volume"), 7),
    ),
    "volume_delta7_rank": (
        "cs_rank",
        ("ts_delta", ("field", "volume"), 7),
    ),
    "volume_delta7_zscore_scaled": (
        "cs_scale",
        ("cs_zscore", ("ts_delta", ("field", "volume"), 7)),
    ),
}

SOURCE_CONFIG_HASHES = {
    "volume_demeaned_delta7_scaled": "2bf6cd3dc0107a4ac380b64843cfe85f91fcab0c2d7d69de955f2a0496844840",
    "volume_delta7_double_demean": "f5e8784a1cd88c44235f9e55c61fb5e9a3b16c9c0280a32cbf564b9c6e736d97",
    "volume_delta7_scaled": "272b97c4d2e334d727a6a37dfee1ca8f5f5b272a823a962ed1d5c3702e303c39",
    "volume_demeaned_delta7_rank": "c42c996cb56fd697afbf3abbae936d4ebe89a37cf7d86b7a6afb4cf373fd0540",
    "volume_delta7_demean": "9b15994151a9521fe1e5c45fd8eb1e8e0d91a2ab733160da087388b92bf2498b",
    "volume_delta7_demean_rank": "b81d699e55b4a1fb8e990f7ca7389befb776e5aee5d3f01a60780c9271a2dfc4",
    "volume_delta7_winsorized_zscaled": "337e99952b30e565a0fb639c423426108c692ac9122063cd56cb93e5d3c41054",
    SUBJECT_ALIAS: "c2886d6737d99b8ac2319a2b041d1a5317ce420e4204fd0fd6e0fa96aab849c8",
    "volume_demeaned_double_delta7_rank": "d61b11f981118c870c5f9243b7c381178bbe7d3cbba1c163416e0e1e9dd626ba",
    "volume_acceleration_30_7_scaled": "c66178391eda98b38cb957f227f756b20bf43e7a7b2d41709f8902e5605845cf",
    "volume_delta7_zscale": "ffb49c049939879b9846945a6670a016411cbc9f96e4d65c2501de025e13b2b7",
    "volume_delta7_zscore": "3b59cc0e6fed5c73e32cc94ddda09bc37d2f8e29050a5ff88f11d2a6e9ef56cb",
    "volume_delta7_rank": "01be82856b6d08da14d2c441ecffb7dc5a306a818a9c0f531312c03f3168aa5c",
    "volume_delta7_zscore_scaled": "e682d67828d288b7a7804a24bea2c15593c12501469728ca62fff0212d06c3a5",
}

EVALUATION_CONTRACT = {
    "driver": "volume_acceleration_evaluation.v1",
    "source_search_id": SOURCE_SEARCH_ID,
    "source_config_hashes": SOURCE_CONFIG_HASHES,
    "selected_subject": SUBJECT_ALIAS,
    "fwd_horizon": FWD_HORIZON,
    "periods_per_year": PERIODS_PER_YEAR,
    "quantile": QUANTILE,
    "cost": {"fee_bps": 4.0, "slippage_bps": 2.0, "impact_coef": 0.0},
    "shared_calendar": True,
    "selection_population": tuple(FINALIST_EXPRESSIONS),
    "parameter_scan": False,
}

FALSIFICATION_CRITERIA = (
    "The preselected subject fails catalog-wide DSR.",
    "The complete 14-finalist CSCV population exceeds the PBO ceiling.",
    "Leakage, regime, or sequential walk-forward evidence fails.",
    "The subject fails the production coverage, cost, or redundancy gates.",
)


def _definition(alias: str, expression: tuple) -> tuple[AlphaDefinition, str]:
    params = {
        "evaluation_contract": stable_hash(EVALUATION_CONTRACT),
        "expr": ax.serialize(expression),
    }
    seed = AlphaDefinition(
        alpha_id=alias,
        name=alias,
        factor_ids=("volume",),
        universe="crypto_volume_majors_pit",
        params=params,
    )
    research_id = f"volume_confirm_{alias}_{seed.definition_hash[:16]}"
    definition = AlphaDefinition(
        alpha_id=research_id,
        name=alias,
        factor_ids=seed.factor_ids,
        universe=seed.universe,
        params=params,
    )
    if definition.definition_hash != seed.definition_hash:
        raise RuntimeError("content-addressed volume finalist identity drifted")
    return definition, seed.definition_hash


def build_candidate_batch(panel):
    candidates: list[tuple[AlphaDefinition, tuple]] = []
    evidence: dict[str, dict[str, bool]] = {}
    mappings: dict[str, dict[str, Any]] = {}
    by_time: list[dict[int, Any]] = []
    for alias, expression in FINALIST_EXPRESSIONS.items():
        definition, definition_hash = _definition(alias, expression)
        signal = ax.evaluate(expression, panel)
        periods = tuple(
            ax.panel_to_periods(
                signal,
                panel,
                fwd_horizon=FWD_HORIZON,
                non_overlapping=False,
            )
        )
        if any(period.event_time is None for period in periods):
            raise RuntimeError("volume finalist calendar requires event_time")
        indexed = {int(period.event_time): period for period in periods}
        if len(indexed) != len(periods):
            raise RuntimeError("volume finalist calendar contains duplicate event_time")
        candidates.append((definition, periods))
        by_time.append(indexed)
        evidence[definition.alpha_id] = {
            "leakage_ok": compute_leakage_ok(expression, panel)
        }
        mappings[alias] = {
            "alias": alias,
            "research_alpha_id": definition.alpha_id,
            "definition_hash": definition_hash,
            "source_config_hash": SOURCE_CONFIG_HASHES[alias],
            "expression": ax.serialize(expression),
            "economic_hypothesis": (
                "Cross-sectional acceleration in traded volume leads a persistent "
                "next-day attention/liquidity response after realistic turnover cost."
            ),
        }
    common_times = sorted(set.intersection(*(set(item) for item in by_time)))
    selected_times: list[int] = []
    for event_time in common_times:
        if not selected_times or event_time - selected_times[-1] >= DAY_MS:
            selected_times.append(event_time)
    if not selected_times:
        raise RuntimeError("volume finalist family has no shared causal calendar")
    candidates = [
        (
            definition,
            tuple(by_time[index][event_time] for event_time in selected_times),
        )
        for index, (definition, _periods) in enumerate(candidates)
    ]
    for mapping in mappings.values():
        mapping["period_count"] = len(selected_times)
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
    if len(links) != 1 or links[0].get("evaluation_content_hash") != row.get(
        "content_hash"
    ):
        raise RuntimeError("evaluation has no unique immutable search binding")
    research_id = str(row.get("alpha_id", ""))
    alias = next(
        (
            name
            for name, mapping in mappings.items()
            if mapping["research_alpha_id"] == research_id
        ),
        None,
    )
    if alias is None or links[0].get("config_hash") != mappings[alias][
        "definition_hash"
    ]:
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
        "observed_sr": evaluation.get("observed_sr"),
        "n_periods": evaluation.get("n_periods"),
        "regime_profitable_frac": evaluation.get("regime_profitable_frac"),
        "catalog_trial_count": evaluation.get("catalog_trial_count"),
        "reasons": list(evaluation.get("reasons") or ()),
        "content_hash": row.get("content_hash"),
    }


def _decision(evaluation: Mapping[str, Any] | None) -> dict[str, bool]:
    verified = isinstance(evaluation, Mapping) and evaluation.get("verified") is True
    formal_pass = verified and evaluation.get("verdict") == "PASS"
    return {
        "formal_evaluation_pass": formal_pass,
        "human_review_required": (
            verified and evaluation.get("verdict") == "REQUIRES_HUMAN_REVIEW"
        ),
        "forward_admission_allowed": formal_pass,
        "testnet_eligible": False,
    }


def run(
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
        params={"source_commit": source_commit, "subject_alias": SUBJECT_ALIAS},
    )
    budget = ResearchBudget(
        max_trials=len(FINALIST_EXPRESSIONS),
        max_feature_count=1,
        max_parameter_dimensions=2,
        allowed_data_sources=(DATA_SOURCE,),
        stopping_rules={"max_trials": len(FINALIST_EXPRESSIONS)},
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
            quantile=QUANTILE,
            config=AlphaFactoryConfig(compute_regime_evidence=True),
            cost=EVALUATION_COST,
            evidence=leakage_evidence,
            budget=budget,
            parent_hypothesis=(
                "A previously selected cross-sectional volume-acceleration "
                "finalist retains next-day net edge under complete evidence."
            ),
            search_space=(
                "Exactly the 14 immutable evaluated finalists from source search "
                f"{SOURCE_SEARCH_ID}; selected subject fixed before replay."
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
    subject_evaluation = evaluations.get(SUBJECT_ALIAS)
    document = {
        "schema_version": "volume_acceleration_evaluation.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": observed_at,
        "source_commit": source_commit,
        "dataset_version": dataset_version,
        "search_id": search_id,
        "source_search_id": SOURCE_SEARCH_ID,
        "selected_subject": SUBJECT_ALIAS,
        "status": "VERIFIED" if complete else "PENDING",
        "research_budget": budget.metadata(),
        "falsification_criteria": list(FALSIFICATION_CRITERIA),
        "family": rows,
        "decision": _decision(subject_evaluation),
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args(argv)
    try:
        document = run(output=args.output, source_commit=args.source_commit)
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        print(f"volume acceleration evaluation blocked: {type(exc).__name__}: {exc}")
        return 2
    candidate = document["family"][SUBJECT_ALIAS]["evaluation"] or {}
    print(
        "volume acceleration evaluation: "
        f"status={document['status']} verdict={candidate.get('verdict')} "
        f"dsr={candidate.get('deflated_sharpe')} pbo={candidate.get('pbo')}"
    )
    return 0 if document["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DATA_SOURCE",
    "DAY_MS",
    "DEFAULT_OUTPUT",
    "EVALUATION_COST",
    "FINALIST_EXPRESSIONS",
    "SUBJECT_ALIAS",
    "build_candidate_batch",
    "run",
]
