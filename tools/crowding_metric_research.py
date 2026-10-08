"""Verify retained crowding-gate V1 inputs and publish their invalidation.

The retained V1 bundle is useful diagnostic history, but it is not calibration
authority: its source timing cannot satisfy the current PIT contract and its
legacy perpetual-ledger semantics are invalidated.  The default ``generate``
and ``check`` paths therefore only verify the immutable retained bundle and
produce deterministic *invalidation* evidence.  They never rebuild or replace
the V1 bundle/manifest, rebuild records from source, or invoke legacy profit
execution.
"""
from __future__ import annotations

import ast
import gzip
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from live.execution.crowding_score import compute_crowding_scores, crowding_zscore
from quantos.contracts.hashing import source_file_sha256
from services.execution.backtest_authority import LEGACY_INVALIDATION_CODE
from services.portfolio.crowding_calibration import (
    SIGNED_MAX_CROWDING_Z,
    summarize_calibration_records,
)
from services.research import alpha_expression as ax
from tools.crypto_oi_research import (
    QUANTILE,
    _DB,
    _FUND,
    _OI,
    _SNAP,
    _legs,
    build_panel,
)
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.dry_run_crowd_contra_multi import _WEEK_MS, _crowding_rank

_ROOT = Path(__file__).resolve().parents[1]
_CACHE_SNAPSHOT = Path("data/cache/crowding_metric_snapshot.json")
_BUNDLE = Path("evidence/datasets/crowding_gate_calibration_v1.jsonl.gz")
_MANIFEST = Path("evidence/datasets/crowding_gate_calibration_v1.manifest.json")
_EVIDENCE = Path("evidence/crowding_gate_calibration.json")
_SOURCE_REFS = tuple(Path(value).as_posix() for value in (_DB, _SNAP, _FUND, _OI))
_V1_BUNDLE_VERSION = "CROWDING_GATE_CALIBRATION_V1"
_THRESHOLD_STATUS = "UNCALIBRATED_CONSERVATIVE_SAFETY_LIMIT"
_FUTURE_AUTHORITY_VERSION = "CROWDING_GATE_CALIBRATION_V2"
_IMPLEMENTATION_REFS = (
    "services/portfolio/crowding_gate.py",
    "services/portfolio/crowding_calibration.py",
    "live/execution/crowding_score.py",
    "tools/dry_run_crowd_contra_multi.py",
    "tools/dry_run_crowd_contra_signal_adapter_multi.py",
    "tools/live_track_advance.py",
    "tools/crowding_metric_research.py",
)
_SIGNED_ASSIGNMENTS = {
    "dry_run": ("tools/dry_run_crowd_contra_multi.py", "_MAX_CROWDING_Z"),
    "signal_adapter_dry_run": (
        "tools/dry_run_crowd_contra_signal_adapter_multi.py",
        "_MAX_CROWDING_Z",
    ),
    "live": ("tools/live_track_advance.py", "_MAX_CROWDING_Z"),
}


class CrowdingCalibrationEvidenceError(ValueError):
    pass


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_ref(path: Path, root: Path) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def calibration_sources_available(repo_root: Path) -> bool:
    present = [
        reference
        for reference in _SOURCE_REFS
        if (repo_root / reference).is_file()
    ]
    if present and len(present) != len(_SOURCE_REFS):
        raise CrowdingCalibrationEvidenceError(
            "partial calibration source set is not permitted"
        )
    return len(present) == len(_SOURCE_REFS)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _trailing_means(gmat, row: int, indices, lookback: int = 4) -> dict[int, float]:
    result = {}
    for index in indices:
        window = [
            gmat[r][index]
            for r in range(max(0, row - lookback + 1), row + 1)
            if gmat[r][index] is not None
        ]
        if window:
            result[index] = sum(window) / len(window)
    return result


def _weekly_rows(event_times: Sequence[int]) -> list[int]:
    rows: list[int] = []
    last = None
    for row, event_time in enumerate(event_times):
        if last is None or event_time - last >= _WEEK_MS:
            rows.append(row)
            last = event_time
    return rows


def build_calibration_records() -> tuple[list[dict], list[str]]:
    """Build frozen gate inputs from the current PIT panel without network access."""
    panel, instruments = build_panel()
    names = panel.instruments
    global_ls = panel.matrices["global_ls"]
    signal = ax.evaluate(CROWD_CONTRA, panel)
    records = []

    for row in _weekly_rows(panel.event_times):
        legs = _legs(signal[row], QUANTILE)
        if not legs:
            continue
        dry_index_scores = _crowding_rank(global_ls, row, list(legs))
        if not dry_index_scores:
            continue
        weights = {names[index]: float(weight) for index, weight in legs.items()}
        dry_crowding = {
            names[index]: float(score) for index, score in dry_index_scores.items()
        }
        current_global_ls = {
            names[index]: global_ls[row][index]
            for index in range(len(names))
            if global_ls[row][index] is not None
        }
        live_crowding = compute_crowding_scores(current_global_ls)
        z_by_symbol = {
            names[index]: float(z)
            for index in legs
            if (z := crowding_zscore(global_ls, row, index)) is not None
        }
        short_indices = [index for index, weight in legs.items() if weight < 0.0]
        trailing = _trailing_means(global_ls, row, legs)
        short_raw = [trailing[index] for index in short_indices if index in trailing]
        short_z = [z_by_symbol[names[index]] for index in short_indices if names[index] in z_by_symbol]
        records.append(
            {
                "period": int(panel.event_times[row]),
                "weights": dict(sorted(weights.items())),
                "dry_crowding": dict(sorted(dry_crowding.items())),
                "live_crowding": dict(sorted(live_crowding.items())),
                "z_by_symbol": dict(sorted(z_by_symbol.items())),
                "short_raw_gls": sum(short_raw) / len(short_raw) if short_raw else None,
                "short_mean_z": sum(short_z) / len(short_z) if short_z else None,
                "short_max_z": max(short_z) if short_z else None,
            }
        )
    return records, list(instruments)


def _deterministic_gzip(content: bytes) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as stream:
        stream.write(content)
    return output.getvalue()


def write_calibration_bundle(
    records: Sequence[Mapping[str, object]],
    bundle_path: Path,
    manifest_path: Path,
    *,
    source_hashes: Mapping[str, str],
    repo_root: Path,
    instruments: Sequence[str] = (),
) -> dict:
    ordered = sorted((dict(record) for record in records), key=lambda row: row["period"])
    if not ordered:
        raise CrowdingCalibrationEvidenceError("calibration bundle cannot be empty")
    periods = [row.get("period") for row in ordered]
    if any(not isinstance(period, int) or isinstance(period, bool) for period in periods):
        raise CrowdingCalibrationEvidenceError("bundle periods must be integers")
    if periods != sorted(set(periods)):
        raise CrowdingCalibrationEvidenceError("bundle periods must be unique and increasing")
    content = b"".join(_canonical_bytes(row) + b"\n" for row in ordered)
    compressed = _deterministic_gzip(content)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_bytes(compressed)

    manifest = {
        "schema_version": 1,
        "bundle_ref": _repo_ref(bundle_path, repo_root),
        "bundle_sha256": _sha256_bytes(compressed),
        "row_count": len(ordered),
        "min_period": periods[0],
        "max_period": periods[-1],
        "instruments": sorted(set(instruments)),
        "source_sha256": dict(sorted(source_hashes.items())),
        "record_schema": (
            "period,weights,dry_crowding,live_crowding,z_by_symbol,"
            "short_raw_gls,short_mean_z,short_max_z"
        ),
    }
    manifest["manifest_sha256"] = _sha256_bytes(_canonical_bytes(manifest))
    _write_json(manifest_path, manifest)
    return manifest


def _verify_declared_source_set(
    source_hashes: Mapping[str, object], *, repo_root: Path
) -> None:
    """Verify all declared sources or none; a partial local set is ambiguous."""
    paths: dict[str, Path] = {}
    for reference, expected_hash in source_hashes.items():
        if (
            not isinstance(reference, str)
            or not reference
            or Path(reference).is_absolute()
            or ".." in Path(reference).parts
        ):
            raise CrowdingCalibrationEvidenceError(
                "invalid calibration source reference"
            )
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or expected_hash.lower() != expected_hash
            or any(character not in "0123456789abcdef" for character in expected_hash)
        ):
            raise CrowdingCalibrationEvidenceError("invalid calibration source hash")
        paths[reference] = repo_root / reference

    present = [reference for reference, path in paths.items() if path.is_file()]
    if present and len(present) != len(paths):
        raise CrowdingCalibrationEvidenceError(
            "partial calibration source set is not permitted"
        )
    if not present:
        return
    for reference, path in paths.items():
        if _sha256_file(path) != source_hashes[reference]:
            raise CrowdingCalibrationEvidenceError(
                f"calibration source drift: {reference}"
            )


def verify_calibration_bundle(
    bundle_path: Path,
    manifest_path: Path,
    *,
    repo_root: Path,
    check_sources: bool = False,
) -> list[dict]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrowdingCalibrationEvidenceError("calibration manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise CrowdingCalibrationEvidenceError("unsupported calibration manifest")
    without_self = dict(manifest)
    manifest_hash = without_self.pop("manifest_sha256", None)
    if manifest_hash != _sha256_bytes(_canonical_bytes(without_self)):
        raise CrowdingCalibrationEvidenceError("calibration manifest hash mismatch")
    if manifest.get("bundle_ref") != _repo_ref(bundle_path, repo_root):
        raise CrowdingCalibrationEvidenceError("calibration bundle reference mismatch")
    try:
        compressed = bundle_path.read_bytes()
        if manifest.get("bundle_sha256") != _sha256_bytes(compressed):
            raise CrowdingCalibrationEvidenceError("calibration bundle hash mismatch")
        with gzip.open(bundle_path, "rt", encoding="utf-8") as stream:
            records = [json.loads(line) for line in stream if line.strip()]
    except (OSError, EOFError, json.JSONDecodeError) as exc:
        raise CrowdingCalibrationEvidenceError("calibration bundle is unreadable") from exc

    periods = [record.get("period") for record in records if isinstance(record, dict)]
    if len(records) != manifest.get("row_count") or len(periods) != len(records):
        raise CrowdingCalibrationEvidenceError("calibration row count mismatch")
    if not periods or periods != sorted(set(periods)):
        raise CrowdingCalibrationEvidenceError("calibration periods are invalid")
    if periods[0] != manifest.get("min_period") or periods[-1] != manifest.get("max_period"):
        raise CrowdingCalibrationEvidenceError("calibration period bounds mismatch")

    source_hashes = manifest.get("source_sha256")
    if not isinstance(source_hashes, dict) or not source_hashes:
        raise CrowdingCalibrationEvidenceError("calibration source hashes are missing")
    for reference, expected_hash in source_hashes.items():
        if (
            not isinstance(reference, str)
            or not reference
            or Path(reference).is_absolute()
            or ".." in Path(reference).parts
            or not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or expected_hash.lower() != expected_hash
            or any(character not in "0123456789abcdef" for character in expected_hash)
        ):
            raise CrowdingCalibrationEvidenceError("invalid calibration source hash")
    if check_sources:
        _verify_declared_source_set(source_hashes, repo_root=repo_root)
    summarize_calibration_records(records)
    return records


def verify_append_only_source_replay(
    frozen_records: Sequence[Mapping[str, object]],
    current_records: Sequence[Mapping[str, object]],
) -> None:
    """Compare a legacy diagnostic prefix; never used by generate/check.

    This technical helper preserves an old fixture invariant while allowing
    later observations.  Passing it does not make V1 PIT-valid, calibrated, or
    authoritative.
    """
    if not frozen_records:
        raise CrowdingCalibrationEvidenceError("frozen calibration replay is empty")
    cutoff = frozen_records[-1].get("period")
    if not isinstance(cutoff, int) or isinstance(cutoff, bool):
        raise CrowdingCalibrationEvidenceError("frozen calibration cutoff is invalid")
    current_prefix = [
        dict(record)
        for record in current_records
        if isinstance(record.get("period"), int) and record["period"] <= cutoff
    ]
    expected = [dict(record) for record in frozen_records]
    if current_prefix != expected:
        raise CrowdingCalibrationEvidenceError(
            "calibration source historical prefix drifted"
        )


def _numeric_assignment(path: Path, name: str) -> float:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
            return float(node.value.value)
    raise CrowdingCalibrationEvidenceError(f"missing numeric assignment {name} in {path}")


def _implementation_hashes(root: Path) -> dict[str, str]:
    return {
        reference: source_file_sha256(root / reference)
        for reference in _IMPLEMENTATION_REFS
    }


def _signed_assignments(root: Path) -> dict[str, float]:
    return {
        label: _numeric_assignment(root / reference, name)
        for label, (reference, name) in _SIGNED_ASSIGNMENTS.items()
    }


def build_invalidated_calibration_evidence(
    records: Sequence[Mapping[str, object]], manifest: Mapping[str, object], *, repo_root: Path
) -> dict:
    """Describe V1 as retained diagnostics without granting calibration authority."""
    if manifest.get("schema_version") != 1:
        raise CrowdingCalibrationEvidenceError("unsupported retained bundle version")
    source_hashes = manifest.get("source_sha256")
    if not isinstance(source_hashes, dict) or set(source_hashes) != set(_SOURCE_REFS):
        raise CrowdingCalibrationEvidenceError(
            "retained V1 source identity does not match the reviewed source set"
        )
    assignments = _signed_assignments(repo_root)
    if any(value != SIGNED_MAX_CROWDING_Z for value in assignments.values()):
        raise CrowdingCalibrationEvidenceError(
            "conservative z safety limit drifted across entrypoints"
        )
    summary = summarize_calibration_records(records)
    gate = summary["gate_at_signed_threshold"]
    if not gate["block_periods_equal"]:
        raise CrowdingCalibrationEvidenceError(
            "retained diagnostic live and dry-run block periods diverged"
        )
    sensitivity_counts = [
        item["n_block"] for item in summary["z_threshold_sensitivity"].values()
    ]
    if sensitivity_counts != sorted(sensitivity_counts, reverse=True):
        raise CrowdingCalibrationEvidenceError(
            "retained diagnostic z sensitivity is not monotonic"
        )
    diagnostic_summary = {
        **summary,
        "status": LEGACY_INVALIDATION_CODE,
        "invalidation_code": LEGACY_INVALIDATION_CODE,
        "calibration_authoritative": False,
        "provenance": "RETAINED_V1_DIAGNOSTIC_INVALIDATED",
    }

    evidence = {
        "schema_version": 2,
        "document_type": "CROWDING_CALIBRATION_INVALIDATION",
        "status": LEGACY_INVALIDATION_CODE,
        "invalidation_code": LEGACY_INVALIDATION_CODE,
        "readiness": "NOT_READY",
        "calibration_authoritative": False,
        "load_bearing": False,
        "counted_as_calibration_evidence": False,
        "counted_as_completion_evidence": False,
        "policy": {
            "signed_max_crowding_z": SIGNED_MAX_CROWDING_Z,
            "threshold_status": _THRESHOLD_STATUS,
            "calibration_authoritative": False,
            "legacy_recorded_on": "2026-07-07",
            "entrypoint_assignments": assignments,
            "average_rank_path": "WARN_ONLY",
        },
        "dataset": {
            "bundle_version": _V1_BUNDLE_VERSION,
            "retention_status": LEGACY_INVALIDATION_CODE,
            "purpose": "DIAGNOSTIC_ONLY",
            "calibration_authoritative": False,
            "bundle_ref": manifest["bundle_ref"],
            "bundle_sha256": manifest["bundle_sha256"],
            "manifest_sha256": manifest["manifest_sha256"],
            "row_count": manifest["row_count"],
            "min_period": manifest["min_period"],
            "max_period": manifest["max_period"],
            "instruments": manifest.get("instruments", []),
            "source_sha256": source_hashes,
            "source_verification_policy": (
                "ALL_DECLARED_FILES_SHA256_MATCH_OR_ALL_ABSENT;PARTIAL_REJECTED"
            ),
        },
        "rank_semantics": {
            "dry_run": "TRAILING_GLOBAL_LS_CROSS_SECTIONAL_RANK",
            "live": "CURRENT_ABS_GLOBAL_LS_MINUS_ONE_CROSS_SECTIONAL_RANK",
            "legacy_block_rule": "SHORT_LEG_OWN_HISTORY_GLOBAL_LS_ZSCORE",
            "authority_status": LEGACY_INVALIDATION_CODE,
        },
        "summary": diagnostic_summary,
        "future_authority": {
            "required_bundle_version": _FUTURE_AUTHORITY_VERSION,
            "requires_independent_pit_bundle": True,
            "requires_explicit_reviewed_entrypoint": True,
            "requires_independently_controlled_signed_source_result_authority": True,
            "self_generated_hashes_can_grant_authority": False,
            "automatic_upgrade_from_v1": False,
            "current_entrypoint_can_grant_authority": False,
        },
        "implementation_binding_role": "DIAGNOSTIC_RECONSTRUCTION_ONLY",
        "implementation_sha256": _implementation_hashes(repo_root),
        "limitations": [
            (
                "The retained V1 bundle cannot establish causal PIT calibration: "
                "its source timing does not satisfy the current minimum knowledge-lag contract."
            ),
            (
                "All retained replay counts and sensitivity statistics are diagnostic "
                "history invalidated with the legacy perpetual-ledger semantics."
            ),
            (
                "The deployed numeric threshold is an uncalibrated conservative "
                "safety limit, not a validated production calibration."
            ),
            (
                "Authority requires a separately reviewed V2 entrypoint backed by an "
                "independent PIT bundle and independently controlled signed source/result "
                "authority; self-generated hashes cannot grant authority and V1 can never "
                "upgrade automatically."
            ),
        ],
    }
    evidence["evidence_sha256"] = _sha256_bytes(_canonical_bytes(evidence))
    return evidence


def _read_manifest(manifest_path: Path) -> dict:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrowdingCalibrationEvidenceError("calibration manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise CrowdingCalibrationEvidenceError("unsupported calibration manifest")
    return manifest


def generate(repo_root: Path = _ROOT, *, write_snapshot: bool = True) -> dict:
    """Refresh only the invalidation evidence; retain V1 bundle bytes unchanged."""
    bundle = repo_root / _BUNDLE
    manifest_path = repo_root / _MANIFEST
    records = verify_calibration_bundle(
        bundle, manifest_path, repo_root=repo_root, check_sources=True
    )
    manifest = _read_manifest(manifest_path)
    evidence = build_invalidated_calibration_evidence(
        records, manifest, repo_root=repo_root
    )
    _write_json(repo_root / _EVIDENCE, evidence)
    if write_snapshot:
        _write_json(
            repo_root / _CACHE_SNAPSHOT,
            {
                "schema": "crowding_metric.invalidated.v1",
                "status": LEGACY_INVALIDATION_CODE,
                "readiness": "NOT_READY",
                "calibration_authoritative": False,
                "counted_as_calibration_evidence": False,
                "retained_diagnostic_summary": evidence["summary"],
            },
        )
    return evidence


def check(repo_root: Path = _ROOT) -> dict:
    bundle = repo_root / _BUNDLE
    manifest_path = repo_root / _MANIFEST
    evidence_path = repo_root / _EVIDENCE
    records = verify_calibration_bundle(
        bundle, manifest_path, repo_root=repo_root, check_sources=True
    )
    manifest = _read_manifest(manifest_path)
    expected = build_invalidated_calibration_evidence(
        records, manifest, repo_root=repo_root
    )
    try:
        observed = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrowdingCalibrationEvidenceError("calibration evidence is unreadable") from exc
    if observed != expected:
        raise CrowdingCalibrationEvidenceError("calibration evidence is stale or tampered")
    return observed


def _scan_current_pit_diagnostic() -> dict:
    """Private current-PIT diagnostic; cannot promote retained V1 authority."""
    records, _ = build_calibration_records()
    return summarize_calibration_records(records)


def main() -> None:
    if "--check" in sys.argv:
        evidence = check()
        print(
            "Crowding calibration evidence: invalidation current; "
            f"status={evidence['status']}; "
            f"retained_weeks={evidence['summary']['n_periods']}"
        )
        return
    evidence = generate()
    print(
        f"-> {_EVIDENCE} (status={evidence['status']}, "
        f"retained_weeks={evidence['summary']['n_periods']})"
    )


if __name__ == "__main__":
    main()
