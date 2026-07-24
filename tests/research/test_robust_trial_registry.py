from __future__ import annotations

from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore


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
