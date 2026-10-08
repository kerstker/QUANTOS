from __future__ import annotations

import json

import pytest

from services.research.alpha_expression import FieldPanel
from tools.feature_distribution_drift_research import (
    PROVENANCE,
    FeatureDriftEvidenceError,
    build_default_evidence,
    build_feature_drift_evidence,
    verify_feature_drift_evidence,
)


def _panel(n: int = 100) -> FieldPanel:
    instruments = ("A", "B", "C", "D")
    event_times = tuple(range(n))
    close = [
        [100.0 + 0.2 * t + 0.5 * i + (0.01 * t * i) for i in range(len(instruments))]
        for t in event_times
    ]
    return FieldPanel(instruments, event_times, {"close": close})


def test_feature_drift_replay_is_deterministic_and_pit_explicit():
    manifest = {
        "bundle_ref": "fixture.pit.jsonl.gz",
        "bundle_sha256": "b" * 64,
    }
    first = build_feature_drift_evidence(_panel(), manifest, manifest_sha256="m" * 64)
    second = build_feature_drift_evidence(_panel(), manifest, manifest_sha256="m" * 64)

    assert first == second
    assert first["provenance"] == PROVENANCE
    assert first["load_bearing"] is False
    assert first["source"]["knowledge_rule"] == "knowledge_time <= event_time"
    assert set(first["features"]) == {
        "fx_momentum_20",
        "fx_volatility_20",
        "fx_distance_from_high_20",
    }
    assert all(item["observations"] >= 79 for item in first["features"].values())


def test_default_replay_is_based_on_retained_fx_bundle():
    evidence = build_default_evidence()

    assert evidence["source"]["bundle_ref"] == "evidence/datasets/fx_pit_tradable_v1.jsonl.gz"
    assert evidence["panel"]["instrument_count"] == 18
    assert evidence["panel"]["event_time_count"] > 6_000
    assert evidence["provenance"] == PROVENANCE
    assert evidence["assessment"]["provenance"] == "PIT_FEATURE_TRAILING_KS_DRIFT_V1"
    assert evidence["assessment"]["load_bearing"] is False
    assert all(item["observations"] >= 80 for item in evidence["features"].values())


def test_verify_rejects_tampered_evidence(tmp_path):
    target = tmp_path / "feature-drift.json"
    document = build_default_evidence()
    target.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    assert verify_feature_drift_evidence(target) == document

    document["assessment"]["status"] = "PASS" if document["assessment"]["status"] != "PASS" else "BLOCK"
    target.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    with pytest.raises(FeatureDriftEvidenceError, match="does not match"):
        verify_feature_drift_evidence(target)
