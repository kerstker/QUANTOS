"""Fail-closed composition for selection-bearing research tool entrypoints."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import json
import os

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.hashing import stable_hash
from services.alpha.evaluation_evidence import verify_evaluation_reproducibility
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


def stored_search_evaluation(
    registry: ExperimentRegistry,
    search_id: str,
    run_id: str,
    definitions: Mapping[str, AlphaDefinition],
) -> tuple[str, dict[str, object]]:
    """Verify immutable content and exact search binding without hiding rejection.

    ``registry.verify_evaluation`` additionally requires complete DSR/PBO
    evidence. A fail-closed evaluation can be immutable and reproducible while
    that stronger completeness result is false; retain both facts so a durable
    negative result can still be published instead of being rerun.
    """

    row = registry.get_evaluation(str(run_id))
    if not isinstance(row, Mapping):
        raise RuntimeError("stored evaluation is missing")
    alpha_id = str(row.get("alpha_id", ""))
    definition = definitions.get(alpha_id)
    if definition is None:
        raise RuntimeError("stored evaluation is not part of the frozen family")
    links = [
        item
        for item in registry.search_evaluations(search_id)
        if isinstance(item, Mapping) and str(item.get("run_id", "")) == str(run_id)
    ]
    if (
        len(links) != 1
        or links[0].get("config_hash") != definition.definition_hash
        or links[0].get("evaluation_content_hash") != row.get("content_hash")
    ):
        raise RuntimeError("stored evaluation has no unique exact-definition binding")
    try:
        payload = json.loads(str(row["evaluation_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("stored evaluation payload is invalid") from exc
    content_hash = stable_hash(
        {"evaluation": payload, "dataset_version": row.get("dataset_version")}
    )
    if content_hash != row.get("content_hash"):
        raise RuntimeError("stored evaluation content hash mismatch")
    try:
        evaluation = AlphaEvaluation.model_validate(payload)
    except Exception as exc:
        raise RuntimeError("stored evaluation contract is invalid") from exc
    if not verify_evaluation_reproducibility(
        evaluation, dataset_version=str(row.get("dataset_version", ""))
    ):
        raise RuntimeError("stored evaluation reproducibility hash mismatch")
    return alpha_id, {
        "run_id": str(run_id),
        "content_hash": row.get("content_hash"),
        "verified": True,
        "full_evidence_verified": registry.verify_evaluation(str(run_id)) is True,
        "verdict": payload.get("verdict"),
        "reasons": list(payload.get("reasons") or ()),
        "net_sharpe": payload.get("net_sharpe"),
        "deflated_sharpe": payload.get("deflated_sharpe"),
        "pbo": payload.get("pbo"),
        "n_periods": payload.get("n_periods"),
        "catalog_trial_count": payload.get("catalog_trial_count"),
        "catalog_trial_set_hash": payload.get("catalog_trial_set_hash"),
        "catalog_evidence_hash": payload.get("catalog_evidence_hash"),
        "pbo_evidence_hash": payload.get("pbo_evidence_hash"),
        "reproducibility_hash": payload.get("reproducibility_hash"),
    }


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
