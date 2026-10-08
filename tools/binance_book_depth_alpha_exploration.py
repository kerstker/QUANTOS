"""Run the frozen Binance book-depth direction/horizon exploration.

The driver binds the immutable failed pre-registered result, frozen exploration
contract, governed observation bytes and the durable experiment registry before
it evaluates any trial.  Exploration can select a lead for an independent
confirmation study, but it cannot admit an Alpha or authorize execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
from collections.abc import Mapping, Sequence

from quantos.contracts.hashing import stable_hash
from services.data.binance_book_depth_archive import DATASET, DAY_MS, HOUR_MS
from services.data.binance_systemic_store import BinanceSystemicStore
from services.research.book_depth_alpha import evaluate_exploration
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import (
    ExperimentStoreError,
    SqliteExperimentStore,
)
from tools.binance_book_depth_alpha_research import (
    DEFAULT_CONTRACT,
    _observation_digest,
    _require_clean_source,
    _safety,
    _sha256,
    _verify_preregistration,
)
from tools.cache_io import atomic_write_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPLORATION = ROOT / "config" / "binance_book_depth_alpha_exploration.json"
DEFAULT_SOURCE_EVIDENCE = ROOT / "evidence" / "binance_book_depth_alpha_research.json"
DEFAULT_STORE = Path("data") / "cache" / "binance_book_depth_archive.db"
DEFAULT_EXPERIMENT_DB = Path("data") / "cache" / "experiment_registry.db"
DEFAULT_OUTPUT = ROOT / "evidence" / "binance_book_depth_alpha_exploration.json"
EXPLORATION_COMMIT = "074cf6022db87ba6d11d6ed25f092b9ccaa5315f"
SOURCE_EVIDENCE_COMMIT = "1b3b146e3b5f8230f218440db23bbdd43610d1c1"


class BookDepthExplorationError(RuntimeError):
    """The exploration cannot prove immutable source or trial provenance."""


def _committed_json(
    path: Path, *, commit: str, repository_path: str
) -> tuple[str, dict]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
        committed_blob = subprocess.run(
            ["git", "rev-parse", f"{commit}:{repository_path}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        worktree_blob = subprocess.run(
            ["git", "hash-object", "--path", repository_path, str(path)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        committed = subprocess.run(
            ["git", "show", f"{commit}:{repository_path}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
        raise BookDepthExplorationError(
            f"immutable artifact cannot be verified: {repository_path}"
        ) from exc
    if not isinstance(payload, dict) or committed_blob != worktree_blob:
        raise BookDepthExplorationError(
            f"artifact differs from immutable commit: {repository_path}"
        )
    # Bind the repository-normalized bytes.  This treats CRLF checkout
    # conversion exactly as Git does while still rejecting every content drift.
    return _sha256(committed), payload


def _trial_identities(exploration: Mapping[str, object], contract_sha: str) -> tuple[dict, ...]:
    return tuple(
        {
            "schema_version": 1,
            "study_id": exploration["study_id"],
            "candidate_id": candidate_id,
            "direction": int(direction),
            "horizon_hours": int(horizon_hours),
            "long_count": exploration["long_count"],
            "short_count": exploration["short_count"],
            "one_way_cost_bps_per_unit_turnover": exploration[
                "one_way_cost_bps_per_unit_turnover"
            ],
            "cost_stress_multiplier": exploration["cost_stress_multiplier"],
            "exploration_contract_sha256": contract_sha,
        }
        for candidate_id in exploration["candidate_ids"]  # type: ignore[union-attr]
        for direction in exploration["directions"]  # type: ignore[union-attr]
        for horizon_hours in exploration["horizon_hours"]  # type: ignore[union-attr]
    )


def _register_trials_before_evaluation(
    *,
    registry: ExperimentRegistry,
    identities: Sequence[Mapping[str, object]],
    dataset_version: str,
    exploration_sha256: str,
    recorded_at: int,
) -> dict:
    config_hashes = tuple(stable_hash(identity) for identity in identities)
    if len(config_hashes) != len(set(config_hashes)):
        raise BookDepthExplorationError("exploration trial identities collide")
    search_id = "search-" + stable_hash(
        {
            "study": "BINANCE_BOOK_DEPTH_FROZEN_GRID_EXPLORATION",
            "dataset_version": dataset_version,
            "exploration_sha256": exploration_sha256,
        }
    )[:24]
    before = registry._store.trial_count()
    try:
        events = registry.search_attempt_events(search_id)
    except ExperimentStoreError as exc:
        if "has not STARTED" not in str(exc):
            raise
        events = []
    if not events:
        registry.begin_search_attempt(
            search_id=search_id,
            search_kind="BINANCE_BOOK_DEPTH_FROZEN_GRID_EXPLORATION",
            source="tools.binance_book_depth_alpha_exploration",
            dataset_version=dataset_version,
            recorded_at=recorded_at,
        )
        events = registry.search_attempt_events(search_id)
    identity = registry.search_identity(search_id)
    expected_identity = {
        "search_id": search_id,
        "search_kind": "BINANCE_BOOK_DEPTH_FROZEN_GRID_EXPLORATION",
        "source": "tools.binance_book_depth_alpha_exploration",
        "dataset_version": dataset_version,
    }
    if any(identity.get(key) != value for key, value in expected_identity.items()):
        raise BookDepthExplorationError("durable search identity mismatch")
    terminal = events[-1]["status"] if events else None
    if terminal == "FAILED":
        raise BookDepthExplorationError("durable exploration attempt is FAILED")
    if terminal not in {"STARTED", "COMPLETED"}:
        raise BookDepthExplorationError("durable exploration status is invalid")
    if terminal == "STARTED":
        registry.register_search_candidates(
            search_id, config_hashes, registered_at=recorded_at
        )
    registered = tuple(
        row["config_hash"] for row in registry.search_candidates(search_id)
    )
    if registered != config_hashes:
        raise BookDepthExplorationError(
            "durable search candidate population is incomplete or reordered"
        )
    after_registration = registry._store.trial_count()
    return {
        "search_id": search_id,
        "search_identity": identity,
        "config_hashes": config_hashes,
        "terminal_status": terminal,
        "global_trial_count_before": before,
        "global_trial_count_after_registration": after_registration,
        "global_trial_count_delta": after_registration - before,
    }


def _load_bound_input(
    *, store_path: str | Path, preregistration: Mapping[str, object], source: Mapping[str, object]
) -> tuple[tuple[dict, ...], dict]:
    if source.get("status") != "FAIL" or source.get("formal_admission") is not False:
        raise BookDepthExplorationError("source diagnostic is not the immutable failed study")
    if source.get("dataset") != DATASET:
        raise BookDepthExplorationError("source diagnostic dataset mismatch")
    horizon_ms = int(preregistration["forward_horizon_hours"]) * HOUR_MS
    common_end = int(source["last_decision_event_time_ms"]) + horizon_ms
    start = common_end - int(preregistration["history_days"]) * DAY_MS
    universe = tuple(str(value) for value in preregistration["universe"])  # type: ignore[union-attr]
    store = BinanceSystemicStore(str(store_path))
    try:
        observations = store.range_observations(
            DATASET,
            start_event_time_ms=start + 1,
            end_event_time_ms=common_end + 1,
            instruments=universe,
        )
        if not store.chain_valid():
            raise BookDepthExplorationError("book-depth authority chain is invalid")
        authority_head = store.authority_head()
    finally:
        store.close()
    digest = _observation_digest(list(observations))
    if len(observations) != int(source["input_observation_count"]):
        raise BookDepthExplorationError("source observation count cannot be replayed")
    if digest != source["input_observation_sha256"]:
        raise BookDepthExplorationError("source observation digest cannot be replayed")
    if authority_head != source["authority_head"]:
        raise BookDepthExplorationError("source authority head drifted before exploration")
    return observations, {
        "input_observation_count": len(observations),
        "input_observation_sha256": digest,
        "input_start_exclusive_ms": start,
        "input_end_inclusive_ms": common_end,
        "authority_head": authority_head,
    }


def run_exploration(
    *,
    store_path: str | Path,
    experiment_db_path: str | Path,
    preregistration_path: str | Path = DEFAULT_CONTRACT,
    exploration_path: str | Path = DEFAULT_EXPLORATION,
    source_evidence_path: str | Path = DEFAULT_SOURCE_EVIDENCE,
    output_path: str | Path = DEFAULT_OUTPUT,
    generated_at_ms: int | None = None,
) -> dict:
    now_ms = int(time.time() * 1000) if generated_at_ms is None else generated_at_ms
    source_commit = _require_clean_source()
    prereg_commit, prereg_sha, preregistration = _verify_preregistration(
        Path(preregistration_path)
    )
    exploration_sha, exploration = _committed_json(
        Path(exploration_path),
        commit=EXPLORATION_COMMIT,
        repository_path="config/binance_book_depth_alpha_exploration.json",
    )
    source_evidence_sha, source_evidence = _committed_json(
        Path(source_evidence_path),
        commit=SOURCE_EVIDENCE_COMMIT,
        repository_path="evidence/binance_book_depth_alpha_research.json",
    )
    if exploration.get("source_evidence") != "evidence/binance_book_depth_alpha_research.json":
        raise BookDepthExplorationError("exploration source evidence binding drifted")
    observations, input_binding = _load_bound_input(
        store_path=store_path,
        preregistration=preregistration,
        source=source_evidence,
    )
    identities = _trial_identities(exploration, exploration_sha)
    if len(identities) != int(exploration["trial_count"]):
        raise BookDepthExplorationError("frozen exploration trial population is incomplete")

    experiment_store = SqliteExperimentStore(str(experiment_db_path), create=False)
    registry = ExperimentRegistry(experiment_store)
    try:
        if not registry.search_history_valid():
            raise BookDepthExplorationError("experiment search authority is invalid")
        registration = _register_trials_before_evaluation(
            registry=registry,
            identities=identities,
            dataset_version=input_binding["input_observation_sha256"],
            exploration_sha256=exploration_sha,
            recorded_at=now_ms,
        )
        diagnostic = evaluate_exploration(
            observations, preregistration, exploration
        )
        if diagnostic["trial_count"] != len(registration["config_hashes"]):
            raise BookDepthExplorationError("evaluated and registered trial counts differ")
        events = registry.search_attempt_events(registration["search_id"])
        if events[-1]["status"] == "STARTED":
            registry.finish_search_attempt(
                registration["search_id"], status="COMPLETED", recorded_at=now_ms
            )
        terminal_events = registry.search_attempt_events(registration["search_id"])
        if terminal_events[-1]["status"] != "COMPLETED":
            raise BookDepthExplorationError("durable exploration is not terminal")
        registry_after = experiment_store.trial_count()
        search_history_valid = registry.search_history_valid()
    finally:
        experiment_store.close()

    result = {
        "schema_version": 1,
        "generated_at_ms": now_ms,
        **diagnostic,
        "dataset": DATASET,
        "preregistration_commit": prereg_commit,
        "preregistration_sha256": prereg_sha,
        "exploration_commit": EXPLORATION_COMMIT,
        "exploration_sha256": exploration_sha,
        "source_evidence_commit": SOURCE_EVIDENCE_COMMIT,
        "source_evidence_sha256": source_evidence_sha,
        "source_commit": source_commit,
        **input_binding,
        "experiment_registry": {
            "search_id": registration["search_id"],
            "search_identity": registration["search_identity"],
            "candidate_count": len(registration["config_hashes"]),
            "global_trial_count_before": registration["global_trial_count_before"],
            "global_trial_count_after": registry_after,
            "global_trial_count_delta": (
                registry_after - registration["global_trial_count_before"]
            ),
            "terminal_status": "COMPLETED",
            "search_history_valid": search_history_valid,
        },
        "safety": _safety(),
    }
    atomic_write_json(str(output_path), result, indent=2)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run frozen book-depth Alpha exploration")
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--experiment-db", default=str(DEFAULT_EXPERIMENT_DB))
    parser.add_argument("--preregistration", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--exploration", default=str(DEFAULT_EXPLORATION))
    parser.add_argument("--source-evidence", default=str(DEFAULT_SOURCE_EVIDENCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    result = run_exploration(
        store_path=args.store,
        experiment_db_path=args.experiment_db,
        preregistration_path=args.preregistration,
        exploration_path=args.exploration,
        source_evidence_path=args.source_evidence,
        output_path=args.output,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "LEAD_SELECTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BookDepthExplorationError",
    "DEFAULT_EXPERIMENT_DB",
    "DEFAULT_EXPLORATION",
    "DEFAULT_OUTPUT",
    "DEFAULT_SOURCE_EVIDENCE",
    "DEFAULT_STORE",
    "EXPLORATION_COMMIT",
    "SOURCE_EVIDENCE_COMMIT",
    "main",
    "run_exploration",
]
