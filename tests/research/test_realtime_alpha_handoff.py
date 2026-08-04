from __future__ import annotations

from dataclasses import asdict

import pytest

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.research.alpha_production_workflow import CandidateDefinitionKind
from services.research.realtime_alpha_discovery import (
    RealtimeAlphaPolicy,
    default_realtime_hypotheses,
)
from services.research.realtime_alpha_handoff import (
    REALTIME_UNIVERSE,
    RealtimeAlphaHandoffError,
    build_realtime_candidate_identities,
    canonical_realtime_research_policy,
    realtime_research_policy_hash,
)


def _manifest(*, adaptive: bool = False) -> list[dict[str, object]]:
    root = default_realtime_hypotheses()[0]
    row = root.as_dict()
    row["required_features"] = list(root.required_features)
    rows = [row]
    if adaptive:
        child = dict(row)
        child.update(
            {
                "candidate_id": f"{root.candidate_id}_adaptive_g1",
                "signal_version": "realtime-microstructure.feedback.v1",
                "adaptive_feedback": True,
                "parent_candidate_id": root.candidate_id,
                "feedback_cutoff_ms": 123_000,
                "mutation": "shorter_horizon",
                "fresh_oos_required": True,
                "adaptive_generation": 1,
            }
        )
        rows.append(child)
    return rows


def _config_hash(row: dict[str, object]) -> str:
    params = {
        "family_id": str(row["family_id"]),
        "mechanism": str(row["mechanism"]),
        "horizon_ms": str(row["horizon_ms"]),
        "signal_version": str(row["signal_version"]),
        "label_policy": "post_latency_first_known_mid",
    }
    if row.get("adaptive_feedback") is True:
        params.update(
            {
                "adaptive_feedback": "true",
                "parent_candidate_id": str(row["parent_candidate_id"]),
                "feedback_cutoff_ms": str(row["feedback_cutoff_ms"]),
                "mutation": str(row["mutation"]),
                "fresh_oos_required": "true",
            }
        )
    return AlphaDefinition(
        alpha_id=str(row["candidate_id"]),
        name="test",
        factor_ids=tuple(sorted(row["required_features"])),
        universe=REALTIME_UNIVERSE,
        params=params,
    ).definition_hash


def _document(rows: list[dict[str, object]]) -> dict[str, object]:
    policy = canonical_realtime_research_policy(
        RealtimeAlphaPolicy(), feature_version="binance-realtime-feature.v1"
    )
    return {
        "hypotheses": rows,
        "manifest_hash": stable_hash(rows),
        "research_policy": policy,
        "research_policy_hash": realtime_research_policy_hash(policy),
        "feedback": {
            "formal_gate_impact": False,
            "promotion_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "safety": {
            "paper_only": True,
            "live_trading_enabled": False,
            "real_broker_enabled": False,
            "allow_order_submission": False,
            "allow_ai_direct_trading": False,
        },
    }


def _registrations(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    result = []
    for index, row in enumerate(rows):
        result.append(
            {
                **row,
                "config_hash": _config_hash(row),
                "paper_eligible": False,
                "record_hash": stable_hash({"row": row, "index": index}),
                "recorded_at": 1_000 + index,
            }
        )
    return result


def test_builds_root_and_adaptive_canonical_identities() -> None:
    rows = _manifest(adaptive=True)
    identities = build_realtime_candidate_identities(
        _registrations(rows), _document(rows)
    )

    assert len(identities) == 2
    assert all(
        item.definition_kind is CandidateDefinitionKind.REALTIME_MECHANISM
        for item in identities
    )
    assert identities[0].predecessor_id is None
    assert identities[1].predecessor_id == identities[0].candidate_id
    assert identities[1].change_class.value == "MATERIAL_MULTIPLE"
    assert identities[0].decision_interval_ms == RealtimeAlphaPolicy().decision_interval_ms
    assert identities[0].label_horizon_ms == rows[0]["horizon_ms"]
    assert identities[0].expression == ""


@pytest.mark.parametrize(
    "mutation",
    ("policy_hash", "manifest_hash", "config_hash", "paper_authority"),
)
def test_handoff_rejects_tampered_or_widened_evidence(mutation: str) -> None:
    rows = _manifest()
    document = _document(rows)
    registrations = _registrations(rows)
    if mutation == "policy_hash":
        document["research_policy_hash"] = "0" * 64
    elif mutation == "manifest_hash":
        document["manifest_hash"] = "0" * 64
    elif mutation == "config_hash":
        registrations[0]["config_hash"] = "0" * 64
    else:
        registrations[0]["paper_eligible"] = True

    with pytest.raises(RealtimeAlphaHandoffError):
        build_realtime_candidate_identities(registrations, document)


def test_handoff_rejects_adaptive_mechanism_drift() -> None:
    rows = _manifest(adaptive=True)
    rows[1]["mechanism"] = "changed_after_feedback"
    document = _document(rows)
    registrations = _registrations(rows)

    with pytest.raises(RealtimeAlphaHandoffError, match="lineage changes"):
        build_realtime_candidate_identities(registrations, document)


def test_policy_shape_is_complete_and_hash_sensitive() -> None:
    policy = canonical_realtime_research_policy(
        RealtimeAlphaPolicy(), feature_version="fv1"
    )
    assert set(asdict(RealtimeAlphaPolicy())).issubset(policy)
    changed = dict(policy)
    changed["fee_bps"] = float(changed["fee_bps"]) + 0.01
    assert realtime_research_policy_hash(changed) != realtime_research_policy_hash(policy)
