"""Fail-closed composition for selection-bearing research tool entrypoints."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import os

from quantos.contracts.hashing import stable_hash
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import (
    ExperimentStoreError,
    SqliteExperimentStore,
)


PRODUCTION_EXPERIMENT_DB = os.path.join("data", "cache", "experiment_registry.db")


def make_search_id(
    *,
    source: str,
    search_kind: str,
    dataset_version: str,
    recorded_at: int,
    params: Mapping[str, object] | None = None,
) -> str:
    digest = stable_hash(
        {
            "dataset_version": dataset_version,
            "params": dict(params or {}),
            "recorded_at": recorded_at,
            "search_kind": search_kind,
            "source": source,
        }
    )
    return f"search-{digest[:24]}"


def field_panel_dataset_version(
    panel,
    *,
    source_versions: Mapping[str, object] | None = None,
) -> str:
    """Bind a factory dataset version to the exact in-memory panel inputs."""
    matrices = getattr(panel, "matrices", None)
    instruments = getattr(panel, "instruments", None)
    event_times = getattr(panel, "event_times", None)
    if not isinstance(matrices, Mapping) or instruments is None or event_times is None:
        raise TypeError("panel must expose instruments, event_times, and matrices")
    return stable_hash(
        {
            "event_times": tuple(event_times),
            "instruments": tuple(instruments),
            "matrix_sha256": {
                name: stable_hash({"rows": rows})
                for name, rows in sorted(matrices.items())
            },
            "source_versions": dict(source_versions or {}),
        }
    )


@contextmanager
def production_experiment_registry() -> Iterator[ExperimentRegistry]:
    if not os.path.isfile(PRODUCTION_EXPERIMENT_DB):
        raise ExperimentStoreError(
            "production experiment registry is missing; refusing to create it implicitly"
        )
    store = SqliteExperimentStore(PRODUCTION_EXPERIMENT_DB, create=False)
    try:
        store.trial_count()
        yield ExperimentRegistry(store)
    finally:
        store.close()
