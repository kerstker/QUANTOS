"""Reconstruct the lost confirmation source as a new governed authority.

The old SQLite chain is not recreated or impersonated.  This worker downloads
the exact frozen 97-day public Binance source window into a new append-only
store, applies the unchanged v4 source-quality policy, and requires every
pre-outcome capacity fact to reproduce exactly before publishing recovery
evidence.  It never calls an Alpha evaluator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from services.data.binance_book_depth_archive import DATASET, DAY_MS
from services.data.binance_systemic_store import BinanceSystemicStore
from tools.binance_book_depth_alpha_exploration import _committed_json
from tools.binance_book_depth_alpha_research import (
    _observation_digest,
    _require_clean_source,
    _safety,
)
from tools.binance_book_depth_backfill import (
    _confirmation_capacity,
    run_backfill,
)
from tools.binance_book_depth_fetch import refresh_book_depth_days
from tools.cache_io import atomic_write_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = (
    ROOT / "config" / "binance_book_depth_confirmation_authority_recovery_v1.json"
)
DEFAULT_STORE = (
    ROOT / ".runtime" / "book-depth-confirmation" / "binance_book_depth_archive.db"
)
DEFAULT_ARCHIVE = ROOT / ".runtime" / "book-depth-confirmation" / "raw_market_data"
DEFAULT_STATUS = (
    ROOT / ".runtime" / "book-depth-confirmation" / "authority_recovery_status.json"
)
DEFAULT_OUTPUT = (
    ROOT / "evidence" / "binance_book_depth_confirmation_authority_recovery.json"
)
DEFAULT_CONFIRMATION = ROOT / "config" / "binance_book_depth_alpha_confirmation.json"
DEFAULT_POLICY = ROOT / "config" / "binance_book_depth_confirmation_quality_policy_v4.json"
DEFAULT_SUPPLEMENT = ROOT / "config" / "binance_book_depth_alpha_confirmation_supplement_v1.json"
DEFAULT_CAPACITY_BLOCK = ROOT / "evidence" / "binance_book_depth_confirmation_window1_v4_capacity_blocked.json"
DEFAULT_CAPACITY_PASS = ROOT / "evidence" / "binance_book_depth_confirmation_capacity_supplement.json"

CONTRACT_COMMIT = "969a9aa068454b8a180817adea7c1fae328183de"
CONFIRMATION_COMMIT = "7b208c08e4161099b20ebbea8d6a6c76b9521a1b"
POLICY_COMMIT = "1c85c937e4c6b91c1c631263b01cbc01c7e4ab30"
SUPPLEMENT_COMMIT = "831222f363a8b54a25d3b9cb34a5ab0d5d548864"
CAPACITY_BLOCK_COMMIT = "b0a3722b64f872b368ca05c2763aac35b372d728"
CAPACITY_PASS_COMMIT = "143c417dd673c7bbbdb6f4f9748525a638753ecc"


class BookDepthAuthorityRecoveryError(RuntimeError):
    """The new confirmation authority cannot reproduce its frozen source facts."""


def _artifact(path: Path, *, commit: str, repository_path: str) -> tuple[str, dict]:
    try:
        return _committed_json(path, commit=commit, repository_path=repository_path)
    except Exception as exc:
        raise BookDepthAuthorityRecoveryError(
            f"immutable recovery artifact cannot be verified: {repository_path}"
        ) from exc


def _verify_artifacts() -> dict[str, dict[str, Any]]:
    specs = {
        "recovery_contract": (
            DEFAULT_CONTRACT,
            CONTRACT_COMMIT,
            "config/binance_book_depth_confirmation_authority_recovery_v1.json",
        ),
        "confirmation": (
            DEFAULT_CONFIRMATION,
            CONFIRMATION_COMMIT,
            "config/binance_book_depth_alpha_confirmation.json",
        ),
        "quality_policy": (
            DEFAULT_POLICY,
            POLICY_COMMIT,
            "config/binance_book_depth_confirmation_quality_policy_v4.json",
        ),
        "supplement": (
            DEFAULT_SUPPLEMENT,
            SUPPLEMENT_COMMIT,
            "config/binance_book_depth_alpha_confirmation_supplement_v1.json",
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
    }
    artifacts: dict[str, dict[str, Any]] = {}
    for name, (path, commit, repository_path) in specs.items():
        digest, payload = _artifact(path, commit=commit, repository_path=repository_path)
        artifacts[name] = {
            "commit": commit,
            "sha256": digest,
            "repository_path": repository_path,
            "payload": payload,
        }
    _validate_artifacts(artifacts)
    return artifacts


def _validate_artifacts(artifacts: Mapping[str, Mapping[str, Any]]) -> None:
    contract = artifacts["recovery_contract"]["payload"]
    confirmation = artifacts["confirmation"]["payload"]
    policy = artifacts["quality_policy"]["payload"]
    supplement = artifacts["supplement"]["payload"]
    blocked = artifacts["capacity_block"]["payload"]
    capacity = artifacts["capacity_pass"]["payload"]
    source_contracts = contract.get("source_contracts")
    reconstruction = contract.get("reconstruction")
    acceptance = contract.get("exact_acceptance")
    authorization = contract.get("authorization")
    old = contract.get("old_authority")
    if (
        contract.get("status") != "FROZEN_BEFORE_RECONSTRUCTION_OR_OUTCOME_ACCESS"
        or not all(isinstance(value, Mapping) for value in (
            source_contracts, reconstruction, acceptance, authorization, old
        ))
        or source_contracts.get("confirmation_contract_commit") != CONFIRMATION_COMMIT
        or source_contracts.get("quality_policy_v4_commit") != POLICY_COMMIT
        or source_contracts.get("supplement_contract_commit") != SUPPLEMENT_COMMIT
        or source_contracts.get("parent_capacity_block_commit") != CAPACITY_BLOCK_COMMIT
        or source_contracts.get("capacity_pass_commit") != CAPACITY_PASS_COMMIT
        or reconstruction.get("dataset_id") != DATASET
        or reconstruction.get("source_transport") != "PUBLIC_HTTPS_NO_CREDENTIAL"
        or reconstruction.get("official_checksum_required") is not True
        or reconstruction.get("mark_price_checksum_required") is not True
        or reconstruction.get("quality_policy") != "ESTIMATOR_ALIGNED_V4_UNCHANGED"
        or reconstruction.get("new_authority_chain_required") is not True
        or reconstruction.get("old_chain_identity_must_not_be_claimed") is not True
        or old.get("recoverable") is not False
        or old.get("claim_new_chain_equals_old_chain") is not False
        or any(value is not False for value in authorization.values())
    ):
        raise BookDepthAuthorityRecoveryError("authority recovery contract is invalid")
    if (
        confirmation.get("dataset_id") != DATASET
        or confirmation.get("status") != "PRE_REGISTERED_BEFORE_CONFIRMATION_DATA_ACCESS"
        or policy.get("schema_version") != 4
        or policy.get("status") != "FROZEN_BEFORE_CONFIRMATION_OUTCOME"
        or supplement.get("status") != "PRE_REGISTERED_BEFORE_SUPPLEMENT_DATA_ACCESS"
        or blocked.get("status") != "BLOCKED_BEFORE_OUTCOME"
        or blocked.get("confirmation_return_accessed") is not False
        or capacity.get("status") != "PASS"
        or capacity.get("confirmation_return_accessed") is not False
        or capacity.get("confirmation_metrics_computed") is not False
        or capacity.get("authority", {}).get("head_seq") != old.get("head_seq")
        or capacity.get("authority", {}).get("head_row_hash") != old.get("head_row_hash")
    ):
        raise BookDepthAuthorityRecoveryError("pre-outcome source evidence drifted")
    parent_window = confirmation.get("confirmation_window")
    supplement_window = supplement.get("supplement_window")
    universe = tuple(str(value) for value in reconstruction.get("universe", ()))
    if (
        not isinstance(parent_window, Mapping)
        or not isinstance(supplement_window, Mapping)
        or int(reconstruction["start_day_ms"]) != int(supplement_window["start_day_ms"])
        or int(reconstruction["end_exclusive_ms"]) != int(parent_window["end_exclusive_ms"])
        or int(supplement_window["end_exclusive_ms"]) != int(parent_window["start_day_ms"])
        or int(reconstruction["day_count"]) != 97
        or universe != tuple(str(value) for value in confirmation["universe"])
        or universe != tuple(str(value) for value in supplement["universe"])
        or len(universe) != 14
        or acceptance.get("complete_day_count") != 91
        or acceptance.get("excluded_day_count") != 6
        or acceptance.get("parent_complete_day_count") != 85
        or acceptance.get("supplement_complete_day_count") != 6
        or acceptance.get("actual_adjacent_24h_decision_count") != 84
        or acceptance.get("accepted_observation_count") != 30_576
        or acceptance.get("all_97_days_must_be_processed") is not True
        or acceptance.get("excluded_days_are_never_imputed") is not True
        or acceptance.get("any_mismatch_blocks_before_alpha_outcome") is not True
    ):
        raise BookDepthAuthorityRecoveryError("frozen reconstruction facts are invalid")
    coverage = policy.get("coverage_decision")
    hard = policy.get("unchanged_hard_gates")
    if (
        not isinstance(coverage, Mapping)
        or coverage.get("daily_total_snapshot_floor_is_admission_gate") is not False
        or coverage.get("all_24_utc_hours_required") is not True
        or coverage.get("minimum_hourly_snapshot_count") != 96
        or not isinstance(hard, Mapping)
        or hard.get("official_archive_checksum_required") is not True
        or hard.get("exact_symmetric_depth_levels_required") is not True
        or hard.get("maximum_inverted_snapshot_ratio") != 0.002
        or hard.get("all_hours_must_align_to_checksum_verified_mark_prices") is not True
        or hard.get("maximum_mark_alignment_deviation_ratio") != 0.01
        or hard.get("network_or_timeout_is_excludable") is not False
        or hard.get("checksum_mismatch_is_excludable") is not False
        or hard.get("unexpected_exception_is_excludable") is not False
    ):
        raise BookDepthAuthorityRecoveryError("v4 quality gates drifted")


def _fixed_observed_at(status_path: Path, requested: int) -> int:
    if not status_path.exists():
        return requested
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        frozen = int(status["generated_at_ms"])
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise BookDepthAuthorityRecoveryError("recovery resume status is invalid") from exc
    if frozen <= 0:
        raise BookDepthAuthorityRecoveryError("recovery knowledge time is invalid")
    return frozen


def _store_facts(
    *, store_path: Path, contract: Mapping[str, Any], confirmation: Mapping[str, Any]
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    reconstruction = contract["reconstruction"]
    start_ms = int(reconstruction["start_day_ms"])
    end_ms = int(reconstruction["end_exclusive_ms"])
    universe = tuple(str(value) for value in reconstruction["universe"])
    store = BinanceSystemicStore(str(store_path))
    try:
        rows = store.range_observations(
            DATASET,
            start_event_time_ms=start_ms + 1,
            end_event_time_ms=end_ms + 1,
            instruments=universe,
        )
        chain_valid = store.chain_valid()
        head = store.authority_head()
        dataset_rows = store.row_count(DATASET)
    finally:
        store.close()
    parent_window = confirmation["confirmation_window"]
    parent = _confirmation_capacity(
        store_path=str(store_path),
        start_day_ms=int(parent_window["start_day_ms"]),
        end_exclusive_ms=int(parent_window["end_exclusive_ms"]),
        universe=universe,
        horizon_hours=int(confirmation["horizon_hours"]),
    )
    combined = _confirmation_capacity(
        store_path=str(store_path),
        start_day_ms=start_ms,
        end_exclusive_ms=end_ms,
        universe=universe,
        horizon_hours=int(confirmation["horizon_hours"]),
    )
    return rows, {
        "dataset_row_count": dataset_rows,
        "authority_head": head,
        "authority_chain_valid": chain_valid,
        "parent_capacity": parent,
        "combined_capacity": combined,
    }


def _verify_exact_result(
    *,
    backfill: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    facts: Mapping[str, Any],
    contract: Mapping[str, Any],
    observed_at_ms: int,
) -> None:
    acceptance = contract["exact_acceptance"]
    excluded = tuple(sorted(int(item["day_ms"]) for item in backfill["quality_exclusions"]))
    expected_excluded = tuple(int(value) for value in acceptance["excluded_days_ms"])
    combined = facts["combined_capacity"]
    parent = facts["parent_capacity"]
    supplement_complete = int(combined["complete_day_count"]) - int(parent["complete_day_count"])
    if (
        backfill.get("status") != "PASS"
        or int(backfill.get("processed_day_count", -1)) != 97
        or int(backfill.get("quality_excluded_day_count", -1)) != 6
        or excluded != expected_excluded
        or len(rows) != int(acceptance["accepted_observation_count"])
        or facts.get("dataset_row_count") != len(rows)
        or facts.get("authority_chain_valid") is not True
        or not isinstance(facts.get("authority_head"), Mapping)
        or facts["authority_head"].get("seq") != len(rows)
        or int(combined["complete_day_count"]) != int(acceptance["complete_day_count"])
        or int(parent["complete_day_count"]) != int(acceptance["parent_complete_day_count"])
        or supplement_complete != int(acceptance["supplement_complete_day_count"])
        or int(combined["actual_adjacent_24h_decision_count"])
        != int(acceptance["actual_adjacent_24h_decision_count"])
        or any(int(row["knowledge_time_ms"]) != observed_at_ms for row in rows)
        or any(int(row["forward_eligible_from_ms"]) != observed_at_ms for row in rows)
        or any(row.get("external_price_alignment") != "PASS" for row in rows)
        or any(
            not item.get("failures")
            or any(
                failure.get("book_depth_checksum_verified") is not True
                or failure.get("mark_price_checksum_verified") is not True
                for failure in item["failures"]
            )
            for item in backfill["quality_exclusions"]
        )
    ):
        raise BookDepthAuthorityRecoveryError(
            "reconstructed authority does not exactly reproduce frozen source facts"
        )


def run_recovery(
    *,
    store_path: str | Path = DEFAULT_STORE,
    archive_root: str | Path = DEFAULT_ARCHIVE,
    status_path: str | Path = DEFAULT_STATUS,
    output_path: str | Path = DEFAULT_OUTPUT,
    observed_at_ms: int | None = None,
    max_workers: int = 4,
    refresh: Callable[..., dict[str, object]] = refresh_book_depth_days,
) -> dict[str, Any]:
    output = Path(output_path)
    if output.exists():
        raise BookDepthAuthorityRecoveryError("authority recovery evidence already exists")
    source_commit = _require_clean_source()
    artifacts = _verify_artifacts()
    contract = artifacts["recovery_contract"]["payload"]
    confirmation = artifacts["confirmation"]["payload"]
    requested = int(time.time() * 1000) if observed_at_ms is None else observed_at_ms
    status = Path(status_path)
    if not status.exists():
        atomic_write_json(
            str(status),
            {
                "schema_version": 1,
                "generated_at_ms": requested,
                "status": "INITIALIZED_BEFORE_NETWORK_ACCESS",
                "mode": "NEW_CONFIRMATION_AUTHORITY_RECONSTRUCTION",
                "alpha_outcome_accessed": False,
                "confirmation_metrics_computed": False,
                "formal_admission": False,
                "testnet_authorized": False,
            },
            indent=2,
        )
    fixed_observed_at = _fixed_observed_at(status, requested)
    if fixed_observed_at <= int(confirmation["frozen_unseen_window_evidence"]["observed_at_ms"]):
        raise BookDepthAuthorityRecoveryError("recovery knowledge time predates confirmation freeze")
    reconstruction = contract["reconstruction"]
    backfill = run_backfill(
        days=int(reconstruction["day_count"]),
        closed_day_ms=int(reconstruction["end_exclusive_ms"]) - DAY_MS,
        observed_at_ms=fixed_observed_at,
        store_path=str(store_path),
        archive_root=str(archive_root),
        status_path=str(status),
        universe=tuple(str(value) for value in reconstruction["universe"]),
        max_workers=max_workers,
        refresh=refresh,
        maximum_quality_excluded_days=int(contract["exact_acceptance"]["excluded_day_count"]),
        minimum_snapshots_per_archive=1,
    )
    rows, facts = _store_facts(
        store_path=Path(store_path), contract=contract, confirmation=confirmation
    )
    _verify_exact_result(
        backfill=backfill,
        rows=rows,
        facts=facts,
        contract=contract,
        observed_at_ms=fixed_observed_at,
    )
    bindings = {
        name: {
            "commit": artifact["commit"],
            "sha256": artifact["sha256"],
            "repository_path": artifact["repository_path"],
        }
        for name, artifact in artifacts.items()
    }
    result = {
        "schema_version": 1,
        "generated_at_ms": int(time.time() * 1000),
        "status": "PASS",
        "mode": "NEW_AUTHORITY_CHAIN_FROM_EXACT_PUBLIC_SOURCE_RECONSTRUCTION",
        "recovery_id": contract["recovery_id"],
        "source_commit": source_commit,
        "knowledge_time_ms": fixed_observed_at,
        "immutable_bindings": bindings,
        "old_authority": contract["old_authority"],
        "new_authority": {
            "dataset_row_count": facts["dataset_row_count"],
            "head_seq": facts["authority_head"]["seq"],
            "head_row_hash": facts["authority_head"]["row_hash"],
            "chain_valid": facts["authority_chain_valid"],
            "claims_old_chain_identity": False,
        },
        "input_observation_count": len(rows),
        "input_observation_sha256": _observation_digest(list(rows)),
        "processed_day_count": backfill["processed_day_count"],
        "complete_day_count": facts["combined_capacity"]["complete_day_count"],
        "quality_excluded_day_count": backfill["quality_excluded_day_count"],
        "quality_excluded_days_ms": sorted(
            int(item["day_ms"]) for item in backfill["quality_exclusions"]
        ),
        "quality_exclusions": backfill["quality_exclusions"],
        "parent_complete_day_count": facts["parent_capacity"]["complete_day_count"],
        "supplement_complete_day_count": (
            int(facts["combined_capacity"]["complete_day_count"])
            - int(facts["parent_capacity"]["complete_day_count"])
        ),
        "actual_adjacent_24h_decision_count": facts["combined_capacity"][
            "actual_adjacent_24h_decision_count"
        ],
        "source_facts_exactly_reproduced": True,
        "alpha_outcome_accessed": False,
        "confirmation_metrics_computed": False,
        "formal_validation_started": False,
        "formal_admission": False,
        "forward_credit": False,
        "testnet_authorized": False,
        "order_submission_authorized": False,
        "safety": _safety(),
    }
    atomic_write_json(str(output), result, indent=2)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconstruct the frozen book-depth confirmation authority"
    )
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--archive-root", default=str(DEFAULT_ARCHIVE))
    parser.add_argument("--status", default=str(DEFAULT_STATUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    result = run_recovery(
        store_path=args.store,
        archive_root=args.archive_root,
        status_path=args.status,
        output_path=args.output,
        max_workers=args.workers,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BookDepthAuthorityRecoveryError",
    "DEFAULT_ARCHIVE",
    "DEFAULT_OUTPUT",
    "DEFAULT_STATUS",
    "DEFAULT_STORE",
    "main",
    "run_recovery",
]
