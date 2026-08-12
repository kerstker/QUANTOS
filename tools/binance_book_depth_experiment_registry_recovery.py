"""Recover the lost book-depth exploration registry from a verified L2 restore.

The recovery is deliberately narrow: it accepts only an already isolated,
officially restored L2 tree whose manifest and experiment-registry snapshot
match the frozen pre-exploration baseline.  It then replays only the immutable
60-candidate registration transaction and requires the resulting search
identity and 2763 -> 2823 trial-count transition to match committed evidence.

No Alpha outcome, market observation, credential, broker or order surface is
read by this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from collections.abc import Mapping, Sequence
from typing import Any

from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from tools.binance_book_depth_alpha_confirmation import (
    DEFAULT_EXPLORATION_CONTRACT,
    DEFAULT_EXPLORATION_EVIDENCE,
    EXPLORATION_CONTRACT_COMMIT,
    EXPLORATION_EVIDENCE_COMMIT,
    _artifact,
)
from tools.binance_book_depth_alpha_exploration import (
    _register_trials_before_evaluation,
    _trial_identities,
)
from tools.binance_book_depth_alpha_research import _require_clean_source
from tools.cache_io import atomic_write_json
from tools.l2_backup import (
    DEFAULT_SOURCES,
    MANIFEST_SCHEMA_VERSION,
    _source_contract,
    _source_contract_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESTORED_TREE = (
    ROOT / ".runtime" / "confirmation-registry-restore-20260812T014609Z"
)
DEFAULT_OUTPUT_DB = (
    ROOT / ".runtime" / "book-depth-confirmation" / "experiment_registry.db"
)
DEFAULT_EVIDENCE = (
    ROOT / "evidence" / "binance_book_depth_experiment_registry_recovery.json"
)
BACKUP_BATCH_ID = "20260812T014609Z"
BACKUP_MANIFEST_SHA256 = (
    "e9026fcfebcf7fd1143d40e15d9a0b5b5c6fd9acd5881606f0a5e8a7ce6930df"
)
BASELINE_REGISTRY_SHA256 = (
    "c61a9538a3a785c9d2f1a742c7f55fd034ff4f3ee4bdcfc4a9b49fd9341d6750"
)
BASELINE_TRIAL_COUNT = 2763
RECOVERED_TRIAL_COUNT = 2823


class BookDepthRegistryRecoveryError(RuntimeError):
    """The frozen experiment-registry authority cannot be recovered safely."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_verified_baseline(restored_tree: str | Path) -> tuple[Path, dict[str, Any]]:
    tree = Path(restored_tree).resolve()
    manifest_path = tree / ".quantos_backup_manifest.json"
    registry_path = tree / "data" / "cache" / "experiment_registry.db"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BookDepthRegistryRecoveryError("verified restore manifest is unreadable") from exc
    expected_contract = _source_contract(DEFAULT_SOURCES)
    expected_contract_sha = _source_contract_sha256(expected_contract)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION
        or manifest.get("mode") != "L2_AUTHORITY_STATE"
        or manifest.get("source_contract") != expected_contract
        or manifest.get("source_contract_sha256") != expected_contract_sha
        or _sha256_file(manifest_path) != BACKUP_MANIFEST_SHA256
        or int(manifest.get("observed_at_ms", 0)) <= 0
    ):
        raise BookDepthRegistryRecoveryError("restored L2 source contract drifted")
    entries = manifest.get("entries")
    entry = next(
        (
            item
            for item in entries
            if isinstance(item, Mapping)
            and item.get("relative_path") == "data/cache/experiment_registry.db"
        ),
        None,
    ) if isinstance(entries, list) else None
    if (
        not isinstance(entry, Mapping)
        or entry.get("role") != "experiment_registry"
        or entry.get("required") is not True
        or entry.get("snapshot_method") != "SQLITE_ONLINE_BACKUP"
        or entry.get("sha256") != BASELINE_REGISTRY_SHA256
        or not registry_path.is_file()
        or registry_path.stat().st_size != int(entry.get("size", -1))
        or _sha256_file(registry_path) != BASELINE_REGISTRY_SHA256
    ):
        raise BookDepthRegistryRecoveryError("restored experiment registry digest drifted")
    return registry_path, manifest


def _verify_baseline(path: Path, *, expected_search_id: str) -> dict[str, Any]:
    store = SqliteExperimentStore(str(path), create=False)
    registry = ExperimentRegistry(store)
    try:
        trial_count = store.trial_count()
        search_valid = registry.search_history_valid()
        trial_chain_valid = registry.trial_provenance_chain_valid()
        oos_chain_valid = registry.trial_oos_access_chain_valid()
        integrity = store._conn.execute("PRAGMA integrity_check").fetchone()[0]
        existing_search = store._conn.execute(
            "SELECT 1 FROM search_attempt_events WHERE search_id=? LIMIT 1",
            (expected_search_id,),
        ).fetchone()
    finally:
        store.close()
    if (
        trial_count != BASELINE_TRIAL_COUNT
        or not search_valid
        or not trial_chain_valid
        or not oos_chain_valid
        or integrity != "ok"
        or existing_search is not None
    ):
        raise BookDepthRegistryRecoveryError("restored registry is not the exact baseline")
    return {
        "trial_count": trial_count,
        "search_history_valid": search_valid,
        "trial_provenance_chain_valid": trial_chain_valid,
        "oos_access_chain_valid": oos_chain_valid,
        "sqlite_integrity": integrity,
        "target_search_absent": True,
    }


def recover_registry(
    *,
    restored_tree: str | Path = DEFAULT_RESTORED_TREE,
    output_db_path: str | Path = DEFAULT_OUTPUT_DB,
    evidence_path: str | Path = DEFAULT_EVIDENCE,
) -> dict[str, Any]:
    output_db = Path(output_db_path).resolve()
    evidence = Path(evidence_path).resolve()
    if output_db.exists() or evidence.exists():
        raise BookDepthRegistryRecoveryError("registry recovery output already exists")
    source_commit = _require_clean_source()
    contract_sha, contract = _artifact(
        DEFAULT_EXPLORATION_CONTRACT,
        commit=EXPLORATION_CONTRACT_COMMIT,
        repository_path="config/binance_book_depth_alpha_exploration.json",
    )
    evidence_sha, exploration = _artifact(
        DEFAULT_EXPLORATION_EVIDENCE,
        commit=EXPLORATION_EVIDENCE_COMMIT,
        repository_path="evidence/binance_book_depth_alpha_exploration.json",
    )
    registry_evidence = exploration.get("experiment_registry")
    expected_increment = int(contract.get("trial_count", 0))
    expected_final_count = BASELINE_TRIAL_COUNT + expected_increment
    if (
        not isinstance(registry_evidence, Mapping)
        or registry_evidence.get("global_trial_count_before") != BASELINE_TRIAL_COUNT
        or registry_evidence.get("global_trial_count_after") != expected_final_count
        or registry_evidence.get("global_trial_count_delta") != expected_increment
        or registry_evidence.get("candidate_count") != expected_increment
        or registry_evidence.get("terminal_status") != "COMPLETED"
        or registry_evidence.get("search_history_valid") is not True
        or exploration.get("exploration_sha256") != contract_sha
    ):
        raise BookDepthRegistryRecoveryError("committed exploration registry evidence drifted")
    search_id = str(registry_evidence["search_id"])
    baseline_path, manifest = _load_verified_baseline(restored_tree)
    baseline = _verify_baseline(baseline_path, expected_search_id=search_id)
    output_db.parent.mkdir(parents=True, exist_ok=True)
    partial = output_db.with_name(output_db.name + ".partial")
    if partial.exists():
        raise BookDepthRegistryRecoveryError("stale partial recovery output exists")
    shutil.copy2(baseline_path, partial)
    try:
        store = SqliteExperimentStore(str(partial), create=False)
        registry = ExperimentRegistry(store)
        try:
            identities = _trial_identities(contract, contract_sha)
            registration = _register_trials_before_evaluation(
                registry=registry,
                identities=identities,
                dataset_version=str(exploration["input_observation_sha256"]),
                exploration_sha256=contract_sha,
                recorded_at=int(exploration["generated_at_ms"]),
            )
            if registration["search_id"] != search_id:
                raise BookDepthRegistryRecoveryError("recovered search id drifted")
            if registration["terminal_status"] != "STARTED":
                raise BookDepthRegistryRecoveryError("recovery did not create a new search")
            registry.finish_search_attempt(
                search_id,
                status="COMPLETED",
                recorded_at=int(exploration["generated_at_ms"]),
            )
            final_identity = registry.search_identity(search_id)
            final_candidates = tuple(
                row["config_hash"] for row in registry.search_candidates(search_id)
            )
            final_events = registry.search_attempt_events(search_id)
            final_count = store.trial_count()
            search_valid = registry.search_history_valid()
            trial_chain_valid = registry.trial_provenance_chain_valid()
            oos_chain_valid = registry.trial_oos_access_chain_valid()
            integrity = store._conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            store.close()
        if (
            final_count != expected_final_count
            or final_count != RECOVERED_TRIAL_COUNT
            or final_count - baseline["trial_count"] != expected_increment
            or final_identity != registry_evidence.get("search_identity")
            or len(final_candidates) != expected_increment
            or final_events[-1].get("status") != "COMPLETED"
            or not search_valid
            or not trial_chain_valid
            or not oos_chain_valid
            or integrity != "ok"
        ):
            raise BookDepthRegistryRecoveryError("recovered registry does not match evidence")
        partial.replace(output_db)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    result = {
        "schema_version": 1,
        "status": "PASS",
        "mode": "VERIFIED_L2_BASELINE_PLUS_IMMUTABLE_SEARCH_REGISTRATION_REPLAY",
        "source_commit": source_commit,
        "backup_batch_id": BACKUP_BATCH_ID,
        "backup_observed_at_ms": manifest["observed_at_ms"],
        "backup_manifest_sha256": BACKUP_MANIFEST_SHA256,
        "source_contract_sha256": manifest["source_contract_sha256"],
        "baseline_registry_sha256": BASELINE_REGISTRY_SHA256,
        "baseline": baseline,
        "exploration_contract_commit": EXPLORATION_CONTRACT_COMMIT,
        "exploration_contract_sha256": contract_sha,
        "exploration_evidence_commit": EXPLORATION_EVIDENCE_COMMIT,
        "exploration_evidence_sha256": evidence_sha,
        "search_id": search_id,
        "search_identity": final_identity,
        "candidate_count": len(final_candidates),
        "global_trial_count_before": baseline["trial_count"],
        "global_trial_count_after": final_count,
        "global_trial_count_delta": final_count - baseline["trial_count"],
        "terminal_status": final_events[-1]["status"],
        "search_history_valid": search_valid,
        "trial_provenance_chain_valid": trial_chain_valid,
        "oos_access_chain_valid": oos_chain_valid,
        "sqlite_integrity": integrity,
        "alpha_outcome_accessed": False,
        "market_observations_accessed": False,
        "credentials_accessed": False,
        "broker_connectivity": False,
        "order_submission": False,
        "formal_admission": False,
        "testnet_authorized": False,
    }
    atomic_write_json(str(evidence), result, indent=2)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recover the frozen book-depth exploration registry"
    )
    parser.add_argument("--restored-tree", default=str(DEFAULT_RESTORED_TREE))
    parser.add_argument("--output-db", default=str(DEFAULT_OUTPUT_DB))
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    args = parser.parse_args(argv)
    result = recover_registry(
        restored_tree=args.restored_tree,
        output_db_path=args.output_db,
        evidence_path=args.evidence,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BookDepthRegistryRecoveryError",
    "DEFAULT_EVIDENCE",
    "DEFAULT_OUTPUT_DB",
    "DEFAULT_RESTORED_TREE",
    "main",
    "recover_registry",
]
