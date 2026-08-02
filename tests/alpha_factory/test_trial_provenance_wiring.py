"""P5-1 wiring: the two canonical search entries write 727 §7.1 TrialRecords.

The money tests: TrialProvenance persistence existed but was NEVER CALLED by any
driver. These tests prove `run_alpha_factory` and
`run_registered_candidate_batch` now write a full TrialRecord per registered
candidate — and stay honest where information is genuinely absent.
"""

from __future__ import annotations

import pytest

import services.research.alpha_expression as ax
import services.research.cs_genome as cs_genome
from quantos.contracts.alpha import AlphaDefinition
from services.intelligence import registered_candidate_batch as batch_module
from services.research.alpha_factory import run_alpha_factory
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.trial_provenance import (
    ActorType,
    TrialProvenance,
    TrialStatus,
)

FIELDS = ["close", "funding"]
WINDOWS = [3, 5]


def _panel() -> ax.FieldPanel:
    close = [[100.0 + t + i for i in range(6)] for t in range(30)]
    funding = [[0.001 * (i - 2.5) for i in range(6)] for _ in range(30)]
    return ax.FieldPanel(
        [f"C{i}" for i in range(6)],
        list(range(30)),
        {"close": close, "funding": funding},
    )


def _registry() -> ExperimentRegistry:
    return ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))


def _factory_run(registry: ExperimentRegistry, search_id: str, **overrides):
    kwargs = dict(
        generations=1,
        population_size=4,
        elite_k=2,
        dataset_version="dataset-v1",
        timestamp=50,
        search_registry=registry,
        search_id=search_id,
        search_kind="ALPHA_FACTORY",
        search_source="tests.alpha_factory",
    )
    kwargs.update(overrides)
    return run_alpha_factory(_panel(), FIELDS, WINDOWS, **kwargs)


def _provenance_records(registry: ExperimentRegistry, config_hash: str) -> list[dict]:
    return [row["record"] for row in registry.trial_provenance_for_config(config_hash)]


def test_factory_writes_full_trial_record_per_candidate() -> None:
    registry = _registry()
    result = _factory_run(registry, "factory-p5")

    candidates = registry.search_candidates("factory-p5")
    assert len(candidates) == result["n_evaluated"] >= 1
    for row in candidates:
        records = _provenance_records(registry, row["config_hash"])
        assert len(records) == 1
        record = records[0]
        assert record["trial_id"] == f"factory-p5:{row['config_hash']}"
        assert record["research_session_id"] == "factory-p5"
        assert record["canonical_expression"]  # non-empty for every candidate
        assert record["expression_hash"]
        assert record["model_name"] == "cs_genome-evolution"
        assert record["actor_type"] == "AUTOMATED_SEARCH"
        assert record["actor_id"] == "tests.alpha_factory"
        assert record["status"] == "RECORDED"  # factory tracks no elimination stage
        assert record["dataset_versions"] == ["dataset-v1"]
        assert record["prompt_hash"] == ""  # no LLM in the deterministic engine
        assert record["hypothesis_id"] == ""  # honest: caller supplied none
        # canonical form is the canonicalize()d twin of the raw expression
        expr = ax.deserialize(record["alpha_expression"])
        assert record["canonical_expression"] == ax.serialize(ax.canonicalize(expr))
    assert registry.trial_provenance_chain_valid() is True


def test_factory_hypothesis_binding_flows_when_supplied() -> None:
    registry = _registry()
    seeds = cs_genome.seed_alphas(FIELDS, WINDOWS)
    target_alpha_id = ax.alpha_id(seeds[0])
    _factory_run(
        registry,
        "factory-hyp",
        population_size=len(seeds),
        hypothesis_by_candidate={target_alpha_id: "hyp_seed0"},
    )

    bound, unbound = 0, 0
    for row in registry.search_candidates("factory-hyp"):
        for record in _provenance_records(registry, row["config_hash"]):
            if record["expression_hash"] == target_alpha_id:
                assert record["hypothesis_id"] == "hyp_seed0"
                bound += 1
            elif record["hypothesis_id"] == "":
                unbound += 1
    assert bound == 1
    assert unbound >= 1  # unsupplied candidates stay honestly unbound


def test_factory_repeated_attempts_across_sessions_all_counted() -> None:
    """§7.2「重复候选也必须记账」: same config in two sessions = two rows."""
    registry = _registry()
    first = _factory_run(registry, "factory-r1")
    _factory_run(registry, "factory-r2")

    config_hash = registry.search_candidates("factory-r1")[0]["config_hash"]
    records = _provenance_records(registry, config_hash)
    assert len(records) == 2
    assert {r["trial_id"] for r in records} == {
        f"factory-r1:{config_hash}",
        f"factory-r2:{config_hash}",
    }
    assert len(registry.search_candidates("factory-r2")) == first["n_evaluated"]
    assert registry.trial_provenance_chain_valid() is True


def test_provenance_record_is_idempotent_on_replay() -> None:
    registry = _registry()
    record = TrialProvenance(
        trial_id="session-x:cfg-x",
        research_session_id="session-x",
        config_hash="cfg-x",
        actor_id="tests.alpha_factory",
        actor_type=ActorType.AUTOMATED_SEARCH,
        status=TrialStatus.RECORDED,
        started_at=1,
        completed_at=1,
    )
    assert registry.record_trial_provenance(record) is True
    assert registry.record_trial_provenance(record) is False  # replay is a no-op
    assert len(registry.trial_provenance_for_config("cfg-x")) == 1
    assert registry.trial_provenance_chain_valid() is True


def test_ephemeral_factory_rejects_provenance_pins() -> None:
    with pytest.raises(ValueError, match="ephemeral"):
        run_alpha_factory(
            _panel(), FIELDS, WINDOWS,
            generations=1, population_size=4, elite_k=2,
            dataset_version="dataset-v1", timestamp=50,
            ephemeral=True, hypothesis_by_candidate={"a": "hyp_x"},
        )
    with pytest.raises(ValueError, match="ephemeral"):
        run_alpha_factory(
            _panel(), FIELDS, WINDOWS,
            generations=1, population_size=4, elite_k=2,
            dataset_version="dataset-v1", timestamp=50,
            ephemeral=True, code_hash="abc123",
        )


# --- run_registered_candidate_batch -----------------------------------------


def _batch_candidates():
    expr = cs_genome.seed_alphas(FIELDS, WINDOWS)[0]
    serialized = ax.serialize(expr)
    dsl = AlphaDefinition(
        alpha_id="batch-dsl",
        name="Batch DSL",
        factor_ids=("close",),
        params={"expr": serialized},
    )
    plain = AlphaDefinition(
        alpha_id="batch-plain",
        name="Batch Plain",
        factor_ids=("close",),
        params={"family": "non-dsl", "index": "0"},
    )
    return (dsl, serialized, expr), ((dsl, ()), (plain, ()))


def _run_batch(registry: ExperimentRegistry, monkeypatch, **overrides):
    (dsl, _serialized, _expr), pool = _batch_candidates()

    def fake_run(self, received, **_kwargs):
        return {
            "report": {d.alpha_id: {"state": None} for d, _periods in received},
            "human_review_queue": [],
        }

    monkeypatch.setattr(batch_module.ResearchAgentLoop, "run", fake_run)
    kwargs = dict(
        registry=registry,
        search_id="batch-p5",
        search_kind="CURATED_CANDIDATE_BATCH",
        search_source="tests.alpha_factory",
        dataset_version="dataset-v1",
        recorded_at=100,
        config=None,
    )
    kwargs.update(overrides)
    return dsl, batch_module.run_registered_candidate_batch(pool, **kwargs)


def test_batch_writes_trial_record_per_candidate(monkeypatch) -> None:
    registry = _registry()
    dsl, _result = _run_batch(registry, monkeypatch)

    rows = registry.search_candidates("batch-p5")
    assert len(rows) == 2
    records = [
        record
        for row in rows
        for record in _provenance_records(registry, row["config_hash"])
    ]
    assert len(records) == 2
    by_alpha = {
        ("dsl" if r["alpha_expression"] else "plain"): r for r in records
    }
    dsl_record = by_alpha["dsl"]
    expr = ax.deserialize(dsl_record["alpha_expression"])
    assert dsl_record["canonical_expression"] == ax.serialize(ax.canonicalize(expr))
    assert dsl_record["expression_hash"] == ax.alpha_id(expr)
    # honest absence: a non-DSL candidate records no fabricated identity
    assert by_alpha["plain"]["canonical_expression"] == ""
    assert by_alpha["plain"]["expression_hash"] == ""
    for record in records:
        assert record["trial_id"].startswith("batch-p5:")
        assert record["research_session_id"] == "batch-p5"
        assert record["actor_type"] == "AUTOMATED_SEARCH"
        assert record["status"] == "RECORDED"
        assert record["model_name"] == ""  # batch evaluates; the driver generates
        assert record["dataset_versions"] == ["dataset-v1"]
        assert record["hypothesis_id"] == ""
    assert registry.trial_provenance_chain_valid() is True


def test_batch_hypothesis_and_code_hash_flow(monkeypatch) -> None:
    registry = _registry()
    dsl, _result = _run_batch(
        registry,
        monkeypatch,
        hypothesis_by_candidate={"batch-dsl": "hyp_batch0"},
        code_hash="abc123def456",
    )

    rows = registry.search_candidates("batch-p5")
    records = {
        record["hypothesis_id"]: record
        for row in rows
        for record in _provenance_records(registry, row["config_hash"])
    }
    assert records["hyp_batch0"]["alpha_expression"] == dsl.params["expr"]
    assert records["hyp_batch0"]["code_hash"] == "abc123def456"
    assert records[""]["code_hash"] == "abc123def456"


def test_batch_rejects_invalid_provenance_kwargs(monkeypatch) -> None:
    registry = _registry()
    (_dsl, _s, _e), pool = _batch_candidates()
    with pytest.raises(TypeError, match="hypothesis_by_candidate"):
        batch_module.run_registered_candidate_batch(
            pool,
            registry=registry,
            search_id="batch-bad",
            search_kind="K",
            search_source="S",
            dataset_version="dataset-v1",
            recorded_at=1,
            hypothesis_by_candidate="not-a-mapping",
        )
    with pytest.raises(TypeError, match="code_hash"):
        batch_module.run_registered_candidate_batch(
            pool,
            registry=registry,
            search_id="batch-bad2",
            search_kind="K",
            search_source="S",
            dataset_version="dataset-v1",
            recorded_at=1,
            code_hash=123,
        )
