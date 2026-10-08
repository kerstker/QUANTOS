import json

import pytest

from quantos.contracts.hashing import stable_hash
from tools.paper_candidate_universe import (
    STATIC_ALPHA_IDS,
    paper_candidate_ids,
    realtime_mechanism_candidate_ids,
    shadow_paper_candidate_ids,
)


def test_candidate_universe_unions_forward_and_evaluation_artifacts(tmp_path):
    status = tmp_path / "status.json"
    evaluations = tmp_path / "evaluations.json"
    status.write_text(
        json.dumps(
            {
                "alphas": {
                    "z_variant": {"n_forward_periods": 1},
                    "crowd_contra_q20": {"n_forward_periods": 1},
                }
            }
        ),
        encoding="utf-8",
    )
    evaluations.write_text(
        json.dumps({"alphas": {"a_variant": {"status": "VERIFIED"}}}),
        encoding="utf-8",
    )

    assert paper_candidate_ids(
        forward_status=status,
        evaluations=evaluations,
        workflow=tmp_path / "missing-workflow.json",
    ) == STATIC_ALPHA_IDS + ("a_variant", "crowd_contra_q20", "z_variant")


def test_candidate_universe_falls_back_to_static_without_optional_artifacts(tmp_path):
    assert paper_candidate_ids(
        forward_status=tmp_path / "missing-status.json",
        evaluations=tmp_path / "missing-evaluations.json",
        workflow=tmp_path / "missing-workflow.json",
    ) == STATIC_ALPHA_IDS


def test_verified_workflow_is_the_unique_population_authority(tmp_path):
    workflow = tmp_path / "workflow.json"
    identities = [
        {"candidate_id": alpha_id, "expression": "expr"}
        for alpha_id in (*STATIC_ALPHA_IDS, "authoritative_candidate")
    ]
    workflow.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authority": "ALPHA_PRODUCTION_WORKFLOW",
                "status": "VERIFIED",
                "active_candidate_ids": [
                    *STATIC_ALPHA_IDS,
                    "authoritative_candidate",
                ],
                "candidates": [
                    {
                        "candidate_id": identity["candidate_id"],
                        "identity": identity,
                        "identity_hash": stable_hash(identity),
                    }
                    for identity in identities
                ],
                "safety": {
                    "live_allowed": False,
                    "order_submission_allowed": False,
                },
            }
        ),
        encoding="utf-8",
    )
    forward = tmp_path / "forward.json"
    forward.write_text(
        json.dumps({"alphas": {"stale_candidate": {}}}), encoding="utf-8"
    )

    assert paper_candidate_ids(
        workflow=workflow,
        forward_status=forward,
        evaluations=tmp_path / "missing.json",
    ) == STATIC_ALPHA_IDS + ("authoritative_candidate",)


def test_existing_invalid_workflow_fails_closed(tmp_path):
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({"status": "INVALID"}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="not verified"):
        paper_candidate_ids(workflow=workflow)


def test_shadow_scope_routes_verified_realtime_mechanisms_to_realtime_workers(tmp_path):
    workflow = tmp_path / "workflow.json"
    realtime_identity = {
        "candidate_id": "rt_basis_5m",
        "expression": "",
        "definition_kind": "REALTIME_MECHANISM",
    }
    daily_identity = {
        "candidate_id": "daily_challenger",
        "expression": "expr",
    }
    identities = [
        *[
            {"candidate_id": alpha_id, "expression": "expr"}
            for alpha_id in STATIC_ALPHA_IDS
        ],
        daily_identity,
        realtime_identity,
    ]
    workflow.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authority": "ALPHA_PRODUCTION_WORKFLOW",
                "status": "VERIFIED",
                "active_candidate_ids": [
                    *STATIC_ALPHA_IDS,
                    "daily_challenger",
                    "rt_basis_5m",
                ],
                "candidates": [
                    {
                        "candidate_id": identity["candidate_id"],
                        "identity": identity,
                        "identity_hash": stable_hash(identity),
                    }
                    for identity in identities
                ],
                "safety": {
                    "live_allowed": False,
                    "order_submission_allowed": False,
                },
            }
        ),
        encoding="utf-8",
    )

    population = paper_candidate_ids(workflow=workflow)
    assert population == STATIC_ALPHA_IDS + ("daily_challenger", "rt_basis_5m")
    assert shadow_paper_candidate_ids(
        workflow=workflow,
        candidate_ids=population,
    ) == STATIC_ALPHA_IDS + ("daily_challenger",)
    assert realtime_mechanism_candidate_ids(
        workflow=workflow,
        candidate_ids=population,
    ) == ("rt_basis_5m",)
