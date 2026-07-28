from __future__ import annotations

from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.robust_optimization import CandidateSpec
from tools.crypto_robust_optimization import _register_trials


def test_robust_trial_budget_survives_store_reopen(tmp_path) -> None:
    path = str(tmp_path / "experiment_registry.db")
    first = SqliteExperimentStore(path, create=True)
    registry = ExperimentRegistry(first)
    registry.begin_search_attempt(
        search_id="robust-a", search_kind="ROBUST_CRYPTO_GRID", source="test",
        dataset_version="data-v1", recorded_at=1,
    )
    assert registry.register_search_candidates("robust-a", ["alpha-a", "alpha-b", "alpha-a"], registered_at=1) == 2
    registry.finish_search_attempt("robust-a", status="COMPLETED", recorded_at=2)
    assert first.trial_count() == 2
    first.close()

    second = SqliteExperimentStore(path, create=False)
    try:
        assert second.trial_count() == 2
        registry = ExperimentRegistry(second)
        registry.begin_search_attempt(
            search_id="robust-b", search_kind="ROBUST_CRYPTO_GRID", source="test",
            dataset_version="data-v1", recorded_at=3,
        )
        assert registry.register_search_candidates("robust-b", ["alpha-b", "alpha-c"], registered_at=3) == 2
        registry.finish_search_attempt("robust-b", status="COMPLETED", recorded_at=4)
        assert second.trial_count() == 3
    finally:
        second.close()


def test_robust_grid_registers_immutable_provenance(tmp_path) -> None:
    path = str(tmp_path / "experiment_registry.db")
    candidates = [
        CandidateSpec((1.0, 0.0, 0.0), 1.0, 0.10),
        CandidateSpec((0.0, 1.0, 0.0), 1.0, 0.10),
    ]
    bootstrap = SqliteExperimentStore(path, create=True)
    bootstrap.close()
    result = _register_trials(candidates, "data-v1", path)
    assert result["registered"] == 2

    store = SqliteExperimentStore(path, create=False)
    try:
        registry = ExperimentRegistry(store)
        records = [
            row["record"]
            for candidate in candidates
            for row in registry.trial_provenance_for_config(candidate.config_hash)
        ]
        assert len(records) == len(candidates)
        assert {record["actor_id"] for record in records} == {
            "tools.crypto_robust_optimization"
        }
        assert all(
            record["parameter_set"]["candidate"] for record in records
        )
        assert registry.trial_provenance_chain_valid() is True
    finally:
        store.close()
