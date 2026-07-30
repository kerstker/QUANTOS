"""Offline verification of retained external GitHub OIDC Sigstore evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any

from cryptography.hazmat.primitives.serialization import Encoding
from sigstore.errors import VerificationError
from sigstore.models import Bundle, InvalidBundle, TrustedRoot
from sigstore.verify import Verifier
from sigstore.verify.policy import Identity

from services.governance.artifact_admission import (
    load_json_without_duplicate_keys,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTITY = (
    "https://github.com/kerstker/QUANTOS/.github/workflows/"
    "sigstore-evidence.yml@refs/heads/codex/top-tier-wave0"
)
_ISSUER = "https://token.actions.githubusercontent.com"
_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
_BUILD_TYPE = (
    "https://quantos.dev/build-types/reproducible-source-bundle/v1"
)
_SOURCE_URI = (
    "git+https://github.com/kerstker/QUANTOS"
    "@refs/heads/codex/top-tier-wave0"
)
_BUILDERS = ["ubuntu-22.04", "ubuntu-24.04"]
_AUTHORITY = {
    "credential_access": False,
    "deployment": False,
    "execution": False,
    "live_trading": False,
    "order_submission": False,
}
_LIMITATIONS = {
    "external_chain_head_seal": "NOT_PROVIDED",
    "external_receipt_authority": "NOT_PROVIDED",
    "independent_admission_boundary": "NOT_PROVIDED",
    "revocation_operations": "NOT_PROVIDED",
    "worm_retention": "GITHUB_ARTIFACT_RETENTION_ONLY",
}
_MAX_JSON_BYTES = 8 * 1024 * 1024


class ExternalSigstoreEvidenceError(ValueError):
    """Retained external evidence is incomplete, inconsistent, or invalid."""


def _read(path: Path, *, maximum_bytes: int = _MAX_JSON_BYTES) -> bytes:
    if path.is_symlink():
        raise ExternalSigstoreEvidenceError(
            f"symlink evidence is not accepted: {path.name}"
        )
    try:
        with path.open("rb") as handle:
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise ExternalSigstoreEvidenceError(
                    f"evidence is not a regular file: {path.name}"
                )
            if before.st_size > maximum_bytes:
                raise ExternalSigstoreEvidenceError(
                    f"evidence exceeds size limit: {path.name}"
                )
            content = handle.read(maximum_bytes + 1)
            after = path.stat()
    except OSError as exc:
        raise ExternalSigstoreEvidenceError(
            f"evidence is unavailable: {path.name}"
        ) from exc
    if len(content) > maximum_bytes:
        raise ExternalSigstoreEvidenceError(
            f"evidence exceeds size limit: {path.name}"
        )
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ExternalSigstoreEvidenceError(
            f"evidence changed while being read: {path.name}"
        )
    return content


def _document(path: Path) -> tuple[bytes, dict[str, Any]]:
    content = _read(path)
    try:
        document = load_json_without_duplicate_keys(content)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        raise ExternalSigstoreEvidenceError(
            f"evidence JSON is invalid: {path.name}"
        ) from exc
    if not isinstance(document, dict):
        raise ExternalSigstoreEvidenceError(
            f"evidence JSON must be an object: {path.name}"
        )
    return content, document


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise ExternalSigstoreEvidenceError(detail)


def _verify_manifest(
    manifest: dict[str, Any],
    *,
    source_commit: str,
    artifact_sha256: str,
) -> None:
    _require(manifest.get("schema_version") == 1, "manifest schema is invalid")
    _require(
        manifest.get("artifact_kind")
        == "QUANTOS_REPRODUCIBLE_SOURCE_BUNDLE",
        "manifest artifact kind is invalid",
    )
    _require(
        manifest.get("source_commit") == source_commit,
        "manifest source commit does not match",
    )
    _require(
        manifest.get("archive_sha256") == artifact_sha256,
        "manifest artifact digest does not match",
    )
    _require(
        manifest.get("archive_format") == "POSIX_PAX_TAR_GZIP",
        "manifest archive format is invalid",
    )
    _require(manifest.get("authority") == _AUTHORITY, "manifest authority changed")
    files = manifest.get("files")
    _require(isinstance(files, list) and bool(files), "manifest files are missing")
    _require(
        manifest.get("file_count") == len(files),
        "manifest file count does not match",
    )
    paths: list[str] = []
    for entry in files:
        _require(isinstance(entry, dict), "manifest file entry is invalid")
        _require(
            set(entry)
            == {"git_blob_sha1", "mode", "path", "sha256", "size"},
            "manifest file entry shape is invalid",
        )
        path = entry.get("path")
        _require(isinstance(path, str) and bool(path), "manifest path is invalid")
        _require(
            entry.get("mode") in {"100644", "100755"},
            "manifest mode is invalid",
        )
        _require(
            isinstance(entry.get("git_blob_sha1"), str)
            and _COMMIT.fullmatch(entry["git_blob_sha1"]) is not None,
            "manifest Git blob digest is invalid",
        )
        _require(
            isinstance(entry.get("sha256"), str)
            and _SHA256.fullmatch(entry["sha256"]) is not None,
            "manifest file digest is invalid",
        )
        _require(
            type(entry.get("size")) is int and entry["size"] >= 0,
            "manifest file size is invalid",
        )
        paths.append(path)
    _require(
        paths == sorted(paths, key=lambda item: item.encode("utf-8")),
        "manifest paths are not canonical",
    )
    _require(len(paths) == len(set(paths)), "manifest paths are duplicated")
    content_root = _digest(
        json.dumps(
            files,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    _require(
        manifest.get("content_root_sha256") == content_root,
        "manifest content root does not match",
    )


def verify_external_sigstore_evidence(
    evidence_directory: str | Path,
) -> dict[str, Any]:
    """Verify the retained bundle without network, credentials, or signing."""
    root = Path(evidence_directory)
    if root.is_symlink() or not root.is_dir():
        raise ExternalSigstoreEvidenceError(
            "external Sigstore evidence directory is unavailable"
        )
    expected_names = {
        "external-evidence-receipt.json",
        "github-artifact.json",
        "quantos-source.manifest.json",
        "quantos-source.sigstore.json",
        "slsa-predicate.json",
        "trusted-root.json",
    }
    try:
        observed_names = {
            item.name for item in root.iterdir() if item.is_file()
        }
    except OSError as exc:
        raise ExternalSigstoreEvidenceError(
            "external Sigstore evidence directory cannot be read"
        ) from exc
    _require(observed_names == expected_names, "evidence file set is invalid")

    receipt_bytes, receipt = _document(
        root / "external-evidence-receipt.json"
    )
    manifest_bytes, manifest = _document(
        root / "quantos-source.manifest.json"
    )
    bundle_bytes, bundle_document = _document(
        root / "quantos-source.sigstore.json"
    )
    _, predicate = _document(root / "slsa-predicate.json")
    trusted_root_bytes, _ = _document(root / "trusted-root.json")
    _, artifact_metadata = _document(root / "github-artifact.json")

    _require(receipt.get("schema_version") == 1, "receipt schema is invalid")
    _require(
        receipt.get("status")
        == "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE",
        "receipt status is invalid",
    )
    source_commit = receipt.get("source_commit")
    _require(
        isinstance(source_commit, str)
        and _COMMIT.fullmatch(source_commit) is not None,
        "receipt source commit is invalid",
    )
    artifact_sha256 = receipt.get("artifact_sha256")
    _require(
        isinstance(artifact_sha256, str)
        and _SHA256.fullmatch(artifact_sha256) is not None,
        "receipt artifact digest is invalid",
    )
    _require(
        receipt.get("bundle_sha256") == _digest(bundle_bytes),
        "bundle digest does not match receipt",
    )
    _require(
        receipt.get("manifest_sha256") == _digest(manifest_bytes),
        "manifest digest does not match receipt",
    )
    _require(
        receipt.get("trusted_root_sha256") == _digest(trusted_root_bytes),
        "trusted root digest does not match receipt",
    )
    _require(
        receipt.get("certificate_identity") == _IDENTITY,
        "certificate identity is invalid",
    )
    _require(
        receipt.get("certificate_oidc_issuer") == _ISSUER,
        "certificate OIDC issuer is invalid",
    )
    _require(receipt.get("cross_host_builders") == _BUILDERS, "cross-host builders are invalid")
    _require(receipt.get("predicate_type") == _PREDICATE_TYPE, "predicate type is invalid")
    _require(receipt.get("sigstore_version") == "4.4.0", "Sigstore version is invalid")
    _require(receipt.get("rekor_version") == 1, "Rekor version is invalid")
    _require(receipt.get("authority") == _AUTHORITY, "receipt authority changed")
    _require(receipt.get("limitations") == _LIMITATIONS, "receipt limitations changed")

    _verify_manifest(
        manifest,
        source_commit=source_commit,
        artifact_sha256=artifact_sha256,
    )

    try:
        bundle = Bundle.from_json(bundle_bytes)
    except (InvalidBundle, TypeError, ValueError) as exc:
        raise ExternalSigstoreEvidenceError(
            "Sigstore bundle is invalid"
        ) from exc
    try:
        trusted_root = TrustedRoot.from_file(
            str(root / "trusted-root.json")
        )
        verifier = Verifier(trusted_root=trusted_root)
        payload_type, payload_bytes = verifier.verify_dsse(
            bundle,
            Identity(identity=_IDENTITY, issuer=_ISSUER),
        )
    except VerificationError as exc:
        raise ExternalSigstoreEvidenceError(
            "Sigstore DSSE verification failed"
        ) from exc
    except Exception as exc:
        raise ExternalSigstoreEvidenceError(
            "Sigstore verifier failed closed"
        ) from exc
    _require(
        payload_type == "application/vnd.in-toto+json",
        "DSSE payload type is invalid",
    )
    try:
        statement = load_json_without_duplicate_keys(payload_bytes)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        raise ExternalSigstoreEvidenceError(
            "signed in-toto statement is invalid"
        ) from exc
    _require(isinstance(statement, dict), "signed statement must be an object")
    _require(
        statement.get("_type") == "https://in-toto.io/Statement/v1",
        "signed statement type is invalid",
    )
    _require(
        statement.get("subject")
        == [
            {
                "name": "quantos-source.tar.gz",
                "digest": {"sha256": artifact_sha256},
            }
        ],
        "signed subject digest does not match",
    )
    _require(
        statement.get("predicateType") == _PREDICATE_TYPE,
        "signed predicate type does not match",
    )
    signed_predicate = statement.get("predicate")
    _require(
        signed_predicate == predicate,
        "retained predicate does not match signed payload",
    )
    _require(
        isinstance(signed_predicate, dict),
        "signed predicate is invalid",
    )
    build_definition = signed_predicate.get("buildDefinition")
    run_details = signed_predicate.get("runDetails")
    _require(
        isinstance(build_definition, dict)
        and isinstance(run_details, dict),
        "signed SLSA structure is invalid",
    )
    _require(
        build_definition.get("buildType") == _BUILD_TYPE,
        "signed build type is invalid",
    )
    _require(
        build_definition.get("externalParameters")
        == {"sourceCommit": source_commit},
        "signed source commit does not match",
    )
    _require(
        build_definition.get("internalParameters")
        == {"crossHostBuilders": _BUILDERS},
        "signed cross-host builders do not match",
    )
    _require(
        build_definition.get("resolvedDependencies")
        == [
            {
                "uri": _SOURCE_URI,
                "digest": {"gitCommit": source_commit},
            }
        ],
        "signed source dependency does not match",
    )
    workflow_run = receipt.get("workflow_run")
    _require(
        run_details.get("builder") == {"id": _IDENTITY},
        "signed builder identity does not match",
    )
    _require(
        run_details.get("metadata") == {"invocationId": workflow_run},
        "signed invocation identity does not match",
    )
    _require(run_details.get("byproducts") == [], "signed byproducts are invalid")

    _require(
        bundle_document.get("mediaType")
        == "application/vnd.dev.sigstore.bundle.v0.3+json",
        "Sigstore bundle media type is invalid",
    )
    entries = (
        bundle_document.get("verificationMaterial", {}).get("tlogEntries")
        if isinstance(bundle_document.get("verificationMaterial"), dict)
        else None
    )
    _require(
        isinstance(entries, list) and len(entries) == 1,
        "Sigstore transparency entry count is invalid",
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

    _require(
        artifact_metadata.get("schema_version") == 1,
        "GitHub artifact metadata schema is invalid",
    )
    _require(
        artifact_metadata.get("status")
        == "RETAINED_EXTERNAL_GITHUB_ACTIONS_ARTIFACT",
        "GitHub artifact metadata status is invalid",
    )
    _require(
        artifact_metadata.get("source_commit") == source_commit,
        "GitHub artifact source commit does not match",
    )
    _require(
        artifact_metadata.get("authority") == _AUTHORITY,
        "GitHub artifact authority changed",
    )
    _require(
        artifact_metadata.get("limitations") == _LIMITATIONS,
        "GitHub artifact limitations changed",
    )
    artifact = artifact_metadata.get("artifact")
    workflow = artifact_metadata.get("workflow")
    download = artifact_metadata.get("download_verification")
    _require(
        isinstance(artifact, dict)
        and isinstance(workflow, dict)
        and isinstance(download, dict),
        "GitHub artifact metadata structure is invalid",
    )
    zip_digest = artifact.get("digest")
    _require(
        isinstance(zip_digest, str)
        and zip_digest.startswith("sha256:")
        and _SHA256.fullmatch(zip_digest.removeprefix("sha256:")) is not None,
        "GitHub artifact ZIP digest is invalid",
    )
    _require(
        download
        == {
            "downloaded_size_in_bytes": artifact.get("size_in_bytes"),
            "downloaded_zip_sha256": zip_digest.removeprefix("sha256:"),
            "status": "VERIFIED",
        },
        "GitHub artifact download verification does not match",
    )
    _require(
        workflow_run
        == f"{workflow.get('url')}/attempts/{workflow.get('attempt')}",
        "workflow run identity does not match",
    )
    _require(
        workflow.get("run_id") == 30540314233
        and artifact.get("artifact_id") == 8758408888,
        "external workflow or artifact ID is invalid",
    )

    return {
        "status": "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE_CROSS_HOST",
        "source_commit": source_commit,
        "artifact_sha256": artifact_sha256,
        "bundle_sha256": _digest(bundle_bytes),
        "trusted_root_sha256": _digest(trusted_root_bytes),
        "certificate_sha256": certificate_sha256,
        "certificate_identity": _IDENTITY,
        "certificate_oidc_issuer": _ISSUER,
        "transparency_integrated_at_ms": int(integrated_time) * 1_000,
        "transparency_log_verified": True,
        "signed_certificate_timestamp_verified": True,
        "cross_host_builders": list(_BUILDERS),
        "workflow_run_id": workflow["run_id"],
        "artifact_id": artifact["artifact_id"],
        "external_artifact_retention_expires_at": artifact["expires_at"],
        "artifact_bytes_retained_in_repository": False,
        "artifact_zip_sha256": zip_digest.removeprefix("sha256:"),
        "m4_limitations": [
            "EXTERNAL_RECEIPT_AUTHORITY_NOT_PROVIDED",
            "INDEPENDENT_ADMISSION_BOUNDARY_NOT_PROVIDED",
            "REVOCATION_OPERATIONS_NOT_PROVIDED",
            "EXTERNAL_CHAIN_HEAD_SEAL_NOT_PROVIDED",
            "WORM_RETENTION_NOT_PROVIDED",
        ],
    }


__all__ = [
    "ExternalSigstoreEvidenceError",
    "verify_external_sigstore_evidence",
]
