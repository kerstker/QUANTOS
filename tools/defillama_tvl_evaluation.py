"""PIT-gated preregistered DefiLlama chain-TVL alpha evaluation.

The family is fixed before chain histories are loaded: 30-day TVL growth, TVL
growth acceleration, and TVL growth relative to price growth.  There is no
sign, window, horizon, quantile, or parameter scan.  All candidates share one
no-overlap decision calendar and pass through catalog-wide DSR/PBO, leakage,
regime, and walk-forward gates.

Current Free API snapshots are retrospective and have no historical vintages,
so ``run`` blocks before trial registration.  The fixed family and attachment
path are retained for diagnostic work and a future forward-vintage artifact.

This is L2 public-data research only.  It does not read credentials, connect to
a broker, grant Testnet admission, or create/submit orders.  Deployment data
entitlement remains a separate human review.
"""
from __future__ import annotations

import argparse
import hashlib
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
from services.intelligence.registered_candidate_batch import run_registered_candidate_batch
from services.research import alpha_expression as ax
from services.research.expression_leakage import compute_leakage_ok
from services.research.research_budget import ResearchBudget
from tools.cache_io import atomic_write_json
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
)


DEFAULT_SOURCE = Path("data/cache/defillama_chain_tvl.json")
DEFAULT_OUTPUT = Path("data/cache/defillama_tvl_evaluation.json")
FWD_HORIZON = 7
TVL_WINDOW = 30
PERIODS_PER_YEAR = 52
EVALUATION_QUANTILE = 0.3
MIN_CROSS_SECTION = 10
MIN_COMMON_ROWS = 730
DATA_SOURCE = "crypto_price_pit_panel+defillama_chain_tvl.json"
SOURCE = "tools.defillama_tvl_evaluation"
SEARCH_KIND = "PREREGISTERED_DEFILLAMA_CHAIN_TVL_V1"
SOURCE_BOUNDARY = "DefiLlama Free API / no authentication"
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


_TVL_LOG_CHANGE = (
    "ts_delta", ("log", ("field", "defillama_chain_tvl")), TVL_WINDOW
)
TVL_GROWTH_EXPRESSION = ("cs_zscore", _TVL_LOG_CHANGE)
TVL_ACCELERATION_EXPRESSION = (
    "cs_zscore",
    ("sub", _TVL_LOG_CHANGE, ("ts_delay", _TVL_LOG_CHANGE, TVL_WINDOW)),
)
TVL_PRICE_DIVERGENCE_EXPRESSION = (
    "cs_zscore",
    (
        "sub",
        _TVL_LOG_CHANGE,
        ("ts_delta", ("log", ("field", "close")), TVL_WINDOW),
    ),
)

CANDIDATE_EXPRESSIONS: dict[str, tuple] = {
    "chain_tvl_growth": TVL_GROWTH_EXPRESSION,
    "chain_tvl_acceleration": TVL_ACCELERATION_EXPRESSION,
    "chain_tvl_price_divergence": TVL_PRICE_DIVERGENCE_EXPRESSION,
}

ECONOMIC_HYPOTHESES = {
    "chain_tvl_growth": (
        "Chains with stronger thirty-day DeFi TVL growth may have improving capital "
        "use and demand relative to peers."
    ),
    "chain_tvl_acceleration": (
        "Chains whose thirty-day TVL growth improves versus the prior thirty days may "
        "be entering a new adoption phase rather than merely remaining large."
    ),
    "chain_tvl_price_divergence": (
        "TVL growth that outpaces the native asset's thirty-day price growth may "
        "identify fundamental use not yet fully reflected in price."
    ),
}

FALSIFICATION_CRITERIA = (
    "No family member passes the immutable catalog-wide DSR threshold.",
    "The complete three-member CSCV selection population exceeds the PBO ceiling.",
    "A candidate fails leakage, regime, or sequential walk-forward evidence.",
    "The PASS-only selector has no verified candidate to admit to forward paper.",
)

EVALUATION_CONTRACT = {
    "driver": "defillama_tvl_evaluation.v1",
    "tvl_window_days": TVL_WINDOW,
    "fwd_horizon": FWD_HORIZON,
    "non_overlapping": True,
    "periods_per_year": PERIODS_PER_YEAR,
    "quantile": EVALUATION_QUANTILE,
    "return_basis": "PRICE",
    "selection_population": list(CANDIDATE_EXPRESSIONS),
    "selection_rule": "highest_net_sharpe_among_verified_formal_pass_then_alias_order.v1",
    "parameter_scan": False,
    "shared_decision_calendar": True,
    "decision_calendar_rule": (
        "first family-common row with at least ten tradable instruments, then the "
        "next family-common row at least seven panel rows later"
    ),
    "source_boundary": SOURCE_BOUNDARY,
}


def _positive_series(raw: object, *, asset: str) -> dict[int, float]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[int, float] = {}
    try:
        for timestamp, value in raw.items():
            numeric = float(value)
            if math.isfinite(numeric) and numeric > 0:
                result[int(timestamp)] = numeric
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid DefiLlama TVL series for {asset}") from exc
    return result


def attach_chain_tvl(
    panel,
    snapshot: Mapping[str, Any],
    *,
    min_instruments: int = MIN_CROSS_SECTION,
    min_common_rows: int = MIN_COMMON_ROWS,
) -> dict[str, Any]:
    if snapshot.get("schema_version") != 1:
        raise RuntimeError("unsupported DefiLlama snapshot schema")
    if snapshot.get("provenance") != "REAL_DEFILLAMA_FREE_CHAIN_TVL_HISTORY":
        raise RuntimeError("DefiLlama provenance is not authoritative")
    if snapshot.get("source_boundary") != SOURCE_BOUNDARY:
        raise RuntimeError("DefiLlama source boundary is missing or changed")
    if snapshot.get("pit_admissible_for_promotion") is not False:
        raise RuntimeError("DefiLlama PIT admission flag is missing or invalid")
    if snapshot.get("revision_policy") != "RETROSPECTIVE_REVISABLE_NO_VINTAGES":
        raise RuntimeError("DefiLlama revision policy is missing or changed")
    source_series = snapshot.get("series")
    if not isinstance(source_series, Mapping):
        raise RuntimeError("DefiLlama series is missing")
    close = panel.matrices.get("close")
    if not isinstance(close, list):
        raise RuntimeError("base panel has no close matrix")

    normalized = {
        instrument: _positive_series(
            (source_series.get(instrument) or {}).get("tvl")
            if isinstance(source_series.get(instrument), Mapping)
            else None,
            asset=instrument,
        )
        for instrument in panel.instruments
    }
    matrix: list[list[float | None]] = []
    common_rows_by_instrument = {instrument: 0 for instrument in panel.instruments}
    usable_rows = 0
    for row_index, event_time in enumerate(panel.event_times):
        row = []
        row_common = 0
        for column, instrument in enumerate(panel.instruments):
            value = normalized[instrument].get(int(event_time))
            row.append(value)
            if value is not None and close[row_index][column] is not None:
                common_rows_by_instrument[instrument] += 1
                row_common += 1
        matrix.append(row)
        if row_common >= min_instruments:
            usable_rows += 1
    covered_instruments = sum(
        count >= min_common_rows for count in common_rows_by_instrument.values()
    )
    if covered_instruments < min_instruments:
        raise RuntimeError(
            f"DefiLlama covered instruments {covered_instruments} < {min_instruments}"
        )
    if usable_rows < min_common_rows:
        raise RuntimeError(f"DefiLlama usable rows {usable_rows} < {min_common_rows}")
    panel.matrices["defillama_chain_tvl"] = matrix
    return {
        "covered_instruments": int(covered_instruments),
        "usable_rows": usable_rows,
        "min_instruments_per_usable_row": min_instruments,
        "minimum_common_rows": min_common_rows,
        "common_rows_by_instrument": dict(sorted(common_rows_by_instrument.items())),
        "pit_admissible_for_promotion": False,
    }


def load_panel(source_snapshot: str | Path = DEFAULT_SOURCE) -> tuple[Any, dict, str]:
    path = Path(source_snapshot)
    payload = path.read_bytes()
    try:
        snapshot = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("DefiLlama artifact is invalid JSON") from exc
    series = snapshot.get("series")
    if not isinstance(series, Mapping):
        raise RuntimeError("DefiLlama artifact has no series")
    from tools.crypto_oi_research import build_panel

    panel, _instruments = build_panel(only=tuple(str(asset).upper() for asset in series))
    coverage = attach_chain_tvl(panel, snapshot)
    return panel, coverage, hashlib.sha256(payload).hexdigest()


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
        universe="defillama_chain_tvl_binance_price_pit",
        params=params,
    )
    research_id = f"defillama_tvl_{alias}_{seed.definition_hash[:16]}"
    definition = AlphaDefinition(
        alpha_id=research_id,
        name=alias,
        factor_ids=seed.factor_ids,
        universe=seed.universe,
        params=params,
    )
    if definition.definition_hash != seed.definition_hash:
        raise RuntimeError("content-addressed DefiLlama identity drifted")
    return definition, seed.definition_hash


def _shared_decision_rows(signals: Sequence[list], panel) -> tuple[int, ...]:
    price = panel.matrices["close"]
    eligible: list[int] = []
    for row_index in range(len(panel.event_times) - FWD_HORIZON):
        forward_index = row_index + FWD_HORIZON
        if all(
            sum(
                value is not None
                and math.isfinite(value)
                and price[row_index][column] is not None
                and price[forward_index][column] is not None
                and price[row_index][column] > 0
                for column, value in enumerate(signal[row_index])
            )
            >= MIN_CROSS_SECTION
            for signal in signals
        ):
            eligible.append(row_index)
    selected: list[int] = []
    for row_index in eligible:
        if not selected or row_index - selected[-1] >= FWD_HORIZON:
            selected.append(row_index)
    if len(selected) < 4:
        raise RuntimeError("shared decision calendar has fewer than four periods")
    return tuple(selected)


def build_candidate_batch(panel) -> tuple[list, dict[str, dict], dict[str, dict]]:
    candidates = []
    evidence: dict[str, dict[str, bool | None]] = {}
    mappings: dict[str, dict[str, Any]] = {}
    prepared = []
    for alias, expression in CANDIDATE_EXPRESSIONS.items():
        definition, definition_hash = _definition(alias, expression)
        signal = ax.evaluate(expression, panel)
        prepared.append((alias, expression, definition, definition_hash, signal))
    decision_rows = _shared_decision_rows([item[4] for item in prepared], panel)
    decision_row_set = set(decision_rows)
    expected_calendar = tuple(int(panel.event_times[row]) for row in decision_rows)
    for alias, expression, definition, definition_hash, signal in prepared:
        masked_signal = [
            list(row) if row_index in decision_row_set else [None] * len(row)
            for row_index, row in enumerate(signal)
        ]
        periods = ax.panel_to_periods(
            masked_signal, panel, fwd_horizon=FWD_HORIZON, non_overlapping=False
        )
        if tuple(period.event_time for period in periods) != expected_calendar:
            raise RuntimeError("candidate period calendar drifted from shared calendar")
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


def _evaluation_row(registry, search_id: str, run_id: object, mappings) -> tuple | None:
    run_id = str(run_id)
    row = registry.get_evaluation(run_id)
    if not isinstance(row, Mapping):
        return None
    links = [
        link for link in registry.search_evaluations(search_id)
        if isinstance(link, Mapping) and str(link.get("run_id", "")) == run_id
    ]
    if len(links) != 1:
        raise RuntimeError("evaluation has no unique immutable search binding")
    link = links[0]
    if link.get("evaluation_content_hash") != row.get("content_hash"):
        raise RuntimeError("evaluation content binding mismatch")
    research_id = str(row.get("alpha_id", ""))
    alias = next(
        (name for name, mapping in mappings.items()
         if mapping["research_alpha_id"] == research_id),
        None,
    )
    if alias is None:
        raise RuntimeError("evaluation is not bound to the registered TVL family")
    if link.get("config_hash") != mappings[alias]["definition_hash"]:
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
        if verdict != "PASS" or isinstance(net_sharpe, bool):
            continue
        if not isinstance(net_sharpe, (int, float)) or not math.isfinite(float(net_sharpe)):
            continue
        pass_rows.append((-float(net_sharpe), alias_order[alias], alias))
    selected_alias = min(pass_rows)[2] if pass_rows else None
    formal_pass = selected_alias is not None
    return {
        "selected_alias": selected_alias,
        "formal_evaluation_pass": formal_pass,
        "human_review_required": human_review_required,
        "forward_admission_allowed": formal_pass,
        "testnet_eligible": False,
    }


def _panel_coverage(panel) -> dict[str, Any]:
    matrix = panel.matrices.get("defillama_chain_tvl") or []
    usable_rows = sum(
        sum(value is not None for value in row) >= MIN_CROSS_SECTION for row in matrix
    )
    return {
        "covered_instruments": len(panel.instruments),
        "usable_rows": usable_rows,
        "min_instruments_per_usable_row": MIN_CROSS_SECTION,
        "pit_admissible_for_promotion": True,
    }


def run(
    *,
    output: str | Path = DEFAULT_OUTPUT,
    recorded_at: int | None = None,
    source_commit: str,
    source_snapshot: str | Path = DEFAULT_SOURCE,
    panel=None,
    source_artifact_sha256: str | None = None,
) -> dict[str, Any]:
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source_commit must be a lowercase 40-character git commit")
    if panel is None:
        panel, coverage, source_digest = load_panel(source_snapshot)
    else:
        coverage = _panel_coverage(panel)
        source_digest = source_artifact_sha256 or stable_hash(
            {"injected_panel": field_panel_dataset_version(panel)}
        )
    if _SHA256.fullmatch(source_digest) is None:
        raise ValueError("source_artifact_sha256 must be a lowercase SHA-256 digest")
    if coverage["usable_rows"] <= 0:
        raise RuntimeError("DefiLlama TVL panel has no usable rows")
    if coverage.get("pit_admissible_for_promotion") is not True:
        raise RuntimeError(
            "DefiLlama history is retrospective/revisable and not PIT-admissible "
            "for formal trial registration"
        )

    candidates, leakage_evidence, mappings = build_candidate_batch(panel)
    observed_at = int(recorded_at if recorded_at is not None else time.time_ns() // 1_000_000)
    dataset_version = field_panel_dataset_version(
        panel,
        source_versions={
            **EVALUATION_CONTRACT,
            "defillama_chain_tvl_sha256": source_digest,
        },
    )
    search_id = make_search_id(
        source=SOURCE,
        search_kind=SEARCH_KIND,
        dataset_version=dataset_version,
        recorded_at=observed_at,
        params={
            "candidate_aliases": list(CANDIDATE_EXPRESSIONS),
            "source_commit": source_commit,
            "defillama_chain_tvl_sha256": source_digest,
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
                "Independent chain-level DeFi capital use may explain cross-sectional "
                "returns beyond exchange positioning and price-only signals."
            ),
            search_space=(
                "Exactly three preregistered members: fixed 30-day TVL growth, "
                "growth acceleration versus the prior 30 days, and TVL-minus-price "
                "growth; quantile 0.30, horizon 7d, no parameter or sign scan."
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
            "status": "VERIFIED" if evaluations.get(alias, {}).get("verified") is True else "PENDING",
        }
        for alias, mapping in mappings.items()
    }
    complete = all(row["status"] == "VERIFIED" for row in rows.values())
    document = {
        "schema_version": "defillama_tvl_evaluation.v1",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": observed_at,
        "source_commit": source_commit,
        "source_artifact": {
            "path": str(source_snapshot),
            "sha256": source_digest,
            "coverage": coverage,
            "source_boundary": SOURCE_BOUNDARY,
        },
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
            "deployment_entitlement_reviewed": False,
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
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args(argv)
    try:
        document = run(
            output=args.output,
            source_snapshot=args.source,
            source_commit=args.source_commit,
        )
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        print(f"DefiLlama TVL evaluation blocked: {type(exc).__name__}: {exc}")
        return 2
    selected = document["decision"]["selected_alias"]
    print(
        "DefiLlama TVL evaluation: "
        f"status={document['status']} selected={selected} "
        f"formal_pass={document['decision']['formal_evaluation_pass']}"
    )
    return 0 if document["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CANDIDATE_EXPRESSIONS",
    "EVALUATION_CONTRACT",
    "TVL_ACCELERATION_EXPRESSION",
    "TVL_GROWTH_EXPRESSION",
    "TVL_PRICE_DIVERGENCE_EXPRESSION",
    "attach_chain_tvl",
    "build_candidate_batch",
    "run",
]
