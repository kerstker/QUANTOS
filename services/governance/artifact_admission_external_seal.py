"""Offline verification for an externally sealed artifact-admission chain head."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any, Mapping

from cryptography.hazmat.primitives.serialization import Encoding
from sigstore.errors import VerificationError
from sigstore.models import Bundle, InvalidBundle, TrustedRoot
from sigstore.verify import Verifier
from sigstore.verify.policy import Identity

from services.governance.json_integrity import (
    load_json_without_duplicate_keys,
)


STATUS = (
    "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE_REKOR_"
    "ARTIFACT_ADMISSION_CHAIN_HEAD"
)
CERTIFICATE_IDENTITY = (
    "https://github.com/kerstker/QUANTOS/.github/workflows/"
    "sigstore-evidence.yml@refs/heads/codex/top-tier-wave0"
)
CERTIFICATE_ISSUER = "https://token.actions.githubusercontent.com"
AUTHORITY = {
    "credential_access": False,
    "deployment": False,
    "execution": False,
    "live_trading": False,
    "order_submission": False,
}
RETENTION = {
    "github_artifact_days": 90,
    "rekor_transparency_log": True,
    "worm_object_lock": False,
}
LIMITATIONS = [
    "GITHUB_ARTIFACT_IS_NOT_WORM_OBJECT_LOCK",
    "INDEPENDENT_ADMISSION_DEPLOYMENT_NOT_PROVIDED",
    "SOURCE_NATIVE_CHECKPOINT_REVOCATION_NOT_PROVIDED",
]

_EXPECTED_NAMES = {
    "artifact-admission-external-receipt.json",
    "artifact-admission-external-receipt.sigstore.json",
    "artifact-admission-seal.json",
    "trusted-root.json",
}
_RECEIPT_FIELDS = {
    "schema_version",
    "status",
    "source_commit",
    "workflow_run",
    "certificate_identity",
    "certificate_oidc_issuer",
    "sigstore_version",
    "rekor_version",
    "seal_sha256",
    "local_anchor_sha256",
    "commitment",
    "retention",
    "limitations",
    "authority",
}
_COMMITMENT_FIELDS = {
    "schema_version",
    "store_kind",
    "store_schema_version",
    "event_count",
    "event_head_hash",
    "decision_count",
    "registry_snapshot_count",
    "overall_hash",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_WORKFLOW_RUN = re.compile(
    r"^https://github\.com/kerstker/QUANTOS/actions/runs/"
    r"[1-9][0-9]*/attempts/[1-9][0-9]*$"
)
_MAX_BYTES = 8 * 1024 * 1024


class ArtifactAdmissionExternalSealError(ValueError):
    """External chain-head evidence is incomplete, inconsistent, or invalid."""


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise ArtifactAdmissionExternalSealError(detail)


def _read_regular(path: Path) -> bytes:
    if path.is_symlink():
        raise ArtifactAdmissionExternalSealError(
            f"symlink evidence is not accepted: {path.name}"
        )
    try:
        with path.open("rb") as handle:
            before = path.stat()
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size > _MAX_BYTES
            ):
                raise ArtifactAdmissionExternalSealError(
                    f"evidence file is invalid: {path.name}"
                )
            content = handle.read(_MAX_BYTES + 1)
            after = path.stat()
    except OSError as exc:
        raise ArtifactAdmissionExternalSealError(
            f"evidence file is unavailable: {path.name}"
        ) from exc
    if (
        len(content) > _MAX_BYTES
        or len(content) != before.st_size
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise ArtifactAdmissionExternalSealError(
            f"evidence changed while being read: {path.name}"
        )
    return content


def _object(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = load_json_without_duplicate_keys(content)
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise ArtifactAdmissionExternalSealError(
            f"{label} is not valid JSON"
        ) from exc
    if not isinstance(value, Mapping):
        raise ArtifactAdmissionExternalSealError(
            f"{label} must be an object"
        )
    return dict(value)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def artifact_admission_seal_contract_sha256(
    value: Mapping[str, Any],
) -> str:
    """Hash the stable authority contract, excluding local observation time.

    The retained Sigstore bundle still authenticates every byte of the signed
    seal, including its original timestamp.  This comparison digest exists to
    match that signed checkpoint to a later local observation of the *same*
    authority head; a scheduler refresh must not invalidate the checkpoint
    merely because ``observed_at_ms`` advanced.
    """
    if not isinstance(value, Mapping):
        raise TypeError("artifact-admission seal contract must be an object")
    contract = dict(value)
    contract.pop("observed_at_ms", None)
    return _digest(
        json.dumps(
            contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _verify_commitment(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "commitment must be an object")
    commitment = dict(value)
    _require(
        set(commitment) == _COMMITMENT_FIELDS,
        "commitment shape is invalid",
    )
    _require(
        commitment.get("schema_version") == 1,
        "commitment schema is invalid",
    )
    _require(
        commitment.get("store_kind")
        == "QUANTOS_ARTIFACT_ADMISSION_AUTHORITY",
        "commitment store kind is invalid",
    )
    _require(
        commitment.get("store_schema_version") == 4,
        "commitment store schema is invalid",
    )
    for field in ("event_count", "decision_count", "registry_snapshot_count"):
        _require(
            type(commitment.get(field)) is int and commitment[field] >= 0,
            f"commitment {field} is invalid",
        )
    _require(
        commitment["event_count"] >= commitment["registry_snapshot_count"],
        "commitment event count is inconsistent",
    )
    for field in ("event_head_hash", "overall_hash"):
        _require(
            isinstance(commitment.get(field), str)
            and _SHA256.fullmatch(commitment[field]) is not None,
            f"commitment {field} is invalid",
        )
    return commitment


def verify_artifact_admission_external_seal(
    evidence_directory: str | Path,
) -> dict[str, Any]:
    """Verify the retained GitHub OIDC/Sigstore/Rekor chain-head receipt."""
    root = Path(evidence_directory)
    if root.is_symlink() or not root.is_dir():
        raise ArtifactAdmissionExternalSealError(
            "external chain-head evidence directory is unavailable"
        )
    try:
        names = {item.name for item in root.iterdir() if item.is_file()}
    except OSError as exc:
        raise ArtifactAdmissionExternalSealError(
            "external chain-head evidence directory cannot be read"
        ) from exc
    _require(names == _EXPECTED_NAMES, "external evidence file set is invalid")

    seal_bytes = _read_regular(root / "artifact-admission-seal.json")
    receipt_bytes = _read_regular(
        root / "artifact-admission-external-receipt.json"
    )
    bundle_bytes = _read_regular(
        root / "artifact-admission-external-receipt.sigstore.json"
    )
    trusted_root_bytes = _read_regular(root / "trusted-root.json")
    seal = _object(seal_bytes, "artifact-admission seal")
    receipt = _object(receipt_bytes, "external chain-head receipt")
    bundle_document = _object(bundle_bytes, "Sigstore bundle")
    _object(trusted_root_bytes, "trusted root")

    _require(
        seal.get("schema_version") == 1
        and seal.get("operation_verdict") == "PASS"
        and seal.get("institutional_seal_status")
        == "PARTIAL_EXTERNAL_WORM_UNVERIFIED"
        and seal.get("authority_mutated") is False
        and type(seal.get("anchor_created")) is bool
        and seal.get("destination_class") in {"SEPARATE_VOLUME", "OFF_HOST_UNC"},
        "local seal contract is invalid",
    )
    _require(
        isinstance(seal.get("anchor_sha256"), str)
        and _SHA256.fullmatch(seal["anchor_sha256"]) is not None,
        "local anchor digest is invalid",
    )
    commitment = _verify_commitment(seal.get("commitment"))

    _require(set(receipt) == _RECEIPT_FIELDS, "receipt shape is invalid")
    _require(receipt.get("schema_version") == 1, "receipt schema is invalid")
    _require(receipt.get("status") == STATUS, "receipt status is invalid")
    _require(
        isinstance(receipt.get("source_commit"), str)
        and _COMMIT.fullmatch(receipt["source_commit"]) is not None,
        "receipt source commit is invalid",
    )
    _require(
        isinstance(receipt.get("workflow_run"), str)
        and _WORKFLOW_RUN.fullmatch(receipt["workflow_run"]) is not None,
        "receipt workflow run is invalid",
    )
    _require(
        receipt.get("certificate_identity") == CERTIFICATE_IDENTITY
        and receipt.get("certificate_oidc_issuer") == CERTIFICATE_ISSUER,
        "receipt workload identity is invalid",
    )
    _require(
        receipt.get("sigstore_version") == "4.4.0"
        and receipt.get("rekor_version") == 1,
        "receipt signer contract is invalid",
    )
    _require(
        receipt.get("seal_sha256") == _digest(seal_bytes),
        "receipt does not bind the local seal",
    )
    _require(
        receipt.get("local_anchor_sha256") == seal["anchor_sha256"],
        "receipt local anchor digest is inconsistent",
    )
    _require(
        receipt.get("commitment") == commitment,
        "receipt commitment is inconsistent",
    )
    _require(receipt.get("retention") == RETENTION, "retention claim changed")
    _require(receipt.get("limitations") == LIMITATIONS, "limitations changed")
    _require(receipt.get("authority") == AUTHORITY, "receipt authority changed")

    try:
        bundle = Bundle.from_json(bundle_bytes)
        trusted_root = TrustedRoot.from_file(str(root / "trusted-root.json"))
        Verifier(trusted_root=trusted_root).verify_artifact(
            receipt_bytes,
            bundle,
            Identity(
                identity=CERTIFICATE_IDENTITY,
                issuer=CERTIFICATE_ISSUER,
            ),
        )
    except (InvalidBundle, VerificationError, OSError, TypeError, ValueError) as exc:
        raise ArtifactAdmissionExternalSealError(
            "external chain-head Sigstore verification failed"
        ) from exc
    except Exception as exc:
        raise ArtifactAdmissionExternalSealError(
            "external chain-head Sigstore verifier failed closed"
        ) from exc

    _require(
        bundle_document.get("mediaType")
        == "application/vnd.dev.sigstore.bundle.v0.3+json",
        "Sigstore bundle media type is invalid",
    )
    material = bundle_document.get("verificationMaterial")
    entries = (
        material.get("tlogEntries")
        if isinstance(material, Mapping)
        else None
    )
    _require(
        isinstance(entries, list) and len(entries) == 1,
        "Rekor transparency entry count is invalid",
    )
    integrated_time = entries[0].get("integratedTime")
    _require(
        isinstance(integrated_time, str)
        and re.fullmatch(r"[1-9][0-9]*", integrated_time) is not None,
        "Rekor integrated time is invalid",
    )
    certificate_sha256 = _digest(
        bundle.signing_certificate.public_bytes(Encoding.DER)
    )
    return {
        "status": STATUS,
        "source_commit": receipt["source_commit"],
        "workflow_run": receipt["workflow_run"],
        "seal_sha256": receipt["seal_sha256"],
        "seal_contract_sha256": (
            artifact_admission_seal_contract_sha256(seal)
        ),
        "receipt_sha256": _digest(receipt_bytes),
        "local_anchor_sha256": receipt["local_anchor_sha256"],
        "commitment": commitment,
        "bundle_sha256": _digest(bundle_bytes),
        "trusted_root_sha256": _digest(trusted_root_bytes),
        "certificate_sha256": certificate_sha256,
        "certificate_identity": CERTIFICATE_IDENTITY,
        "certificate_oidc_issuer": CERTIFICATE_ISSUER,
        "transparency_integrated_at_ms": int(integrated_time) * 1_000,
        "external_signature_verified": True,
        "transparency_log_verified": True,
        "signed_certificate_timestamp_verified": True,
        "worm_retention_verified": False,
        "retention": dict(RETENTION),
        "limitations": list(LIMITATIONS),
    }


__all__ = [
    "AUTHORITY",
    "CERTIFICATE_IDENTITY",
    "CERTIFICATE_ISSUER",
    "LIMITATIONS",
    "RETENTION",
    "STATUS",
    "ArtifactAdmissionExternalSealError",
    "artifact_admission_seal_contract_sha256",
    "verify_artifact_admission_external_seal",
]
