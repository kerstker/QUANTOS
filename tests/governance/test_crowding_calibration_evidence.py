import hashlib
import json
import shutil
from pathlib import Path

import pytest

import tools.crowding_metric_research as crowding_research
from tools.crowding_metric_research import (
    CrowdingCalibrationEvidenceError,
    calibration_sources_available,
    check,
    generate,
    verify_append_only_source_replay,
    verify_calibration_bundle,
    write_calibration_bundle,
)

_ROOT = Path(__file__).resolve().parents[2]


def _records():
    return [
        {
            "period": 1,
            "weights": {"A": 0.25, "B": 0.25, "C": -0.25, "D": -0.25},
            "dry_crowding": {"A": 0.0, "B": 0.3, "C": 0.7, "D": 1.0},
            "live_crowding": {"A": 0.1, "B": 0.2, "C": 0.8, "D": 0.9},
            "z_by_symbol": {"C": 2.0, "D": 3.2},
            "short_raw_gls": 3.0,
            "short_mean_z": 2.6,
            "short_max_z": 3.2,
        }
    ]


def _fixture_repo(tmp_path: Path, *, present_sources: int = 0) -> tuple[Path, Path]:
    for reference in crowding_research._IMPLEMENTATION_REFS:
        target = tmp_path / reference
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_ROOT / reference, target)

    source_payloads = {
        reference: f"retained:{reference}".encode("utf-8")
        for reference in crowding_research._SOURCE_REFS
    }
    source_hashes = {
        reference: hashlib.sha256(payload).hexdigest()
        for reference, payload in source_payloads.items()
    }
    for reference in crowding_research._SOURCE_REFS[:present_sources]:
        path = tmp_path / reference
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source_payloads[reference])

    bundle = tmp_path / crowding_research._BUNDLE
    manifest = tmp_path / crowding_research._MANIFEST
    write_calibration_bundle(
        _records(),
        bundle,
        manifest,
        source_hashes=source_hashes,
        repo_root=tmp_path,
        instruments=("A", "B", "C", "D"),
    )
    return bundle, manifest


def test_calibration_bundle_is_deterministic_and_reconstructable(tmp_path):
    bundle = tmp_path / "crowding.jsonl.gz"
    manifest = tmp_path / "crowding.manifest.json"
    source_hashes = {"data/cache/source.json": "a" * 64}

    first = write_calibration_bundle(
        _records(), bundle, manifest, source_hashes=source_hashes, repo_root=tmp_path
    )
    first_bytes = bundle.read_bytes()
    second = write_calibration_bundle(
        _records(), bundle, manifest, source_hashes=source_hashes, repo_root=tmp_path
    )

    assert first == second
    assert first_bytes == bundle.read_bytes()
    assert verify_calibration_bundle(bundle, manifest, repo_root=tmp_path) == _records()


def test_calibration_bundle_rejects_manifest_tampering(tmp_path):
    bundle = tmp_path / "crowding.jsonl.gz"
    manifest = tmp_path / "crowding.manifest.json"
    write_calibration_bundle(
        _records(),
        bundle,
        manifest,
        source_hashes={"data/cache/source.json": "a" * 64},
        repo_root=tmp_path,
    )
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["row_count"] = 2
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CrowdingCalibrationEvidenceError):
        verify_calibration_bundle(bundle, manifest, repo_root=tmp_path)


def test_calibration_bundle_rejects_bundle_tampering(tmp_path):
    bundle = tmp_path / "crowding.jsonl.gz"
    manifest = tmp_path / "crowding.manifest.json"
    write_calibration_bundle(
        _records(),
        bundle,
        manifest,
        source_hashes={"data/cache/source.json": "a" * 64},
        repo_root=tmp_path,
    )
    content = bytearray(bundle.read_bytes())
    content[-1] ^= 1
    bundle.write_bytes(content)

    with pytest.raises(CrowdingCalibrationEvidenceError):
        verify_calibration_bundle(bundle, manifest, repo_root=tmp_path)


def test_default_generate_is_invalidation_only_and_preserves_retained_bytes(
    tmp_path, monkeypatch
):
    bundle, manifest = _fixture_repo(tmp_path)
    bundle_before = bundle.read_bytes()
    manifest_before = manifest.read_bytes()

    def _legacy_replay_must_not_run():
        pytest.fail("default invalidation refresh invoked the legacy source replay")

    monkeypatch.setattr(
        crowding_research,
        "build_calibration_records",
        _legacy_replay_must_not_run,
    )
    evidence = generate(tmp_path, write_snapshot=False)

    assert bundle.read_bytes() == bundle_before
    assert manifest.read_bytes() == manifest_before
    assert not (tmp_path / crowding_research._CACHE_SNAPSHOT).exists()
    assert evidence["schema_version"] == 2
    assert evidence["document_type"] == "CROWDING_CALIBRATION_INVALIDATION"
    assert evidence["status"] == "INVALIDATED_PERP_LEDGER_V1"
    assert evidence["invalidation_code"] == "INVALIDATED_PERP_LEDGER_V1"
    assert evidence["readiness"] == "NOT_READY"
    assert evidence["calibration_authoritative"] is False
    assert evidence["counted_as_calibration_evidence"] is False
    assert evidence["counted_as_completion_evidence"] is False
    assert evidence["load_bearing"] is False
    assert evidence["policy"]["threshold_status"] == (
        "UNCALIBRATED_CONSERVATIVE_SAFETY_LIMIT"
    )
    assert evidence["summary"]["status"] == "INVALIDATED_PERP_LEDGER_V1"
    assert evidence["summary"]["calibration_authoritative"] is False
    assert evidence["summary"]["provenance"] == (
        "RETAINED_V1_DIAGNOSTIC_INVALIDATED"
    )
    assert evidence["future_authority"] == {
        "required_bundle_version": "CROWDING_GATE_CALIBRATION_V2",
        "requires_independent_pit_bundle": True,
        "requires_explicit_reviewed_entrypoint": True,
        "requires_independently_controlled_signed_source_result_authority": True,
        "self_generated_hashes_can_grant_authority": False,
        "automatic_upgrade_from_v1": False,
        "current_entrypoint_can_grant_authority": False,
    }
    assert evidence["implementation_binding_role"] == (
        "DIAGNOSTIC_RECONSTRUCTION_ONLY"
    )
    assert check(tmp_path) == evidence
    assert bundle.read_bytes() == bundle_before
    assert manifest.read_bytes() == manifest_before


def test_optional_snapshot_nests_retained_diagnostics(tmp_path):
    bundle, manifest = _fixture_repo(tmp_path)
    bundle_before = bundle.read_bytes()
    manifest_before = manifest.read_bytes()

    generate(tmp_path)

    snapshot = json.loads(
        (tmp_path / crowding_research._CACHE_SNAPSHOT).read_text(encoding="utf-8")
    )
    assert snapshot["schema"] == "crowding_metric.invalidated.v1"
    assert snapshot["status"] == "INVALIDATED_PERP_LEDGER_V1"
    assert snapshot["readiness"] == "NOT_READY"
    assert snapshot["calibration_authoritative"] is False
    assert snapshot["counted_as_calibration_evidence"] is False
    assert snapshot["retained_diagnostic_summary"]["status"] == (
        "INVALIDATED_PERP_LEDGER_V1"
    )
    assert "gate_at_signed_threshold" not in snapshot
    assert bundle.read_bytes() == bundle_before
    assert manifest.read_bytes() == manifest_before


def test_default_generate_and_check_reject_partial_source_set(tmp_path):
    bundle, manifest = _fixture_repo(tmp_path, present_sources=1)
    bundle_before = bundle.read_bytes()
    manifest_before = manifest.read_bytes()

    with pytest.raises(
        CrowdingCalibrationEvidenceError,
        match="partial calibration source set",
    ):
        generate(tmp_path, write_snapshot=False)
    with pytest.raises(
        CrowdingCalibrationEvidenceError,
        match="partial calibration source set",
    ):
        check(tmp_path)

    assert bundle.read_bytes() == bundle_before
    assert manifest.read_bytes() == manifest_before
    assert not (tmp_path / crowding_research._EVIDENCE).exists()


def test_default_generate_rejects_drift_when_all_sources_are_present(tmp_path):
    _fixture_repo(tmp_path, present_sources=len(crowding_research._SOURCE_REFS))
    drifted = tmp_path / crowding_research._SOURCE_REFS[-1]
    drifted.write_bytes(b"not-the-retained-source")

    with pytest.raises(
        CrowdingCalibrationEvidenceError,
        match="calibration source drift",
    ):
        generate(tmp_path, write_snapshot=False)


def test_default_generate_accepts_all_matching_retained_sources(tmp_path):
    bundle, manifest = _fixture_repo(
        tmp_path, present_sources=len(crowding_research._SOURCE_REFS)
    )
    bundle_before = bundle.read_bytes()
    manifest_before = manifest.read_bytes()

    evidence = generate(tmp_path, write_snapshot=False)

    assert evidence["status"] == "INVALIDATED_PERP_LEDGER_V1"
    assert evidence["calibration_authoritative"] is False
    assert bundle.read_bytes() == bundle_before
    assert manifest.read_bytes() == manifest_before


def test_source_replay_allows_strictly_later_append():
    frozen = _records()
    later = {
        **_records()[0],
        "period": 2,
        "short_raw_gls": 4.0,
    }

    verify_append_only_source_replay(frozen, [*frozen, later])


def test_source_replay_rejects_historical_mutation():
    frozen = _records()
    mutated = [{**frozen[0], "short_raw_gls": 99.0}]

    with pytest.raises(
        CrowdingCalibrationEvidenceError,
        match="historical prefix drifted",
    ):
        verify_append_only_source_replay(frozen, mutated)


def test_source_replay_requires_every_declared_source(tmp_path):
    assert calibration_sources_available(tmp_path) is False

    first = tmp_path / crowding_research._SOURCE_REFS[0]
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"partial")
    with pytest.raises(
        CrowdingCalibrationEvidenceError,
        match="partial calibration source set",
    ):
        calibration_sources_available(tmp_path)


def test_repository_crowding_calibration_evidence_is_current():
    evidence = check(_ROOT)
    assert evidence["status"] == "INVALIDATED_PERP_LEDGER_V1"
    assert evidence["invalidation_code"] == "INVALIDATED_PERP_LEDGER_V1"
    assert evidence["readiness"] == "NOT_READY"
    assert evidence["calibration_authoritative"] is False
    assert evidence["counted_as_calibration_evidence"] is False
    assert evidence["counted_as_completion_evidence"] is False
    assert evidence["policy"]["threshold_status"] == (
        "UNCALIBRATED_CONSERVATIVE_SAFETY_LIMIT"
    )
    assert evidence["dataset"]["retention_status"] == (
        "INVALIDATED_PERP_LEDGER_V1"
    )
    assert evidence["summary"]["status"] == "INVALIDATED_PERP_LEDGER_V1"
    assert evidence["summary"]["calibration_authoritative"] is False
    assert evidence["future_authority"]["automatic_upgrade_from_v1"] is False
    assert evidence["future_authority"][
        "requires_independently_controlled_signed_source_result_authority"
    ] is True
    assert evidence["future_authority"][
        "self_generated_hashes_can_grant_authority"
    ] is False
    assert evidence["summary"]["n_periods"] >= 200
    assert evidence["summary"]["gate_at_signed_threshold"]["block_periods_equal"] is True


def test_unified_gate_checks_crowding_calibration_evidence():
    source = (_ROOT / "scripts" / "validate.py").read_text(encoding="utf-8")
    assert '"crowding-calibration-evidence:current"' in source
    assert '"tools.crowding_metric_research"' in source
