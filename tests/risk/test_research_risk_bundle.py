from __future__ import annotations

import json

import pytest

from services.risk.research_risk.bundle import (
    BUNDLE_PROVENANCE,
    forward_decision_payload_sha256,
    load_research_risk_bundle,
)


DECISION_TS = 1_700_000_000_000


def _pending() -> dict:
    return {
        "alpha_id": "crowd_contra",
        "decided_ts": DECISION_TS,
        "recorded_at": DECISION_TS + 100,
        "weights_json": json.dumps({"BTC": 0.5, "ETH": -0.5}),
        "signals_json": json.dumps({"BTC": 1.0, "ETH": -1.0}),
        "source": "FORWARD_L2_ATTESTED_DECISION",
    }


def _bundle(pending: dict) -> dict:
    return {
        "schema_version": 1,
        "provenance": BUNDLE_PROVENANCE,
        "alphas": {
            "crowd_contra": {
                "binding": {
                    "alpha_id": "crowd_contra",
                    "decision_ts": DECISION_TS,
                    "decision_payload_sha256": forward_decision_payload_sha256(pending),
                    "dataset_version": "pit-v1",
                    "config_hash": "c" * 64,
                },
                "inputs": {
                    "capacity_cny": {"BTC": 10_000_000.0, "ETH": 8_000_000.0},
                    "daily_volume_cny": 100_000_000.0,
                    "total_aum": 1_000_000.0,
                    "alpha_returns": {
                        "carry_oi_majors": [0.01, -0.01],
                        "crowd_contra": [0.02, 0.01],
                    },
                    "regime_labels": ["normal", "stress"],
                    "timestamps": [DECISION_TS - 7 * 86_400_000, DECISION_TS],
                    "alpha_metadata": [
                        {
                            "age_days": 120.0,
                            "validation_confidence": 0.8,
                            "paper_confidence": 0.5,
                            "decay_flag": False,
                            "overfit_flag": False,
                            "leakage_flag": False,
                        }
                    ],
                },
            }
        },
    }


def test_missing_bundle_is_pending_and_never_constructs_partial_inputs(tmp_path):
    result = load_research_risk_bundle(
        str(tmp_path / "missing.json"),
        alpha_id="crowd_contra",
        pending=_pending(),
    )

    assert result.inputs is None
    assert result.status["status"] == "PENDING"
    assert result.status["missing"] == ["bundle_file"]


def test_bundle_requires_decision_digest_and_full_metadata(tmp_path):
    pending = _pending()
    document = _bundle(pending)
    document["alphas"]["crowd_contra"]["binding"]["decision_payload_sha256"] = "0" * 64
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = load_research_risk_bundle(
        str(path),
        alpha_id="crowd_contra",
        pending=pending,
    )

    assert result.inputs is None
    assert result.status["status"] == "BLOCKED"
    assert "decision_binding_digest_mismatch" in result.status["reasons"]


def test_valid_bundle_returns_only_explicit_inputs(tmp_path):
    pending = _pending()
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(_bundle(pending)), encoding="utf-8")

    result = load_research_risk_bundle(
        str(path),
        alpha_id="crowd_contra",
        pending=pending,
        expected_dataset_version="pit-v1",
        expected_config_hash="c" * 64,
    )

    assert result.status["status"] == "READY"
    assert result.inputs is not None
    assert result.inputs.capacity_cny["BTC"] == 10_000_000.0
    assert tuple(result.inputs.alpha_returns["crowd_contra"]) == (0.02, 0.01)
    assert tuple(result.inputs.timestamps or ()) == (
        DECISION_TS - 7 * 86_400_000,
        DECISION_TS,
    )


@pytest.mark.parametrize('field,value', [
    ('age_days', -1), ('validation_confidence', -0.01), ('validation_confidence', 1.01),
    ('paper_confidence', -0.01), ('paper_confidence', 1.01),
])
def test_unknown_model_ranges_cannot_be_loaded_as_ready(tmp_path, field, value):
    pending = _pending()
    document = _bundle(pending)
    document['alphas']['crowd_contra']['inputs']['alpha_metadata'][0][field] = value
    path = tmp_path/'bundle.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    result = load_research_risk_bundle(str(path), alpha_id='crowd_contra', pending=pending)
    assert result.inputs is None
    assert result.status['status'] == 'PENDING'
    assert 'alpha_metadata_range_invalid:0' in result.status['reasons']


def test_missing_required_metadata_is_pending_not_defaulted(tmp_path):
    pending = _pending()
    document = _bundle(pending)
    del document["alphas"]["crowd_contra"]["inputs"]["alpha_metadata"][0]["paper_confidence"]
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = load_research_risk_bundle(
        str(path),
        alpha_id="crowd_contra",
        pending=pending,
    )

    assert result.inputs is None
    assert result.status["status"] == "PENDING"
    assert any(reason.startswith("alpha_metadata_missing") for reason in result.status["reasons"])


def test_bundle_cannot_omit_a_decision_instrument_capacity(tmp_path):
    pending = _pending()
    document = _bundle(pending)
    del document["alphas"]["crowd_contra"]["inputs"]["capacity_cny"]["ETH"]
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = load_research_risk_bundle(
        str(path), alpha_id="crowd_contra", pending=pending
    )

    assert result.inputs is None
    assert result.status["status"] == "PENDING"
    assert "capacity_cny_missing:ETH" in result.status["reasons"]


def test_bundle_requires_current_alpha_return_and_hex_config_hash(tmp_path):
    pending = _pending()
    document = _bundle(pending)
    inputs = document["alphas"]["crowd_contra"]["inputs"]
    del inputs["alpha_returns"]["crowd_contra"]
    document["alphas"]["crowd_contra"]["binding"]["config_hash"] = "not-a-hash"
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = load_research_risk_bundle(
        str(path), alpha_id="crowd_contra", pending=pending
    )

    assert result.inputs is None
    assert result.status["status"] == "BLOCKED"
    assert "risk_config_hash_invalid" in result.status["reasons"]


def test_bundle_rejects_unordered_or_unaligned_timestamps(tmp_path):
    pending = _pending()
    document = _bundle(pending)
    inputs = document["alphas"]["crowd_contra"]["inputs"]
    inputs["timestamps"] = [DECISION_TS, DECISION_TS - 7 * 86_400_000]
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = load_research_risk_bundle(
        str(path), alpha_id="crowd_contra", pending=pending
    )

    assert result.inputs is None
    assert "timestamps_invalid" in result.status["reasons"]
