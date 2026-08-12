"""Evaluate the one frozen book-depth confirmation sample exactly once.

The driver verifies the complete immutable artifact graph, the append-only data
authority and the durable exploration population before it accesses any Alpha
outcome.  Its period evidence is emitted in the same write as the result so the
formal validation DAG can consume the exact sample without evaluating it again.

This is an L2 research operation.  A PASS is neither formal admission nor
permission to create Testnet intents or submit orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from collections.abc import Mapping, Sequence
from typing import Any

from quantos.contracts.hashing import stable_hash
from services.data.binance_book_depth_archive import DATASET, DAY_MS
from services.data.binance_systemic_store import BinanceSystemicStore
from services.research.book_depth_alpha import evaluate_confirmation
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from tools.binance_book_depth_alpha_exploration import (
    _committed_json,
    _trial_identities,
)
from tools.binance_book_depth_alpha_research import (
    _observation_digest,
    _require_clean_source,
    _safety,
)
from tools.binance_book_depth_backfill import _confirmation_capacity
from tools.cache_io import atomic_write_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE = Path("data") / "cache" / "binance_book_depth_archive.db"
DEFAULT_EXPERIMENT_DB = Path("data") / "cache" / "experiment_registry.db"
DEFAULT_RECOVERED_EXPERIMENT_DB = (
    ROOT / ".runtime" / "book-depth-confirmation" / "experiment_registry.db"
)
DEFAULT_OUTPUT = ROOT / "evidence" / "binance_book_depth_alpha_confirmation.json"

DEFAULT_CONFIRMATION = ROOT / "config" / "binance_book_depth_alpha_confirmation.json"
DEFAULT_QUALITY_POLICY = (
    ROOT / "config" / "binance_book_depth_confirmation_quality_policy_v4.json"
)
DEFAULT_SUPPLEMENT = (
    ROOT / "config" / "binance_book_depth_alpha_confirmation_supplement_v1.json"
)
DEFAULT_EXPLORATION_CONTRACT = (
    ROOT / "config" / "binance_book_depth_alpha_exploration.json"
)
DEFAULT_EXPLORATION_EVIDENCE = (
    ROOT / "evidence" / "binance_book_depth_alpha_exploration.json"
)
DEFAULT_CAPACITY_BLOCK = (
    ROOT / "evidence" / "binance_book_depth_confirmation_window1_v4_capacity_blocked.json"
)
DEFAULT_CAPACITY_PASS = (
    ROOT / "evidence" / "binance_book_depth_confirmation_capacity_supplement.json"
)
DEFAULT_REGISTRY_RECOVERY_EVIDENCE = (
    ROOT / "evidence" / "binance_book_depth_experiment_registry_recovery.json"
)

CONFIRMATION_COMMIT = "7b208c08e4161099b20ebbea8d6a6c76b9521a1b"
QUALITY_POLICY_COMMIT = "1c85c937e4c6b91c1c631263b01cbc01c7e4ab30"
SUPPLEMENT_COMMIT = "831222f363a8b54a25d3b9cb34a5ab0d5d548864"
EXPLORATION_CONTRACT_COMMIT = "074cf6022db87ba6d11d6ed25f092b9ccaa5315f"
EXPLORATION_EVIDENCE_COMMIT = "aa7937252a04077dd9dd91286c8acc238b272cdc"
CAPACITY_BLOCK_COMMIT = "b0a3722b64f872b368ca05c2763aac35b372d728"
CAPACITY_PASS_COMMIT = "143c417dd673c7bbbdb6f4f9748525a638753ecc"
REGISTRY_RECOVERY_COMMIT = "5e903e7c4ce1d358157014055a9d525da8e29a0b"


class BookDepthConfirmationError(RuntimeError):
    """The one-shot confirmation cannot prove its immutable provenance."""


def _artifact(
    path: Path, *, commit: str, repository_path: str
) -> tuple[str, dict[str, Any]]:
    try:
        digest, payload = _committed_json(
            path, commit=commit, repository_path=repository_path
        )
    except Exception as exc:
        raise BookDepthConfirmationError(
            f"immutable artifact cannot be verified: {repository_path}"
        ) from exc
    return digest, payload


def _verify_immutable_artifacts() -> dict[str, dict[str, Any]]:
    specs = {
        "confirmation": (
            DEFAULT_CONFIRMATION,
            CONFIRMATION_COMMIT,
            "config/binance_book_depth_alpha_confirmation.json",
        ),
        "quality_policy": (
            DEFAULT_QUALITY_POLICY,
            QUALITY_POLICY_COMMIT,
            "config/binance_book_depth_confirmation_quality_policy_v4.json",
        ),
        "supplement": (
            DEFAULT_SUPPLEMENT,
            SUPPLEMENT_COMMIT,
            "config/binance_book_depth_alpha_confirmation_supplement_v1.json",
        ),
        "exploration_contract": (
            DEFAULT_EXPLORATION_CONTRACT,
            EXPLORATION_CONTRACT_COMMIT,
            "config/binance_book_depth_alpha_exploration.json",
        ),
        "exploration_evidence": (
            DEFAULT_EXPLORATION_EVIDENCE,
            EXPLORATION_EVIDENCE_COMMIT,
            "evidence/binance_book_depth_alpha_exploration.json",
        ),
        "capacity_block": (
            DEFAULT_CAPACITY_BLOCK,
            CAPACITY_BLOCK_COMMIT,
            "evidence/binance_book_depth_confirmation_window1_v4_capacity_blocked.json",
        ),
        "capacity_pass": (
            DEFAULT_CAPACITY_PASS,
            CAPACITY_PASS_COMMIT,
            "evidence/binance_book_depth_confirmation_capacity_supplement.json",
        ),
        "registry_recovery": (
            DEFAULT_REGISTRY_RECOVERY_EVIDENCE,
            REGISTRY_RECOVERY_COMMIT,
            "evidence/binance_book_depth_experiment_registry_recovery.json",
        ),
    }
    artifacts: dict[str, dict[str, Any]] = {}
    for name, (path, commit, repository_path) in specs.items():
        digest, payload = _artifact(
            path, commit=commit, repository_path=repository_path
        )
        artifacts[name] = {
            "commit": commit,
            "sha256": digest,
            "repository_path": repository_path,
            "payload": payload,
        }
    _validate_artifact_graph(artifacts)
    return artifacts


def _validate_artifact_graph(artifacts: Mapping[str, Mapping[str, Any]]) -> None:
    confirmation = artifacts["confirmation"]["payload"]
    policy = artifacts["quality_policy"]["payload"]
    supplement = artifacts["supplement"]["payload"]
    exploration_contract = artifacts["exploration_contract"]["payload"]
    exploration = artifacts["exploration_evidence"]["payload"]
    capacity_block = artifacts["capacity_block"]["payload"]
    capacity_pass = artifacts["capacity_pass"]["payload"]
    registry_recovery = artifacts["registry_recovery"]["payload"]

    if (
        confirmation.get("status")
        != "PRE_REGISTERED_BEFORE_CONFIRMATION_DATA_ACCESS"
        or confirmation.get("source_exploration_evidence")
        != artifacts["exploration_evidence"]["repository_path"]
        or confirmation.get("source_exploration_commit")
        != artifacts["exploration_evidence"]["commit"]
        or confirmation.get("dataset_id") != DATASET
    ):
        raise BookDepthConfirmationError("confirmation source binding is invalid")
    trial_id = (
        f"{confirmation['candidate_id']}|direction={int(confirmation['direction']):+d}"
        f"|horizon={int(confirmation['horizon_hours'])}h"
    )
    if (
        exploration.get("status") != "LEAD_SELECTED"
        or not exploration.get("selected_trial_ids")
        or exploration["selected_trial_ids"][0] != trial_id
        or exploration.get("exploration_commit")
        != artifacts["exploration_contract"]["commit"]
        or exploration.get("exploration_sha256")
        != artifacts["exploration_contract"]["sha256"]
        or exploration.get("formal_admission") is not False
        or exploration.get("testnet_authorized") is not False
    ):
        raise BookDepthConfirmationError("frozen exploration lead binding is invalid")
    identity_fields = (
        "dataset_id",
        "candidate_id",
        "direction",
        "horizon_hours",
        "universe",
        "long_count",
        "short_count",
        "one_way_cost_bps_per_unit_turnover",
        "cost_stress_multiplier",
        "annualization_periods",
        "required_gates",
    )
    if (
        supplement.get("status")
        != "PRE_REGISTERED_BEFORE_SUPPLEMENT_DATA_ACCESS"
        or supplement.get("parent_confirmation_study_id")
        != confirmation.get("study_id")
        or supplement.get("parent_confirmation_contract_commit")
        != artifacts["confirmation"]["commit"]
        or supplement.get("quality_policy_commit")
        != artifacts["quality_policy"]["commit"]
        or supplement.get("source_capacity_block_commit")
        != artifacts["capacity_block"]["commit"]
        or any(supplement.get(field) != confirmation.get(field) for field in identity_fields)
    ):
        raise BookDepthConfirmationError("capacity supplement identity drifted")
    parent_window = confirmation.get("confirmation_window")
    supplement_window = supplement.get("supplement_window")
    if not isinstance(parent_window, Mapping) or not isinstance(
        supplement_window, Mapping
    ):
        raise BookDepthConfirmationError("confirmation windows are missing")
    if (
        int(supplement_window["end_exclusive_ms"])
        != int(parent_window["start_day_ms"])
        or supplement_window.get("relation_to_parent_confirmation")
        != "IMMEDIATELY_PRECEDING_NON_OVERLAPPING"
    ):
        raise BookDepthConfirmationError("confirmation windows are not contiguous")
    authorization = policy.get("authorization")
    coverage = policy.get("coverage_decision")
    hard = policy.get("unchanged_hard_gates")
    if (
        policy.get("schema_version") != 4
        or policy.get("status") != "FROZEN_BEFORE_CONFIRMATION_OUTCOME"
        or policy.get("confirmation_study_id") != confirmation.get("study_id")
        or not isinstance(authorization, Mapping)
        or any(authorization.get(key) is not False for key in (
            "formal_admission", "forward_credit", "testnet", "order_submission"
        ))
        or not isinstance(coverage, Mapping)
        or coverage.get("daily_total_snapshot_floor_is_admission_gate") is not False
        or coverage.get("all_24_utc_hours_required") is not True
        or coverage.get("minimum_hourly_snapshot_count") != 96
        or not isinstance(hard, Mapping)
        or hard.get("official_archive_checksum_required") is not True
        or hard.get("all_hours_must_align_to_checksum_verified_mark_prices") is not True
        or hard.get("network_or_timeout_is_excludable") is not False
        or hard.get("checksum_mismatch_is_excludable") is not False
        or hard.get("unexpected_exception_is_excludable") is not False
    ):
        raise BookDepthConfirmationError("frozen quality policy is invalid")
    if (
        capacity_block.get("status") != "BLOCKED_BEFORE_OUTCOME"
        or capacity_block.get("confirmation_return_accessed") is not False
        or capacity_block.get("actual_adjacent_24h_decision_count")
        != supplement.get("parent_capacity_decisions")
        or capacity_pass.get("status") != "PASS"
        or capacity_pass.get("combined_data_capacity_status") != "PASS"
        or capacity_pass.get("confirmation_return_accessed") is not False
        or capacity_pass.get("confirmation_metrics_computed") is not False
        or not isinstance(
            capacity_pass.get("combined_actual_adjacent_24h_decision_count"), int
        )
        or capacity_pass.get("combined_actual_adjacent_24h_decision_count")
        < supplement.get("minimum_combined_decisions")
        or capacity_pass.get("minimum_combined_decisions")
        != supplement.get("minimum_combined_decisions")
        or capacity_pass.get("authority", {}).get("chain_valid") is not True
        or capacity_pass.get("formal_admission") is not False
        or capacity_pass.get("testnet_authorized") is not False
        or confirmation.get("multiple_testing", {}).get("confirmation_adds_new_trial")
        is not False
        or supplement.get("multiple_testing", {}).get("supplement_adds_new_trial")
        is not False
    ):
        raise BookDepthConfirmationError("capacity evidence is not outcome-blind PASS")
    if int(exploration_contract.get("trial_count", 0)) != int(
        exploration.get("trial_count", -1)
    ):
        raise BookDepthConfirmationError("exploration population size drifted")
    exploration_registry = exploration.get("experiment_registry")
    if (
        not isinstance(exploration_registry, Mapping)
        or registry_recovery.get("status") != "PASS"
        or registry_recovery.get("mode")
        != "VERIFIED_L2_BASELINE_PLUS_IMMUTABLE_SEARCH_REGISTRATION_REPLAY"
        or registry_recovery.get("exploration_contract_commit")
        != artifacts["exploration_contract"]["commit"]
        or registry_recovery.get("exploration_contract_sha256")
        != artifacts["exploration_contract"]["sha256"]
        or registry_recovery.get("exploration_evidence_commit")
        != artifacts["exploration_evidence"]["commit"]
        or registry_recovery.get("exploration_evidence_sha256")
        != artifacts["exploration_evidence"]["sha256"]
        or registry_recovery.get("search_id")
        != exploration_registry.get("search_id")
        or registry_recovery.get("search_identity")
        != exploration_registry.get("search_identity")
        or registry_recovery.get("candidate_count")
        != exploration_registry.get("candidate_count")
        or registry_recovery.get("global_trial_count_before")
        != exploration_registry.get("global_trial_count_before")
        or registry_recovery.get("global_trial_count_after")
        != exploration_registry.get("global_trial_count_after")
        or registry_recovery.get("global_trial_count_delta")
        != exploration_registry.get("global_trial_count_delta")
        or registry_recovery.get("search_history_valid") is not True
        or registry_recovery.get("trial_provenance_chain_valid") is not True
        or registry_recovery.get("oos_access_chain_valid") is not True
        or registry_recovery.get("sqlite_integrity") != "ok"
        or registry_recovery.get("alpha_outcome_accessed") is not False
        or registry_recovery.get("market_observations_accessed") is not False
        or registry_recovery.get("formal_admission") is not False
        or registry_recovery.get("testnet_authorized") is not False
    ):
        raise BookDepthConfirmationError("recovered experiment registry binding is invalid")


def _verify_registry(
    *,
    experiment_db_path: str | Path,
    artifacts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    exploration_contract = artifacts["exploration_contract"]["payload"]
    exploration = artifacts["exploration_evidence"]["payload"]
    confirmation = artifacts["confirmation"]["payload"]
    identities = _trial_identities(
        exploration_contract, str(artifacts["exploration_contract"]["sha256"])
    )
    config_hashes = tuple(stable_hash(identity) for identity in identities)
    selected = next(
        (
            identity
            for identity in identities
            if identity["candidate_id"] == confirmation["candidate_id"]
            and identity["direction"] == confirmation["direction"]
            and identity["horizon_hours"] == confirmation["horizon_hours"]
        ),
        None,
    )
    if selected is None:
        raise BookDepthConfirmationError("frozen confirmation trial is unregistered")
    selected_config_hash = stable_hash(selected)
    expected_registry = exploration.get("experiment_registry")
    if not isinstance(expected_registry, Mapping):
        raise BookDepthConfirmationError("exploration registry evidence is missing")
    search_id = str(expected_registry["search_id"])
    store = SqliteExperimentStore(str(experiment_db_path), create=False)
    registry = ExperimentRegistry(store)
    try:
        if not registry.search_history_valid():
            raise BookDepthConfirmationError("experiment search authority is invalid")
        identity = registry.search_identity(search_id)
        events = registry.search_attempt_events(search_id)
        registered = tuple(
            row["config_hash"] for row in registry.search_candidates(search_id)
        )
        global_trial_count = store.trial_count()
        global_hashes = set(store.all_trial_hashes())
    finally:
        store.close()
    if (
        identity != expected_registry.get("search_identity")
        or not events
        or events[-1].get("status") != "COMPLETED"
        or registered != config_hashes
        or selected_config_hash not in global_hashes
        or global_trial_count
        < int(confirmation["multiple_testing"]["global_trial_count_after_exploration"])
    ):
        raise BookDepthConfirmationError("durable exploration population drifted")
    return {
        "search_id": search_id,
        "search_identity": identity,
        "registered_candidate_count": len(registered),
        "selected_config_hash": selected_config_hash,
        "global_trial_count": global_trial_count,
        "confirmation_trial_increment": 0,
        "search_history_valid": True,
        "terminal_status": "COMPLETED",
    }


def _load_bound_input(
    *, store_path: str | Path, artifacts: Mapping[str, Mapping[str, Any]]
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    confirmation = artifacts["confirmation"]["payload"]
    supplement = artifacts["supplement"]["payload"]
    capacity_block = artifacts["capacity_block"]["payload"]
    capacity_pass = artifacts["capacity_pass"]["payload"]
    parent_window = confirmation["confirmation_window"]
    supplement_window = supplement["supplement_window"]
    start_ms = int(supplement_window["start_day_ms"])
    supplement_end_ms = int(supplement_window["end_exclusive_ms"])
    end_ms = int(parent_window["end_exclusive_ms"])
    universe = tuple(str(value) for value in confirmation["universe"])
    store = BinanceSystemicStore(str(store_path))
    try:
        observations = store.range_observations(
            DATASET,
            start_event_time_ms=start_ms + 1,
            end_event_time_ms=end_ms + 1,
            instruments=universe,
        )
        chain_valid = store.chain_valid()
        authority_head = store.authority_head()
        dataset_row_count = store.row_count(DATASET)
    finally:
        store.close()
    expected_head = {
        "seq": int(capacity_pass["authority"]["head_seq"]),
        "row_hash": str(capacity_pass["authority"]["head_row_hash"]),
    }
    if (
        not chain_valid
        or authority_head != expected_head
        or dataset_row_count != int(capacity_pass["authority"]["dataset_row_count"])
    ):
        raise BookDepthConfirmationError("book-depth authority drifted after capacity PASS")
    expected_observation_count = (
        int(capacity_pass["complete_days"])
        + int(capacity_block["complete_stored_days"])
    ) * len(universe) * 24
    if len(observations) != expected_observation_count:
        raise BookDepthConfirmationError("combined confirmation observations drifted")
    parent_frozen_at = int(
        confirmation["frozen_unseen_window_evidence"]["observed_at_ms"]
    )
    supplement_frozen_at = int(
        supplement["frozen_unseen_window_evidence"]["observed_at_ms"]
    )
    for row in observations:
        event_time_ms = int(row["event_time_ms"])
        minimum_knowledge_time = (
            supplement_frozen_at
            if event_time_ms <= supplement_end_ms
            else parent_frozen_at
        )
        if int(row["knowledge_time_ms"]) < minimum_knowledge_time:
            raise BookDepthConfirmationError("confirmation input predates its freeze")
    capacity = _confirmation_capacity(
        store_path=str(store_path),
        start_day_ms=start_ms,
        end_exclusive_ms=end_ms,
        universe=universe,
        horizon_hours=int(confirmation["horizon_hours"]),
    )
    actual_decisions = int(capacity["actual_adjacent_24h_decision_count"])
    if (
        actual_decisions
        != int(capacity_pass["combined_actual_adjacent_24h_decision_count"])
        or actual_decisions < int(supplement["minimum_combined_decisions"])
    ):
        raise BookDepthConfirmationError("combined confirmation capacity cannot be replayed")
    return observations, {
        "input_start_exclusive_ms": start_ms,
        "input_end_inclusive_ms": end_ms,
        "input_observation_count": len(observations),
        "input_observation_sha256": _observation_digest(list(observations)),
        "complete_day_count": int(capacity["complete_day_count"]),
        "actual_adjacent_24h_decision_count": actual_decisions,
        "authority_head": authority_head,
        "authority_chain_valid": True,
    }


def _bindings(artifacts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        name: {
            "commit": artifact["commit"],
            "sha256": artifact["sha256"],
            "repository_path": artifact["repository_path"],
        }
        for name, artifact in artifacts.items()
    }


def run_confirmation(
    *,
    store_path: str | Path = DEFAULT_STORE,
    experiment_db_path: str | Path = DEFAULT_EXPERIMENT_DB,
    output_path: str | Path = DEFAULT_OUTPUT,
    generated_at_ms: int | None = None,
) -> dict[str, Any]:
    """Run the immutable combined confirmation sample once, fail closed."""
    output = Path(output_path)
    if output.exists():
        raise BookDepthConfirmationError(
            "official confirmation result already exists; outcome cannot be re-evaluated"
        )
    now_ms = int(time.time() * 1000) if generated_at_ms is None else generated_at_ms
    source_commit = _require_clean_source()
    artifacts = _verify_immutable_artifacts()
    registry = _verify_registry(
        experiment_db_path=experiment_db_path, artifacts=artifacts
    )
    observations, input_binding = _load_bound_input(
        store_path=store_path, artifacts=artifacts
    )
    contract = dict(artifacts["confirmation"]["payload"])
    contract["minimum_decisions"] = int(
        artifacts["supplement"]["payload"]["minimum_combined_decisions"]
    )
    diagnostic = evaluate_confirmation(
        observations, contract, include_period_evidence=True
    )
    periods = diagnostic.get("period_evidence")
    if (
        diagnostic.get("status") not in {"PASS", "FAIL"}
        or not isinstance(periods, list)
        or diagnostic.get("decision_count")
        != input_binding["actual_adjacent_24h_decision_count"]
        or diagnostic.get("formal_admission") is not False
        or diagnostic.get("testnet_authorized") is not False
    ):
        raise BookDepthConfirmationError("confirmation evaluator result is invalid")
    period_bytes = json.dumps(
        periods,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    result = {
        "schema_version": 1,
        "generated_at_ms": now_ms,
        **diagnostic,
        "study_id": contract["study_id"],
        "combined_with_capacity_supplement": artifacts["supplement"]["payload"][
            "study_id"
        ],
        "dataset": DATASET,
        "source_commit": source_commit,
        "immutable_bindings": _bindings(artifacts),
        **input_binding,
        "period_evidence_sha256": hashlib.sha256(period_bytes).hexdigest(),
        "experiment_registry": registry,
        "confirmation_evaluated_once": True,
        "formal_validation_started": False,
        "safety": _safety(),
    }
    atomic_write_json(str(output), result, indent=2)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the one frozen Binance book-depth Alpha confirmation"
    )
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--experiment-db", default=str(DEFAULT_EXPERIMENT_DB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    result = run_confirmation(
        store_path=args.store,
        experiment_db_path=args.experiment_db,
        output_path=args.output,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BookDepthConfirmationError",
    "DEFAULT_EXPERIMENT_DB",
    "DEFAULT_OUTPUT",
    "DEFAULT_STORE",
    "main",
    "run_confirmation",
]
