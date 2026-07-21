from __future__ import annotations

import json

import pytest

from services.features.registry_store import SqliteFeatureRegistryStore
from services.features.value_store import SqliteFeatureValueStore
from services.research.alpha_expression import FieldPanel
from tools.feature_cache_research import (
    FEATURE_VERSION,
    PROVENANCE,
    FeatureCacheEvidenceError,
    build_cache_evidence,
    build_default_evidence,
    verify_cache_evidence,
)


def _panel(n: int = 100) -> FieldPanel:
    instruments = ("A", "B", "C", "D")
    event_times = tuple(range(n))
    close = [
        [100.0 + 0.2 * t + 0.5 * i + (0.01 * t * i) for i in range(len(instruments))]
        for t in event_times
    ]
    return FieldPanel(instruments, event_times, {"close": close})


def _build(panel: FieldPanel):
    manifest = {"bundle_ref": "fixture.pit.jsonl.gz", "bundle_sha256": "b" * 64, "manifest_sha256": "m" * 64}
    registry = SqliteFeatureRegistryStore(":memory:", create=True)
    values = SqliteFeatureValueStore(":memory:", create=True)
    return manifest, registry, values


def test_cache_replay_is_deterministic_and_pit_explicit():
    manifest, registry, values = _build(_panel())
    first = build_cache_evidence(_panel(), manifest, registry, values)

    manifest2, registry2, values2 = _build(_panel())
    second = build_cache_evidence(_panel(), manifest2, registry2, values2)

    assert first == second
    assert first["provenance"] == PROVENANCE
    assert first["load_bearing"] is False
    assert first["cache"]["feature_version"] == FEATURE_VERSION
    assert first["cache"]["readback_verified"] is True
    assert first["cache"]["total_rows"] > 0
    assert all(item["leakage_passed"] is True for item in first["features"].values())


def test_default_replay_uses_retained_fx_bundle():
    evidence = build_default_evidence()
    assert evidence["source"]["bundle_ref"] == "evidence/datasets/fx_pit_tradable_v1.jsonl.gz"
    assert evidence["cache"]["instrument_count"] == 18
    assert evidence["cache"]["event_time_count"] > 6_000
    assert evidence["cache"]["feature_count"] == 3
    assert evidence["cache"]["readback_verified"] is True


def test_cache_evidence_tamper_is_rejected(tmp_path):
    target = tmp_path / "cache.json"
    document = build_default_evidence()
    target.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    assert verify_cache_evidence(target) == document

    document["cache"]["total_rows"] += 1
    target.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    with pytest.raises(FeatureCacheEvidenceError, match="does not match"):
        verify_cache_evidence(target)
