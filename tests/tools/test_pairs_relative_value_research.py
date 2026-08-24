import json

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import ValidationVerdict
from services.alpha.evaluation_evidence import bind_evaluation_reproducibility
from tools.pairs_relative_value_research import (
    CONTRACT_HASH,
    EVALUATION_CONTRACT,
    PRIMARY_ALIAS,
    _stored_evaluation,
    candidate_definitions,
    judge,
)


def test_frozen_contract_has_exact_three_candidates_and_no_scan():
    assert len(EVALUATION_CONTRACT["candidate_aliases"]) == 3
    assert EVALUATION_CONTRACT["parameter_scan"] is False
    assert EVALUATION_CONTRACT["formation_rows"] == 180
    assert EVALUATION_CONTRACT["trade_rows"] == 90
    assert EVALUATION_CONTRACT["pair_count"] == 10
    assert len(CONTRACT_HASH) == 64


def test_contract_identity_does_not_bind_registry_wall_clock():
    assert "recorded_at" not in EVALUATION_CONTRACT
    assert "generated_at_ms" not in EVALUATION_CONTRACT


def test_definitions_are_unique_and_bind_the_contract():
    definitions = candidate_definitions()
    assert len(definitions) == 3
    assert len({item[0].alpha_id for item in definitions}) == 3
    assert len({item[0].definition_hash for item in definitions}) == 3
    assert all(item[0].params["contract_hash"] == CONTRACT_HASH for item in definitions)


def _fixture_inputs(verdict="PASS", dsr=0.99, pbo=0.1):
    ids = {
        PRIMARY_ALIAS: "primary",
        "pairs_distance_continuation_v1": "sign",
        "pairs_hash_convergence_v1": "hash",
    }
    evaluations = {
        "primary": {
            "verified": True,
            "verdict": verdict,
            "deflated_sharpe": dsr,
            "pbo": pbo,
        }
    }
    base = {
        "net_sharpe": 1.4,
        "net_return": 0.2,
        "max_drawdown": 0.08,
    }
    replays = {
        "primary": {"base_cost": base, "double_base_cost": {**base, "net_return": 0.1}},
        "sign": {"base_cost": {**base, "net_sharpe": -1.4}},
        "hash": {"base_cost": {**base, "net_sharpe": 0.3}},
    }
    holdout = {"net_sharpe": 1.0, "net_return": 0.03}
    return evaluations, replays, ids, holdout


def test_judge_requires_every_frozen_bar():
    decision = judge(*_fixture_inputs())
    assert decision["formal_pass"] is True
    assert decision["economic_candidate"] is True
    assert decision["forward_admission_allowed"] is False


def test_governance_review_can_reject_formally_without_erasing_economics():
    decision = judge(*_fixture_inputs(verdict="REQUIRES_HUMAN_REVIEW"))
    assert decision["formal_pass"] is False
    assert decision["economic_candidate"] is True
    assert decision["failed_bars"] == ["evaluator_verdict_pass"]


def test_immutable_rejection_can_be_published_when_full_evidence_is_incomplete():
    definition = AlphaDefinition(alpha_id="a", name="a", params={"x": "1"})
    evaluation = bind_evaluation_reproducibility(
        AlphaEvaluation(
            alpha_id="a",
            verdict=ValidationVerdict.REQUIRES_HUMAN_REVIEW,
            reasons=("CATALOG_EVIDENCE_INVALID",),
        ),
        dataset_version="dataset-v1",
    )
    payload = evaluation.model_dump(mode="json")
    row = {
        "alpha_id": "a",
        "dataset_version": "dataset-v1",
        "evaluation_json": json.dumps(payload, sort_keys=True),
        "content_hash": stable_hash(
            {"evaluation": payload, "dataset_version": "dataset-v1"}
        ),
    }

    class Registry:
        def get_evaluation(self, run_id):
            assert run_id == "run-1"
            return row

        def verify_evaluation(self, run_id):
            return False

        def search_evaluations(self, search_id):
            assert search_id == "search-1"
            return [{
                "run_id": "run-1",
                "config_hash": definition.definition_hash,
                "evaluation_content_hash": row["content_hash"],
            }]

    alpha_id, stored = _stored_evaluation(
        Registry(), "search-1", "run-1", {"a": definition}
    )
    assert alpha_id == "a"
    assert stored["verified"] is True
    assert stored["full_evidence_verified"] is False
    assert stored["reasons"] == ["CATALOG_EVIDENCE_INVALID"]
