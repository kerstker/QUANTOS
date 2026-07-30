import json
from pathlib import Path

import pytest

from tools.crowding_metric_research import (
    CrowdingCalibrationEvidenceError,
    calibration_sources_available,
    check,
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


def test_repository_crowding_calibration_evidence_is_current():
    evidence = check(_ROOT)
    assert evidence["status"] == "SENSITIVITY_EVIDENCE_COMPLETE_SIGNED_Z_GATE"
    assert evidence["summary"]["n_periods"] >= 200
    assert evidence["summary"]["gate_at_signed_threshold"]["block_periods_equal"] is True


def test_unified_gate_checks_crowding_calibration_evidence():
    source = (_ROOT / "scripts" / "validate.py").read_text(encoding="utf-8")
    assert '"crowding-calibration-evidence:current"' in source
    assert '"tools.crowding_metric_research"' in source
