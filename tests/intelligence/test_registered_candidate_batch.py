from __future__ import annotations

from dataclasses import dataclass

import pytest

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.cost import CostModel
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.validation.selection_authority import (
    LEGACY_INVALIDATION_CODE,
    SelectionAuthorityBlocked,
)
from services.intelligence import registered_candidate_batch as batch_module
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.ports.persistence import ExperimentStoreError
from tools import crypto_alpha_search


run_registered_candidate_batch = (
    batch_module._run_registered_candidate_batch_legacy_diagnostic
)


def _registry() -> ExperimentRegistry:
    return ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))


def test_public_registered_batch_is_quarantined_before_validation():
    with pytest.raises(SelectionAuthorityBlocked, match=LEGACY_INVALIDATION_CODE):
        batch_module.run_registered_candidate_batch(object())


def _candidates(count: int):
    return tuple(
        (
            AlphaDefinition(
                alpha_id=f"candidate-{index}",
                name=f"Candidate {index}",
                factor_ids=("close",),
                params={
                    "family": "registered-batch-test",
                    "index": str(index),
                },
            ),
            (),
        )
        for index in range(count)
    )


@dataclass(frozen=True)
class _FakeVerdict:
    value: str = "REQUIRES_HUMAN_REVIEW"


@dataclass(frozen=True)
class _FakeEvaluation:
    alpha_id: str
    reproducibility_hash: str
    verdict: _FakeVerdict = _FakeVerdict()

    def model_dump(self):
        return {
            "alpha_id": self.alpha_id,
            "reproducibility_hash": self.reproducibility_hash,
            "verdict": self.verdict.value,
        }


def test_candidate_pool_is_durable_before_agent_scoring(monkeypatch) -> None:
    registry = _registry()
    candidates = _candidates(4)

    def assert_pre_registered(self, received, **_kwargs):
        assert tuple(received) == candidates
        assert [
            row["status"]
            for row in registry.search_attempt_events("registered-batch")
        ] == ["STARTED"]
        assert {
            row["config_hash"]
            for row in registry.search_candidates("registered-batch")
        } == {definition.definition_hash for definition, _periods in candidates}
        assert registry._store.trial_count() == len(candidates)
        return {
            "report": {
                definition.alpha_id: {"state": None}
                for definition, _periods in candidates
            },
            "human_review_queue": [],
        }

    monkeypatch.setattr(
        batch_module.ResearchAgentLoop,
        "run",
        assert_pre_registered,
    )
    result = run_registered_candidate_batch(
        candidates,
        registry=registry,
        search_id="registered-batch",
        search_kind="CURATED_CANDIDATE_BATCH",
        search_source="tests.intelligence",
        dataset_version="dataset-v1",
        recorded_at=100,
    )

    assert result["registered_candidate_count"] == len(candidates)
    assert result["selection_authority"] is False
    assert result["evidence_status"] == "DIAGNOSTIC_ONLY"
    assert result["invalidation_code"] == "INVALIDATED_PERP_LEDGER_V1"
    assert [
        row["status"]
        for row in registry.search_attempt_events("registered-batch")
    ] == ["STARTED", "COMPLETED"]


def test_registered_batch_passes_explicit_execution_contract_to_evaluator(monkeypatch) -> None:
    registry = _registry()
    candidates = _candidates(2)
    expected = CostModel(fee_bps=4.0, slippage_bps=2.0, impact_coef=0.0)
    captured = {}

    def capture_evaluator(
        _catalog,
        _config,
        *,
        quantile,
        target_aum,
        periods_per_year,
        cost,
    ):
        captured.update(
            cost=cost,
            quantile=quantile,
            target_aum=target_aum,
            periods_per_year=periods_per_year,
        )
        return object()

    def no_score(self, received, **_kwargs):
        assert tuple(received) == candidates
        return {
            "report": {
                definition.alpha_id: {"state": None}
                for definition, _periods in candidates
            },
            "human_review_queue": [],
        }

    monkeypatch.setattr(batch_module, "AlphaEvaluator", capture_evaluator)
    monkeypatch.setattr(batch_module.ResearchAgentLoop, "run", no_score)

    run_registered_candidate_batch(
        candidates,
        registry=registry,
        search_id="explicit-cost-batch",
        search_kind="CURATED_CANDIDATE_BATCH",
        search_source="tests.intelligence",
        dataset_version="dataset-v1",
        recorded_at=101,
        periods_per_year=1_095,
        quantile=0.2,
        target_aum=1_250_000.0,
        cost=expected,
    )

    assert captured == {
        "cost": expected,
        "quantile": 0.2,
        "target_aum": 1_250_000.0,
        "periods_per_year": 1_095,
    }


@pytest.mark.parametrize("target_aum", [True, 0, -1.0, float("inf")])
def test_registered_batch_rejects_invalid_target_aum_before_writes(
    target_aum,
) -> None:
    registry = _registry()

    with pytest.raises(ValueError, match="target_aum must be finite and positive"):
        run_registered_candidate_batch(
            _candidates(2),
            registry=registry,
            search_id="invalid-aum-batch",
            search_kind="CURATED_CANDIDATE_BATCH",
            search_source="tests.intelligence",
            dataset_version="dataset-v1",
            recorded_at=101,
            target_aum=target_aum,
        )

    assert registry._store.trial_count() == 0
    assert registry.search_attempt_events("invalid-aum-batch") == []


def test_failed_agent_run_retains_candidates_and_failed_terminal(monkeypatch) -> None:
    registry = _registry()
    candidates = _candidates(3)

    def fail_after_registration(self, _received, **_kwargs):
        assert registry._store.trial_count() == len(candidates)
        raise RuntimeError("agent scorer failed")

    monkeypatch.setattr(
        batch_module.ResearchAgentLoop,
        "run",
        fail_after_registration,
    )
    with pytest.raises(RuntimeError, match="agent scorer failed"):
        run_registered_candidate_batch(
            candidates,
            registry=registry,
            search_id="failed-batch",
            search_kind="CURATED_CANDIDATE_BATCH",
            search_source="tests.intelligence",
            dataset_version="dataset-v1",
            recorded_at=200,
        )

    assert [
        row["status"]
        for row in registry.search_attempt_events("failed-batch")
    ] == ["STARTED", "FAILED"]
    assert len(registry.search_candidates("failed-batch")) == len(candidates)
    assert registry._store.trial_count() == len(candidates)


def test_batch_never_sweeps_stale_attempts_implicitly(monkeypatch) -> None:
    registry = _registry()
    registry.begin_search_attempt(
        search_id="crashed-prior-batch",
        search_kind="CURATED_CANDIDATE_BATCH",
        source="tests.intelligence",
        dataset_version="dataset-v0",
        recorded_at=10,
    )

    monkeypatch.setattr(
        batch_module.ResearchAgentLoop,
        "run",
        lambda _self, received, **_kwargs: {
            "report": {
                definition.alpha_id: {"state": None}
                for definition, _periods in received
            },
            "human_review_queue": [],
        },
    )
    with pytest.raises(ValueError, match="automatic stale recovery is disabled"):
        run_registered_candidate_batch(
            _candidates(2),
            registry=registry,
            search_id="recovery-batch",
            search_kind="CURATED_CANDIDATE_BATCH",
            search_source="tests.intelligence",
            dataset_version="dataset-v1",
            recorded_at=100,
            stale_search_timeout_ms=50,
        )
    assert [
        row["status"]
        for row in registry.search_attempt_events("crashed-prior-batch")
    ] == ["STARTED"]

    assert registry.recover_stale_search_attempts(
        recorded_at=100,
        timeout_ms=50,
    ) == ("crashed-prior-batch",)
    result = run_registered_candidate_batch(
        _candidates(2),
        registry=registry,
        search_id="recovery-batch",
        search_kind="CURATED_CANDIDATE_BATCH",
        search_source="tests.intelligence",
        dataset_version="dataset-v1",
        recorded_at=100,
    )
    assert result["recovered_search_ids"] == ()
    assert [
        row["status"]
        for row in registry.search_attempt_events("crashed-prior-batch")
    ] == ["STARTED", "FAILED"]
    assert [
        row["status"] for row in registry.search_attempt_events("recovery-batch")
    ] == ["STARTED", "COMPLETED"]


def test_failed_terminal_write_does_not_mask_original_agent_error(
    monkeypatch,
) -> None:
    registry = _registry()
    candidates = _candidates(2)

    def fail_agent(self, _received, **_kwargs):
        raise RuntimeError("original scorer failure")

    original_finish = registry.finish_search_attempt

    def fail_terminal(search_id, *, status, recorded_at):
        if status == "FAILED":
            raise ExperimentStoreError("terminal write failed")
        return original_finish(
            search_id,
            status=status,
            recorded_at=recorded_at,
        )

    monkeypatch.setattr(batch_module.ResearchAgentLoop, "run", fail_agent)
    monkeypatch.setattr(registry, "finish_search_attempt", fail_terminal)

    with pytest.raises(RuntimeError, match="original scorer failure") as captured:
        run_registered_candidate_batch(
            candidates,
            registry=registry,
            search_id="terminal-write-failed",
            search_kind="CURATED_CANDIDATE_BATCH",
            search_source="tests.intelligence",
            dataset_version="dataset-v1",
            recorded_at=225,
        )

    notes = getattr(captured.value, "__notes__", ())
    assert any(
        "ExperimentStoreError" in note and "terminal write failed" in note
        for note in notes
    )
    assert [
        row["status"]
        for row in registry.search_attempt_events("terminal-write-failed")
    ] == ["STARTED"]
    assert len(registry.search_candidates("terminal-write-failed")) == 2


def test_agent_evaluations_and_catalog_are_saved_before_completion(
    monkeypatch,
) -> None:
    registry = _registry()
    candidates = _candidates(2)

    def return_evaluations(self, received, **_kwargs):
        report = {}
        for definition, _periods in received:
            self.catalog.register(definition)
            report[definition.alpha_id] = {
                "state": "DRAFT",
                "evaluation": _FakeEvaluation(
                    alpha_id=definition.alpha_id,
                    reproducibility_hash=f"repro-{definition.alpha_id}",
                ),
            }
        return {"report": report, "human_review_queue": []}

    monkeypatch.setattr(
        batch_module.ResearchAgentLoop,
        "run",
        return_evaluations,
    )
    result = run_registered_candidate_batch(
        candidates,
        registry=registry,
        search_id="saved-batch",
        search_kind="CURATED_CANDIDATE_BATCH",
        search_source="tests.intelligence",
        dataset_version="dataset-v1",
        recorded_at=250,
    )

    assert len(result["recorded_run_ids"]) == len(candidates)
    assert {
        row["run_id"] for row in registry.search_evaluations("saved-batch")
    } == set(result["recorded_run_ids"])
    definitions_by_alpha_id = {
        definition.alpha_id: definition for definition, _periods in candidates
    }
    for link in registry.search_evaluations("saved-batch"):
        evaluation_row = registry.get_evaluation(link["run_id"])
        assert link["config_hash"] == definitions_by_alpha_id[
            evaluation_row["alpha_id"]
        ].definition_hash
    assert {
        row["alpha_id"] for row in registry._store.all_alphas()
    } == {definition.alpha_id for definition, _periods in candidates}
    for definition, _periods in candidates:
        assert len(registry.list_evaluations(definition.alpha_id)) == 1
    assert [
        row["status"] for row in registry.search_attempt_events("saved-batch")
    ] == ["STARTED", "COMPLETED"]


def test_periods_per_year_is_explicitly_owned_by_registered_batch(
    monkeypatch,
) -> None:
    registry = _registry()
    observed = {}

    def inspect_evaluator(self, received, **_kwargs):
        observed["periods_per_year"] = self.evaluator.periods_per_year
        return {
            "report": {
                definition.alpha_id: {"state": None}
                for definition, _periods in received
            },
            "human_review_queue": [],
        }

    monkeypatch.setattr(batch_module.ResearchAgentLoop, "run", inspect_evaluator)
    run_registered_candidate_batch(
        _candidates(1),
        registry=registry,
        search_id="weekly-batch",
        search_kind="CURATED_CANDIDATE_BATCH",
        search_source="tests.intelligence",
        dataset_version="dataset-v1",
        recorded_at=275,
        periods_per_year=52,
    )
    assert observed == {"periods_per_year": 52}
    for invalid in (True, 0, -1, 52.0):
        with pytest.raises(ValueError, match="periods_per_year"):
            run_registered_candidate_batch(
                _candidates(1),
                registry=registry,
                search_id=f"invalid-{invalid}",
                search_kind="CURATED_CANDIDATE_BATCH",
                search_source="tests.intelligence",
                dataset_version="dataset-v1",
                recorded_at=276,
                periods_per_year=invalid,
            )


def test_two_dataset_reevaluations_preserve_existing_paper_lifecycle_row(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "experiment_registry.db"
    registry = ExperimentRegistry(SqliteExperimentStore(str(path), create=True))
    candidates = _candidates(1)
    definition = candidates[0][0]

    lifecycle_catalog = AlphaCatalog()
    lifecycle_catalog.register(definition)
    lifecycle_catalog.set_returns(definition.alpha_id, (0.01, -0.02, 0.03))
    lifecycle_catalog._set_state(definition.alpha_id, AlphaState.PAPER_TRADING)
    registry.save(lifecycle_catalog, now=10)

    def lifecycle_row() -> tuple:
        row = registry._store._conn.execute(
            "SELECT alpha_id,definition_json,state,returns_json,sharpe,pvalue "
            "FROM alpha_state WHERE alpha_id=?",
            (definition.alpha_id,),
        ).fetchone()
        assert row is not None
        return tuple(row)

    before = lifecycle_row()
    assert before[2] == "PAPER_TRADING"

    invocation = 0

    def return_dataset_evaluation(self, received, **_kwargs):
        nonlocal invocation
        invocation += 1
        assert tuple(received) == candidates
        self.catalog.register(definition)
        return {
            "report": {
                definition.alpha_id: {
                    "state": "DRAFT",
                    "evaluation": _FakeEvaluation(
                        alpha_id=definition.alpha_id,
                        reproducibility_hash=f"dataset-repro-{invocation}",
                    ),
                }
            },
            "human_review_queue": [],
        }

    monkeypatch.setattr(
        batch_module.ResearchAgentLoop,
        "run",
        return_dataset_evaluation,
    )

    for index, dataset_version in enumerate(("dataset-v1", "dataset-v2"), start=1):
        search_id = f"reevaluation-{index}"
        result = run_registered_candidate_batch(
            candidates,
            registry=registry,
            search_id=search_id,
            search_kind="CURATED_CANDIDATE_BATCH",
            search_source="tests.intelligence",
            dataset_version=dataset_version,
            recorded_at=300 + index,
        )
        assert len(result["recorded_run_ids"]) == 1
        assert [
            row["status"] for row in registry.search_attempt_events(search_id)
        ] == ["STARTED", "COMPLETED"]
        assert lifecycle_row() == before

    evaluations = registry.list_evaluations(definition.alpha_id)
    assert {row["dataset_version"] for row in evaluations} == {
        "dataset-v1",
        "dataset-v2",
    }
    assert lifecycle_row() == before


def test_crypto_alpha_search_registers_candidates_outside_top_fifteen(
    monkeypatch,
) -> None:
    registry = _registry()
    candidates = _candidates(20)

    def assert_complete_pool(self, received, **_kwargs):
        assert tuple(received) == candidates
        assert registry._store.trial_count() == 20
        return {
            "report": {
                definition.alpha_id: {"state": None}
                for definition, _periods in candidates
            },
            "human_review_queue": [],
        }

    monkeypatch.setattr(
        batch_module.ResearchAgentLoop,
        "run",
        assert_complete_pool,
    )
    monkeypatch.setattr(
        crypto_alpha_search,
        "run_registered_candidate_batch",
        run_registered_candidate_batch,
    )
    crypto_alpha_search._run_registered_pool(
        candidates,
        registry=registry,
        search_id="crypto-full-pool",
        dataset_version="dataset-v1",
        recorded_at=300,
    )

    registered = registry.search_candidates("crypto-full-pool")
    assert len(registered) == 20
    assert registered[15]["config_hash"] == candidates[15][0].definition_hash
    assert registry._store.trial_count() == 20
