from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from quantos.contracts.hashing import stable_hash
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.experiment_registry.store import _search_identity_hash
from tools import binance_book_depth_experiment_registry_recovery as recovery


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_artifacts(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.db"
    store = SqliteExperimentStore(str(baseline), create=True)
    store.save_catalog_snapshot(
        config_hashes=(f"base-{index}" for index in range(3)),
        alphas=(),
        first_seen_ts=1,
    )
    store.close()
    tree = tmp_path / "restore"
    restored_db = tree / "data" / "cache" / "experiment_registry.db"
    restored_db.parent.mkdir(parents=True)
    shutil.copy2(baseline, restored_db)
    contract = {
        "study_id": "test-study",
        "trial_count": 2,
        "candidate_ids": ["a", "b"],
        "directions": [1],
        "horizon_hours": [24],
        "long_count": 1,
        "short_count": 1,
        "one_way_cost_bps_per_unit_turnover": 1.0,
        "cost_stress_multiplier": 2.0,
    }
    contract_sha = "a" * 64
    identities = recovery._trial_identities(contract, contract_sha)
    dataset_version = "d" * 64
    search_id = "search-" + stable_hash(
        {
            "study": "BINANCE_BOOK_DEPTH_FROZEN_GRID_EXPLORATION",
            "dataset_version": dataset_version,
            "exploration_sha256": contract_sha,
        }
    )[:24]
    expected_identity = {
        "search_id": search_id,
        "search_kind": "BINANCE_BOOK_DEPTH_FROZEN_GRID_EXPLORATION",
        "source": "tools.binance_book_depth_alpha_exploration",
        "dataset_version": dataset_version,
    }
    expected_identity["identity_hash"] = _search_identity_hash(**expected_identity)
    exploration = {
        "generated_at_ms": 10,
        "exploration_sha256": contract_sha,
        "input_observation_sha256": dataset_version,
        "experiment_registry": {
            "search_id": search_id,
            "search_identity": expected_identity,
            "global_trial_count_before": 3,
            "global_trial_count_after": 5,
            "global_trial_count_delta": 2,
            "candidate_count": 2,
            "terminal_status": "COMPLETED",
            "search_history_valid": True,
        },
    }
    monkeypatch.setattr(recovery, "BASELINE_TRIAL_COUNT", 3)
    monkeypatch.setattr(recovery, "RECOVERED_TRIAL_COUNT", 5)
    monkeypatch.setattr(recovery, "BASELINE_REGISTRY_SHA256", _sha(restored_db))
    monkeypatch.setattr(recovery, "BACKUP_MANIFEST_SHA256", "m" * 64)
    monkeypatch.setattr(recovery, "_require_clean_source", lambda: "c" * 40)
    monkeypatch.setattr(
        recovery,
        "_artifact",
        lambda path, **_kwargs: (
            (contract_sha, contract)
            if path == recovery.DEFAULT_EXPLORATION_CONTRACT
            else ("e" * 64, exploration)
        ),
    )
    manifest = {"observed_at_ms": 5, "source_contract_sha256": "s" * 64}
    monkeypatch.setattr(
        recovery, "_load_verified_baseline", lambda _tree: (restored_db, manifest)
    )
    return tree, contract, identities


def test_recovery_replays_only_frozen_registration_and_matches_evidence(
    tmp_path, monkeypatch
):
    tree, _contract, _identities = _fake_artifacts(tmp_path, monkeypatch)
    output = tmp_path / "recovered.db"
    evidence = tmp_path / "evidence.json"

    result = recovery.recover_registry(
        restored_tree=tree,
        output_db_path=output,
        evidence_path=evidence,
    )

    assert result["status"] == "PASS"
    assert result["global_trial_count_before"] == 3
    assert result["global_trial_count_after"] == 5
    assert result["global_trial_count_delta"] == 2
    assert result["candidate_count"] == 2
    assert result["terminal_status"] == "COMPLETED"
    assert result["search_history_valid"] is True
    assert result["alpha_outcome_accessed"] is False
    assert result["market_observations_accessed"] is False
    assert result["testnet_authorized"] is False
    assert json.loads(evidence.read_text(encoding="utf-8")) == result

    with pytest.raises(recovery.BookDepthRegistryRecoveryError, match="already exists"):
        recovery.recover_registry(
            restored_tree=tree,
            output_db_path=output,
            evidence_path=evidence,
        )


def test_recovery_rejects_nonexact_baseline_before_copy(tmp_path, monkeypatch):
    tree, _contract, _identities = _fake_artifacts(tmp_path, monkeypatch)
    monkeypatch.setattr(
        recovery,
        "_verify_baseline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            recovery.BookDepthRegistryRecoveryError("not exact")
        ),
    )
    output = tmp_path / "recovered.db"

    with pytest.raises(recovery.BookDepthRegistryRecoveryError, match="not exact"):
        recovery.recover_registry(
            restored_tree=tree,
            output_db_path=output,
            evidence_path=tmp_path / "evidence.json",
        )

    assert not output.exists()
