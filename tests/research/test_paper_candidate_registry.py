from __future__ import annotations

import sqlite3

import pytest

from quantos.contracts.hashing import stable_hash
from services.research import alpha_expression as ax
from services.research.paper_candidate_registry import (
    PaperCandidate,
    PaperCandidateRegistry,
    PaperCandidateRegistryError,
)


EXPRESSION = ("cs_rank", ("neg", ("field", "global_ls")))


def _candidate(**updates: object) -> PaperCandidate:
    values = {
        "alpha_id": ax.alpha_id(EXPRESSION),
        "expression": ax.serialize(EXPRESSION),
        "definition_hash": stable_hash(
            {"expression": ax.serialize(EXPRESSION), "quantile": 0.3, "cadence_days": 7}
        ),
        "source_search_id": "factory-search-1",
        "evaluation_run_id": "evaluation-run-1",
        "dataset_version": "dataset-v1",
        "quantile": 0.3,
        "cadence_days": 7,
        "admitted_at_ms": 1234,
    }
    values.update(updates)
    return PaperCandidate(**values)


def test_registry_is_idempotent_append_only_and_hash_chained(tmp_path) -> None:
    path = tmp_path / "paper_candidates.db"
    candidate = _candidate()
    with PaperCandidateRegistry(path) as registry:
        assert registry.admit(candidate) is True
        assert registry.admit(candidate) is False
        assert registry.all() == (candidate,)
        assert registry.chain_valid() is True
        assert registry.snapshot()["status"] == "VERIFIED"
        assert registry.snapshot()["live_allowed"] is False

        with pytest.raises(PaperCandidateRegistryError, match="different immutable"):
            registry.admit(
                _candidate(dataset_version="dataset-v2")
            )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            registry._connection.execute(  # noqa: SLF001 - verifies DB authority
                "DELETE FROM paper_candidates"
            )

    with PaperCandidateRegistry(path, create=False) as reopened:
        assert reopened.all() == (candidate,)
        assert reopened.chain_valid() is True


@pytest.mark.parametrize(
    "updates,match",
    (
        ({"alpha_id": "ax_wrong"}, "alpha_id"),
        ({"expression": "(broken"}, "expression"),
        ({"definition_hash": "bad"}, "definition_hash"),
        ({"source_search_id": ""}, "source_search_id"),
        ({"evaluation_run_id": ""}, "evaluation_run_id"),
        ({"dataset_version": ""}, "dataset_version"),
        ({"quantile": 0.0}, "quantile"),
        ({"quantile": 0.6}, "quantile"),
        ({"cadence_days": 0}, "cadence_days"),
        ({"admitted_at_ms": -1}, "admitted_at_ms"),
    ),
)
def test_registry_rejects_malformed_candidate(tmp_path, updates, match) -> None:
    with PaperCandidateRegistry(tmp_path / "paper_candidates.db") as registry:
        with pytest.raises(PaperCandidateRegistryError, match=match):
            registry.admit(_candidate(**updates))
