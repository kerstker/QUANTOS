"""Formal pre-registered Binance x Bybit positioning-family evaluation.

The complete family is fixed before any result is observed.  It contains two
single-venue controls and two economically distinct cross-venue mechanisms:

* fade Bybit-specific broad-account crowding relative to Binance;
* fade Bybit crowding more strongly when Bybit open interest is building.

There is no sign, window, horizon, quantile, or parameter scan.  All four
members are registered in one immutable search population and evaluated by the
existing catalog-wide DSR/PBO, leakage, regime, and sequential walk-forward
gates.  Historical evidence alone never grants Testnet eligibility.

This module is L2 research only.  It reads a public-data artifact, never reads
credentials, never connects to a broker, and never creates or submits an order.
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
from services.execution.backtest_authority import quarantine_legacy_backtest
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


DEFAULT_SOURCE = Path("data/cache/bybit_positioning_metrics.json")
DEFAULT_OUTPUT = Path("data/cache/cross_venue_positioning_evaluation.json")
FWD_HORIZON = 7
PERIODS_PER_YEAR = 52
EVALUATION_QUANTILE = 0.3
DATA_SOURCE = "crypto_oi_pit_panel+bybit_positioning_metrics.json"
SOURCE = "tools.cross_venue_positioning_evaluation"
SEARCH_KIND = "PREREGISTERED_CROSS_VENUE_POSITIONING_V2"
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


BINANCE_RETAIL_CONTRA_EXPRESSION = (
    "cs_zscore",
    ("neg", ("field", "global_ls")),
)
BYBIT_RETAIL_CONTRA_EXPRESSION = (
    "cs_zscore",
    ("neg", ("field", "bybit_global_ls")),
)
VENUE_DISAGREEMENT_EXPRESSION = (
    "cs_zscore",
    ("sub", ("field", "global_ls"), ("field", "bybit_global_ls")),
)
BYBIT_OI_CONFIRMED_CROWD_EXPRESSION = (
    "mul",
    BYBIT_RETAIL_CONTRA_EXPRESSION,
    (
        "cs_rank",
        ("ts_delta", ("log", ("field", "bybit_open_interest")), 7),
    ),
)

CANDIDATE_EXPRESSIONS: dict[str, tuple] = {
    "binance_retail_contra_control": BINANCE_RETAIL_CONTRA_EXPRESSION,
    "bybit_retail_contra_control": BYBIT_RETAIL_CONTRA_EXPRESSION,
    "venue_positioning_disagreement": VENUE_DISAGREEMENT_EXPRESSION,
    "bybit_oi_confirmed_crowd": BYBIT_OI_CONFIRMED_CROWD_EXPRESSION,
}

ECONOMIC_HYPOTHESES = {
    "binance_retail_contra_control": (
        "Control: crowded broad-account positioning on Binance should mean-revert "
        "cross-sectionally."
    ),
    "bybit_retail_contra_control": (
        "Control: crowded broad-account positioning on Bybit should mean-revert "
        "cross-sectionally."
    ),
    "venue_positioning_disagreement": (
        "A coin whose Bybit broad-account long/short ratio is high relative to "
        "Binance contains venue-specific crowding; fade that relative excess."
    ),
    "bybit_oi_confirmed_crowd": (
        "Bybit broad-account crowding is more vulnerable when seven-day open-interest "
        "growth shows fresh leverage entering the same contract."
    ),
}

FALSIFICATION_CRITERIA = (
    "No family member passes the immutable catalog-wide DSR threshold.",
    "The complete four-member CSCV selection population exceeds the PBO ceiling.",
    "A candidate fails leakage, regime, or sequential walk-forward evidence.",
    "The PASS-only selector has no verified candidate to admit to forward paper.",
)

EVALUATION_CONTRACT = {
    "driver": "cross_venue_positioning_evaluation.v2",
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
        "first family-common row with >=2 tradable instruments, then the next "
        "family-common row at least seven panel rows later"
    ),
    "bybit_event_contract": (
        "completed UTC-day public historical records only; unfinished day excluded"
    ),
}


def attach_bybit_positioning(
    panel,
    snapshot: Mapping[str, Any],
    *,
    min_instruments: int = 10,
    min_common_rows: int = 365,
) -> dict[str, Any]:
    """Attach exact-day Bybit OI/ratio cells and fail closed on thin coverage."""
    if snapshot.get("schema_version") != 1:
        raise RuntimeError("unsupported Bybit positioning schema")
    if snapshot.get("provenance") != "REAL_BYBIT_PUBLIC_POSITIONING_HISTORY":
        raise RuntimeError("Bybit positioning provenance is not authoritative")
    source_series = snapshot.get("series")
    if not isinstance(source_series, Mapping):
        raise RuntimeError("Bybit positioning series is missing")

    bybit_ratio: list[list[float | None]] = []
    bybit_oi: list[list[float | None]] = []
    covered_by_instrument: dict[str, int] = {instrument: 0 for instrument in panel.instruments}
    usable_rows = 0
    base_global = panel.matrices.get("global_ls")
    if not isinstance(base_global, list):
        raise RuntimeError("base panel has no global_ls matrix")

    normalized: dict[str, tuple[dict[int, float], dict[int, float]]] = {}
    for instrument in panel.instruments:
        raw = source_series.get(instrument)
        if not isinstance(raw, Mapping):
            normalized[instrument] = ({}, {})
            continue
        try:
            ratios = {
                int(timestamp): float(value)
                for timestamp, value in (raw.get("long_short_ratio") or {}).items()
                if math.isfinite(float(value)) and float(value) > 0
            }
            oi = {
                int(timestamp): float(value)
                for timestamp, value in (raw.get("open_interest") or {}).items()
                if math.isfinite(float(value)) and float(value) > 0
            }
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid Bybit positioning series for {instrument}") from exc
        normalized[instrument] = (ratios, oi)

    for row_index, event_time in enumerate(panel.event_times):
        ratio_row: list[float | None] = []
        oi_row: list[float | None] = []
        row_coverage = 0
        for column, instrument in enumerate(panel.instruments):
            ratios, oi = normalized[instrument]
            ratio = ratios.get(int(event_time))
            oi_value = oi.get(int(event_time))
            ratio_row.append(ratio)
            oi_row.append(oi_value)
            base_value = base_global[row_index][column]
            if ratio is not None and oi_value is not None and base_value is not None:
                covered_by_instrument[instrument] += 1
                row_coverage += 1
        bybit_ratio.append(ratio_row)
        bybit_oi.append(oi_row)
        if row_coverage >= min_instruments:
            usable_rows += 1

    covered_instruments = sum(
        1 for count in covered_by_instrument.values() if count >= min_common_rows
    )
    if covered_instruments < min_instruments:
        raise RuntimeError(
            f"Bybit positioning covered instruments {covered_instruments} < {min_instruments}"
        )
    if usable_rows < min_common_rows:
        raise RuntimeError(
            f"Bybit positioning usable rows {usable_rows} < {min_common_rows}"
        )

    panel.matrices["bybit_global_ls"] = bybit_ratio
    panel.matrices["bybit_open_interest"] = bybit_oi
    return {
        "covered_instruments": covered_instruments,
        "usable_rows": usable_rows,
        "min_instruments_per_usable_row": min_instruments,
        "minimum_common_rows": min_common_rows,
        "common_rows_by_instrument": dict(sorted(covered_by_instrument.items())),
    }


def load_panel(
    source_snapshot: str | Path = DEFAULT_SOURCE,
) -> tuple[Any, dict[str, Any], str]:
    path = Path(source_snapshot)
    payload = path.read_bytes()
    try:
        snapshot = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Bybit positioning artifact is invalid JSON") from exc
    from tools.crypto_oi_research import build_panel

    panel, _instruments = build_panel()
    coverage = attach_bybit_positioning(panel, snapshot)
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
        universe="crypto_oi_majors_binance_bybit_pit",
        params=params,
    )
    research_id = f"cross_venue_positioning_{alias}_{seed.definition_hash[:16]}"
    definition = AlphaDefinition(
        alpha_id=research_id,
        name=alias,
        factor_ids=seed.factor_ids,
        universe=seed.universe,
        params=params,
    )
    if definition.definition_hash != seed.definition_hash:
        raise RuntimeError("content-addressed cross-venue identity drifted")
    return definition, seed.definition_hash


def _shared_decision_rows(signals: Sequence[list], panel) -> tuple[int, ...]:
    """Choose one no-overlap row calendar shared by the complete family."""
    price = panel.matrices["close"]
    eligible = []
    for row_index in range(len(panel.event_times) - FWD_HORIZON):
        forward_index = row_index + FWD_HORIZON
        family_ready = True
        for signal in signals:
            survivors = 0
            for column, value in enumerate(signal[row_index]):
                p0 = price[row_index][column]
                p1 = price[forward_index][column]
                if (
                    value is not None
                    and math.isfinite(value)
                    and p0 is not None
                    and p1 is not None
                    and p0 > 0
                ):
                    survivors += 1
            if survivors < 2:
                family_ready = False
                break
        if family_ready:
            eligible.append(row_index)

    selected = []
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
    decision_rows = _shared_decision_rows(
        [item[4] for item in prepared],
        panel,
    )
    decision_row_set = set(decision_rows)
    expected_calendar = tuple(int(panel.event_times[row]) for row in decision_rows)
    for alias, expression, definition, definition_hash, signal in prepared:
        masked_signal = [
            list(row) if row_index in decision_row_set else [None] * len(row)
            for row_index, row in enumerate(signal)
        ]
        periods = ax.panel_to_periods(
            masked_signal,
            panel,
            fwd_horizon=FWD_HORIZON,
            non_overlapping=False,
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
        raise RuntimeError("evaluation is not bound to the registered cross-venue family")
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
    ratios = panel.matrices.get("bybit_global_ls") or []
    oi = panel.matrices.get("bybit_open_interest") or []
    usable_rows = 0
    for ratio_row, oi_row in zip(ratios, oi):
        if sum(
            ratio is not None and oi_value is not None
            for ratio, oi_value in zip(ratio_row, oi_row)
        ) >= 10:
            usable_rows += 1
    return {
        "covered_instruments": len(panel.instruments),
        "usable_rows": usable_rows,
        "min_instruments_per_usable_row": 10,
    }


def _run_legacy_diagnostic(
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
        raise RuntimeError("cross-venue panel has no usable rows")

    candidates, leakage_evidence, mappings = build_candidate_batch(panel)
    observed_at = int(recorded_at if recorded_at is not None else time.time_ns() // 1_000_000)
    dataset_version = field_panel_dataset_version(
        panel,
        source_versions={
            **EVALUATION_CONTRACT,
            "bybit_positioning_sha256": source_digest,
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
            "bybit_positioning_sha256": source_digest,
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
                "Cross-venue broad-account positioning and Bybit OI growth may "
                "isolate venue-specific leverage crowding beyond Binance-only signals."
            ),
            search_space=(
                "Exactly four preregistered members: Binance and Bybit crowd-fade "
                "controls, one fixed Bybit-minus-Binance disagreement direction, and "
                "one fixed seven-day Bybit OI-confirmed crowd fade; quantile 0.30, "
                "horizon 7d, no parameter or sign scan."
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
                "VERIFIED" if evaluations.get(alias, {}).get("verified") is True else "PENDING"
            ),
        }
        for alias, mapping in mappings.items()
    }
    complete = all(row["status"] == "VERIFIED" for row in rows.values())
    document = {
        "schema_version": "cross_venue_positioning_evaluation.v2",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": observed_at,
        "source_commit": source_commit,
        "source_artifact": {
            "path": str(source_snapshot),
            "sha256": source_digest,
            "coverage": coverage,
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
    quarantine_legacy_backtest("tools.cross_venue_positioning_evaluation.run")


def main(argv: Sequence[str] | None = None) -> int:
    quarantine_legacy_backtest("tools.cross_venue_positioning_evaluation.main")
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
        print(f"cross-venue positioning evaluation blocked: {type(exc).__name__}: {exc}")
        return 2
    selected = document["decision"]["selected_alias"]
    print(
        "cross-venue positioning evaluation: "
        f"status={document['status']} selected={selected} "
        f"formal_pass={document['decision']['formal_evaluation_pass']}"
    )
    return 0 if document["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BINANCE_RETAIL_CONTRA_EXPRESSION",
    "BYBIT_OI_CONFIRMED_CROWD_EXPRESSION",
    "BYBIT_RETAIL_CONTRA_EXPRESSION",
    "CANDIDATE_EXPRESSIONS",
    "DATA_SOURCE",
    "DEFAULT_OUTPUT",
    "DEFAULT_SOURCE",
    "EVALUATION_CONTRACT",
    "VENUE_DISAGREEMENT_EXPRESSION",
    "attach_bybit_positioning",
    "build_candidate_batch",
    "load_panel",
    "run",
]
