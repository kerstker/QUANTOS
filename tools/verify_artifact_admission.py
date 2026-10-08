#!/usr/bin/env python
"""Offline, verify-only Sigstore admission for a retained build artifact."""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import PackageNotFoundError, version as distribution_version
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from typing import Sequence

from cryptography.x509 import (
    ObjectIdentifier,
    OtherName,
    RFC822Name,
    SubjectAlternativeName,
    UniformResourceIdentifier,
)
from cryptography.hazmat.primitives.serialization import Encoding
from pydantic import ValidationError
from sigstore.errors import VerificationError
from sigstore.models import Bundle, InvalidBundle, TrustedRoot
from sigstore.verify import Verifier
from sigstore.verify.policy import Identity, OIDCIssuer

from services.governance.artifact_admission import (
    ArtifactAdmissionDecision,
    ArtifactAdmissionPolicy,
    ArtifactVerificationReceipt,
    VerifiedSigstoreFacts,
    artifact_verification_receipt_sha256,
    denied_admission_decision,
    evaluate_verified_provenance,
    load_json_without_duplicate_keys,
)
from services.governance.artifact_admission_store import (
    ARTIFACT_ADMISSION_STORE_KIND,
    ARTIFACT_ADMISSION_STORE_SCHEMA_VERSION,
    POLICY_REGISTRY_KIND,
    RECEIPT_AUTHORITY_REGISTRY_KIND,
    ArtifactAdmissionRegistryHead,
    ArtifactAdmissionStore,
    ArtifactAdmissionStoreClockRollback,
    ArtifactAdmissionStoreError,
    ArtifactAdmissionStoreRegistryConflict,
    ArtifactAdmissionStoreRegistryGenerationGap,
    ArtifactAdmissionStoreRegistryHeadChanged,
    ArtifactAdmissionStoreRegistryHeadMismatch,
    ArtifactAdmissionStoreRegistryRollback,
    ArtifactAdmissionStoreRegistryUnprovisioned,
)
from services.governance.artifact_receipt_auth import (
    ArtifactReceiptAuthenticationError,
    ReceiptAuthorityKey,
    SignedArtifactVerificationReceipt,
    verify_signed_artifact_receipt,
)


POLICY_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "artifact_admission_policy_registry.json"
RECEIPT_AUTHORITY_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "artifact_receipt_authority_registry.json"
)
DECISION_STORE_PATH = Path(__file__).resolve().parents[1] / "data" / "cache" / "artifact_admission.db"
_FULCIO_OTHERNAME_OID = ObjectIdentifier("1.3.6.1.4.1.57264.1.7")
_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024 * 1024


class AdmissionVerificationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_symlink():
        raise ValueError("symlink inputs are not accepted")
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("input must be a regular file")
        if before.st_size > _MAX_ARTIFACT_BYTES:
            raise ValueError("artifact exceeds the admission size limit")
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
        after = os.fstat(handle.fileno())
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
        raise ValueError("artifact changed while it was hashed")
    return digest.hexdigest()


def _read_bounded(path: Path, maximum_bytes: int) -> bytes:
    if path.is_symlink():
        raise ValueError("symlink inputs are not accepted")
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("input must be a regular file")
        if before.st_size > maximum_bytes:
            raise ValueError(f"input exceeds {maximum_bytes} bytes")
        content = handle.read(maximum_bytes + 1)
        after = os.fstat(handle.fileno())
    if len(content) > maximum_bytes:
        raise ValueError(f"input exceeds {maximum_bytes} bytes")
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
        raise ValueError("input changed while it was read")
    return content


def _runtime_verifier_id() -> str:
    try:
        installed = distribution_version("sigstore")
    except PackageNotFoundError:
        return "sigstore-python-unavailable-offline"
    return f"sigstore-python-{installed}-offline"


def _protobuf_uint64(value: object) -> int:
    if not isinstance(value, str) or re.fullmatch(r"(?:0|[1-9][0-9]*)", value) is None:
        raise ValueError("expected canonical protobuf uint64 string")
    parsed = int(value)
    if parsed > (1 << 64) - 1:
        raise ValueError("protobuf uint64 is out of range")
    return parsed


def _policy_registry_decision(
    *,
    registry_bytes: bytes,
    policy: ArtifactAdmissionPolicy,
    policy_bytes: bytes,
) -> tuple[str, str, int | None]:
    try:
        registry_sha256 = _sha256(registry_bytes)
        registry = load_json_without_duplicate_keys(registry_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return "POLICY_REGISTRY_INVALID", "", None
    if (
        not isinstance(registry, dict)
        or set(registry) != {"schema_version", "readiness", "generation", "policies"}
        or registry.get("schema_version") != 2
        or registry.get("readiness") != "L2_DRY_RUN_ONLY"
        or type(registry.get("generation")) is not int
        or registry["generation"] < 0
        or not isinstance(registry.get("policies"), dict)
    ):
        return "POLICY_REGISTRY_INVALID", registry_sha256, None
    generation = registry["generation"]
    entry = registry["policies"].get(policy.policy_id)
    if entry is None:
        return "POLICY_NOT_TRUSTED", registry_sha256, generation
    if (
        not isinstance(entry, dict)
        or set(entry) != {"policy_sha256", "status"}
        or entry.get("status") not in {"ACTIVE", "REVOKED"}
        or not isinstance(entry.get("policy_sha256"), str)
        or len(entry["policy_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in entry["policy_sha256"])
    ):
        return "POLICY_REGISTRY_INVALID", registry_sha256, generation
    if entry["status"] == "REVOKED":
        return "POLICY_REVOKED", registry_sha256, generation
    if _sha256(policy_bytes) != entry["policy_sha256"]:
        return "POLICY_DIGEST_MISMATCH", registry_sha256, generation
    return "", registry_sha256, generation


def _receipt_authority_registry_decision(
    *,
    registry_bytes: bytes,
    policy: ArtifactAdmissionPolicy,
) -> tuple[str, str, int | None, ReceiptAuthorityKey | None]:
    try:
        registry_sha256 = _sha256(registry_bytes)
        registry = load_json_without_duplicate_keys(registry_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return "RECEIPT_AUTHORITY_REGISTRY_INVALID", "", None, None
    if (
        not isinstance(registry, dict)
        or set(registry) != {"schema_version", "readiness", "generation", "keys"}
        or registry.get("schema_version") != 2
        or registry.get("readiness") != "L2_DRY_RUN_ONLY"
        or type(registry.get("generation")) is not int
        or registry["generation"] < 0
        or not isinstance(registry.get("keys"), dict)
    ):
        return "RECEIPT_AUTHORITY_REGISTRY_INVALID", registry_sha256, None, None
    generation = registry["generation"]
    raw_key = registry["keys"].get(policy.required_receipt_key_id)
    if raw_key is None:
        return (
            "RECEIPT_AUTHORITY_KEY_NOT_TRUSTED",
            registry_sha256,
            generation,
            None,
        )
    try:
        authority_key = ReceiptAuthorityKey.model_validate(raw_key)
    except ValidationError:
        return (
            "RECEIPT_AUTHORITY_REGISTRY_INVALID",
            registry_sha256,
            generation,
            None,
        )
    if authority_key.key_id != policy.required_receipt_key_id:
        return (
            "RECEIPT_AUTHORITY_REGISTRY_INVALID",
            registry_sha256,
            generation,
            None,
        )
    if authority_key.status == "REVOKED":
        return (
            "RECEIPT_AUTHORITY_KEY_REVOKED",
            registry_sha256,
            generation,
            authority_key,
        )
    if (
        authority_key.authority_id != policy.required_receipt_authority_id
        or authority_key.public_key_sha256 != policy.required_receipt_public_key_sha256
    ):
        return (
            "RECEIPT_AUTHORITY_POLICY_MISMATCH",
            registry_sha256,
            generation,
            authority_key,
        )
    return "", registry_sha256, generation, authority_key


def _registry_mirror_reason(
    *,
    registry_path: Path,
    authority_head: ArtifactAdmissionRegistryHead,
    reason_prefix: str,
) -> str:
    try:
        mirror_bytes = _read_bounded(registry_path, 1024 * 1024)
        load_json_without_duplicate_keys(mirror_bytes)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        return f"{reason_prefix}_MIRROR_INVALID"
    if mirror_bytes != authority_head.registry_bytes:
        return f"{reason_prefix}_MIRROR_MISMATCH"
    return ""


def _certificate_identity(certificate, expected_identity: str) -> str:
    san = certificate.extensions.get_extension_for_class(SubjectAlternativeName).value
    identities = set(san.get_values_for_type(RFC822Name))
    identities.update(san.get_values_for_type(UniformResourceIdentifier))
    identities.update(
        value.value.decode("utf-8")
        for value in san.get_values_for_type(OtherName)
        if value.type_id == _FULCIO_OTHERNAME_OID
    )
    if expected_identity not in identities:
        raise ValueError("verified certificate identity is not observable")
    return next(value for value in identities if value == expected_identity)


def _certificate_oidc_issuer(certificate, expected_issuer: str) -> str:
    extension = certificate.extensions.get_extension_for_oid(OIDCIssuer.oid).value
    raw_value = getattr(extension, "value", None)
    if not isinstance(raw_value, bytes):
        raise ValueError("verified certificate OIDC issuer is not observable")
    observed = raw_value.decode("utf-8")
    if observed != expected_issuer:
        raise ValueError("verified certificate OIDC issuer changed after verification")
    return observed


def _verify_sigstore_dsse(
    *,
    bundle_bytes: bytes,
    trusted_root_bytes: bytes,
    identity: str,
    issuer: str,
    artifact_sha256: str,
    bundle_sha256: str,
    trusted_root_sha256: str,
) -> tuple[str, bytes, VerifiedSigstoreFacts]:
    try:
        load_json_without_duplicate_keys(bundle_bytes)
        bundle = Bundle.from_json(bundle_bytes)
    except (
        InvalidBundle,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        raise AdmissionVerificationError("SIGSTORE_BUNDLE_INVALID") from exc
    try:
        load_json_without_duplicate_keys(trusted_root_bytes)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        raise AdmissionVerificationError("SIGSTORE_TRUSTED_ROOT_INVALID") from exc
    with tempfile.TemporaryDirectory(prefix="quantos-sigstore-root-") as directory:
        trusted_root_path = Path(directory) / "trusted-root.json"
        trusted_root_path.write_bytes(trusted_root_bytes)
        try:
            trusted_root = TrustedRoot.from_file(str(trusted_root_path))
        except Exception as exc:
            raise AdmissionVerificationError("SIGSTORE_TRUSTED_ROOT_INVALID") from exc
        try:
            verifier = Verifier(trusted_root=trusted_root)
            verification_policy = Identity(identity=identity, issuer=issuer)
            payload_type, payload = verifier.verify_dsse(bundle, verification_policy)
        except VerificationError as exc:
            raise AdmissionVerificationError("SIGSTORE_VERIFICATION_FAILED") from exc
        except Exception as exc:
            raise AdmissionVerificationError("SIGSTORE_VERIFIER_ERROR") from exc
    try:
        certificate = bundle.signing_certificate
        certificate_sha256 = _sha256(certificate.public_bytes(Encoding.DER))
        bundle_document = load_json_without_duplicate_keys(bundle.to_json())
        entries = bundle_document["verificationMaterial"]["tlogEntries"]
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError("bundle must contain exactly one transparency entry")
        try:
            integrated_time = _protobuf_uint64(entries[0].get("integratedTime"))
        except ValueError as exc:
            raise AdmissionVerificationError("SIGSTORE_REKOR_V1_INTEGRATED_TIME_REQUIRED") from exc
        integrated_at_ms = integrated_time * 1_000
        not_before = certificate.not_valid_before_utc
        not_after = certificate.not_valid_after_utc
        facts = VerifiedSigstoreFacts(
            schema_version=1,
            verifier_id=_runtime_verifier_id(),
            artifact_sha256=artifact_sha256,
            bundle_sha256=bundle_sha256,
            trusted_root_sha256=trusted_root_sha256,
            certificate_sha256=certificate_sha256,
            certificate_identity=_certificate_identity(certificate, identity),
            certificate_oidc_issuer=_certificate_oidc_issuer(certificate, issuer),
            certificate_not_before_ms=int(not_before.timestamp() * 1_000),
            certificate_not_after_ms=int(not_after.timestamp() * 1_000),
            transparency_integrated_at_ms=integrated_at_ms,
            transparency_time_source="REKOR_V1_INTEGRATED_TIME",
            transparency_log_verified=True,
            signed_certificate_timestamp_verified=True,
        )
    except AdmissionVerificationError:
        raise
    except Exception as exc:
        raise AdmissionVerificationError("SIGSTORE_VERIFIED_FACTS_INVALID") from exc
    return payload_type, payload, facts


def _write_decision(path: Path, decision: ArtifactAdmissionDecision) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            decision.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _emit(decision: ArtifactAdmissionDecision, output_path: Path | None) -> int:
    if output_path is not None:
        _write_decision(output_path, decision)
    print(json.dumps(decision.model_dump(mode="json"), sort_keys=True))
    return 0 if decision.verdict == "ALLOW" else 1


def _persist_and_emit(
    *,
    store: ArtifactAdmissionStore,
    decision: ArtifactAdmissionDecision,
    output_path: Path | None,
    policy_sha256: str,
    receipt_sha256: str,
    artifact_sha256: str,
    recorded_at_ms: int,
    expected_policy_registry_head: ArtifactAdmissionRegistryHead | None,
    expected_receipt_authority_registry_head: (ArtifactAdmissionRegistryHead | None),
) -> int:
    if decision.admission_at_ms not in {None, recorded_at_ms}:
        raise ValueError("decision and authority clocks must be identical")
    stamped = decision.model_copy(
        update={
            "admission_at_ms": recorded_at_ms,
            "authority_store_kind": ARTIFACT_ADMISSION_STORE_KIND,
            "authority_store_schema_version": (ARTIFACT_ADMISSION_STORE_SCHEMA_VERSION),
        }
    )
    identity = json.dumps(
        {
            "artifact_sha256": artifact_sha256,
            "decision": stamped.model_dump(mode="json"),
            "policy_sha256": policy_sha256,
            "receipt_sha256": receipt_sha256,
            "recorded_at_ms": recorded_at_ms,
        },
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    decision_id = f"artifact-admission-{_sha256(identity)}"
    stamped = stamped.model_copy(update={"authority_decision_id": decision_id})
    store.record(
        stamped,
        policy_sha256=policy_sha256,
        receipt_sha256=receipt_sha256,
        artifact_sha256=artifact_sha256,
        recorded_at_ms=recorded_at_ms,
        decision_id=decision_id,
        expected_policy_registry_head=expected_policy_registry_head,
        expected_receipt_authority_registry_head=(expected_receipt_authority_registry_head),
    )
    store.close()
    return _emit(stamped, output_path)


def _clear_stale_output(path: Path | None) -> bool:
    if path is None:
        return True
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _requested_output_path(argv: Sequence[str]) -> Path | None:
    for index, value in enumerate(argv):
        if value == "--decision-output":
            if index + 1 < len(argv):
                return Path(argv[index + 1])
            return None
        prefix = "--decision-output="
        if value.startswith(prefix) and value != prefix:
            return Path(value[len(prefix) :])
    return None


def _preflight_reason(
    *,
    policy: ArtifactAdmissionPolicy,
    receipt: ArtifactVerificationReceipt,
    artifact_sha256: str,
    bundle_sha256: str,
    trusted_root_sha256: str,
    verifier_runtime_id: str,
) -> str:
    if artifact_sha256 != policy.artifact_sha256:
        return "ARTIFACT_DIGEST_MISMATCH"
    if trusted_root_sha256 != policy.trusted_root_sha256:
        return "TRUSTED_ROOT_DIGEST_MISMATCH"
    if verifier_runtime_id != policy.required_verifier_id:
        return "VERIFIER_RUNTIME_MISMATCH"
    if receipt.verifier_id != policy.required_verifier_id:
        return "RECEIPT_VERIFIER_MISMATCH"
    if receipt.artifact_sha256 != artifact_sha256:
        return "RECEIPT_ARTIFACT_MISMATCH"
    if receipt.bundle_sha256 != bundle_sha256:
        return "RECEIPT_BUNDLE_MISMATCH"
    if receipt.trusted_root_sha256 != trusted_root_sha256:
        return "RECEIPT_TRUSTED_ROOT_MISMATCH"
    if receipt.revocation_source != policy.required_revocation_source:
        return "REVOCATION_SOURCE_MISMATCH"
    if receipt.revocation_status == "REVOKED":
        return "CERTIFICATE_REVOKED"
    if receipt.revocation_status != "GOOD":
        return "REVOCATION_STATUS_UNKNOWN"
    return ""


def main(
    argv: Sequence[str] | None = None,
    *,
    policy_registry_path: Path = POLICY_REGISTRY_PATH,
    receipt_authority_registry_path: Path = RECEIPT_AUTHORITY_REGISTRY_PATH,
    decision_store_path: Path = DECISION_STORE_PATH,
) -> int:
    argument_values = list(sys.argv[1:] if argv is None else argv)
    requested_output = _requested_output_path(argument_values)
    if not _clear_stale_output(requested_output):
        decision = denied_admission_decision("ADMISSION_OUTPUT_UNAVAILABLE")
        print(json.dumps(decision.model_dump(mode="json"), sort_keys=True))
        return 1
    parser = argparse.ArgumentParser(description="Verify a Sigstore DSSE/SLSA bundle at an offline admission boundary")
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--trusted-root", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--verification-receipt", required=True)
    parser.add_argument("--decision-output")
    args = parser.parse_args(argument_values)
    output_path = Path(args.decision_output) if args.decision_output else None
    try:
        store = ArtifactAdmissionStore(str(decision_store_path), create=False)
    except (ArtifactAdmissionStoreError, OSError, TypeError, ValueError):
        decision = denied_admission_decision(
            "ADMISSION_AUTHORITY_UNAVAILABLE_UNRECORDED",
            admission_at_ms=int(time.time() * 1_000),
        )
        return _emit(decision, output_path)
    verifier_runtime_id = _runtime_verifier_id()
    policy: ArtifactAdmissionPolicy | None = None
    receipt: ArtifactVerificationReceipt | None = None
    receipt_envelope: SignedArtifactVerificationReceipt | None = None
    receipt_authentication = None
    artifact_sha = ""
    bundle_sha = ""
    root_sha = ""
    policy_sha = ""
    registry_sha = ""
    policy_registry_generation = None
    policy_registry_head: ArtifactAdmissionRegistryHead | None = None
    receipt_authority_registry_sha = ""
    receipt_authority_registry_generation = None
    receipt_authority_registry_head: ArtifactAdmissionRegistryHead | None = None
    receipt_sha = ""

    def emit_unrecorded(
        decision: ArtifactAdmissionDecision,
        *,
        reason_code: str,
        recorded_at_ms: int,
    ) -> int:
        try:
            store.close()
        except Exception:
            pass
        failure = decision.model_copy(
            update={
                "verdict": "DENY",
                "reason_code": reason_code,
                "admission_at_ms": recorded_at_ms,
                "authority_store_kind": "",
                "authority_store_schema_version": None,
                "authority_decision_id": "",
            }
        )
        return _emit(failure, output_path)

    def persist(decision: ArtifactAdmissionDecision) -> int:
        head_bindings: dict[str, object] = {}
        if policy_registry_head is not None:
            head_bindings.update(
                {
                    "policy_registry_sha256": (policy_registry_head.registry_sha256),
                    "policy_registry_generation": (policy_registry_head.generation),
                    "policy_registry_event_seq": (policy_registry_head.authority_event_seq),
                    "policy_registry_event_hash": policy_registry_head.event_hash,
                }
            )
        if receipt_authority_registry_head is not None:
            head_bindings.update(
                {
                    "receipt_authority_registry_sha256": (receipt_authority_registry_head.registry_sha256),
                    "receipt_authority_registry_generation": (receipt_authority_registry_head.generation),
                    "receipt_authority_registry_event_seq": (receipt_authority_registry_head.authority_event_seq),
                    "receipt_authority_registry_event_hash": (receipt_authority_registry_head.event_hash),
                }
            )
        bound = decision.model_copy(update=head_bindings)
        recorded_at_ms = bound.admission_at_ms if bound.admission_at_ms is not None else int(time.time() * 1_000)
        try:
            return _persist_and_emit(
                store=store,
                decision=bound,
                output_path=output_path,
                policy_sha256=policy_sha,
                receipt_sha256=receipt_sha,
                artifact_sha256=artifact_sha,
                recorded_at_ms=recorded_at_ms,
                expected_policy_registry_head=policy_registry_head,
                expected_receipt_authority_registry_head=(receipt_authority_registry_head),
            )
        except ArtifactAdmissionStoreClockRollback:
            try:
                authority_floor = store.authority_time_floor()
            except (ArtifactAdmissionStoreError, OSError, TypeError, ValueError):
                return emit_unrecorded(
                    bound,
                    reason_code="ADMISSION_AUTHORITY_UNAVAILABLE_UNRECORDED",
                    recorded_at_ms=recorded_at_ms,
                )
            failure_recorded_at_ms = max(
                recorded_at_ms,
                int(time.time() * 1_000),
                authority_floor or 0,
            )
            failure = bound.model_copy(
                update={
                    "verdict": "DENY",
                    "reason_code": "ADMISSION_CLOCK_ROLLBACK",
                    "admission_at_ms": failure_recorded_at_ms,
                    "authority_store_kind": "",
                    "authority_store_schema_version": None,
                    "authority_decision_id": "",
                }
            )
            try:
                return _persist_and_emit(
                    store=store,
                    decision=failure,
                    output_path=output_path,
                    policy_sha256=policy_sha,
                    receipt_sha256=receipt_sha,
                    artifact_sha256=artifact_sha,
                    recorded_at_ms=failure_recorded_at_ms,
                    expected_policy_registry_head=None,
                    expected_receipt_authority_registry_head=None,
                )
            except (ArtifactAdmissionStoreError, OSError):
                return emit_unrecorded(
                    failure,
                    reason_code="ADMISSION_AUTHORITY_UNAVAILABLE_UNRECORDED",
                    recorded_at_ms=failure_recorded_at_ms,
                )
            except (TypeError, ValueError):
                return emit_unrecorded(
                    failure,
                    reason_code=("ADMISSION_AUTHORITY_CONTRACT_VIOLATION_UNRECORDED"),
                    recorded_at_ms=failure_recorded_at_ms,
                )
        except (
            ArtifactAdmissionStoreRegistryConflict,
            ArtifactAdmissionStoreRegistryGenerationGap,
            ArtifactAdmissionStoreRegistryHeadChanged,
            ArtifactAdmissionStoreRegistryHeadMismatch,
            ArtifactAdmissionStoreRegistryRollback,
            ArtifactAdmissionStoreRegistryUnprovisioned,
        ) as exc:
            failure_recorded_at_ms = max(
                recorded_at_ms,
                int(time.time() * 1_000),
                getattr(getattr(exc, "actual_head", None), "recorded_at_ms", 0),
            )
            failure = bound.model_copy(
                update={
                    "verdict": "DENY",
                    "reason_code": exc.reason_code,
                    "admission_at_ms": failure_recorded_at_ms,
                    "authority_store_kind": "",
                    "authority_store_schema_version": None,
                    "authority_decision_id": "",
                }
            )
            try:
                return _persist_and_emit(
                    store=store,
                    decision=failure,
                    output_path=output_path,
                    policy_sha256=policy_sha,
                    receipt_sha256=receipt_sha,
                    artifact_sha256=artifact_sha,
                    recorded_at_ms=failure_recorded_at_ms,
                    expected_policy_registry_head=None,
                    expected_receipt_authority_registry_head=None,
                )
            except (ArtifactAdmissionStoreError, OSError):
                return emit_unrecorded(
                    failure,
                    reason_code="ADMISSION_AUTHORITY_UNAVAILABLE_UNRECORDED",
                    recorded_at_ms=failure_recorded_at_ms,
                )
            except (TypeError, ValueError):
                return emit_unrecorded(
                    failure,
                    reason_code=("ADMISSION_AUTHORITY_CONTRACT_VIOLATION_UNRECORDED"),
                    recorded_at_ms=failure_recorded_at_ms,
                )
        except (ArtifactAdmissionStoreError, OSError):
            return emit_unrecorded(
                bound,
                reason_code="ADMISSION_AUTHORITY_UNAVAILABLE_UNRECORDED",
                recorded_at_ms=recorded_at_ms,
            )
        except (TypeError, ValueError):
            return emit_unrecorded(
                bound,
                reason_code="ADMISSION_AUTHORITY_CONTRACT_VIOLATION_UNRECORDED",
                recorded_at_ms=recorded_at_ms,
            )

    try:
        registry_heads = store.registry_heads()
        if POLICY_REGISTRY_KIND not in registry_heads:
            raise ArtifactAdmissionStoreRegistryUnprovisioned("POLICY_REGISTRY_UNPROVISIONED")
        if RECEIPT_AUTHORITY_REGISTRY_KIND not in registry_heads:
            raise ArtifactAdmissionStoreRegistryUnprovisioned("RECEIPT_AUTHORITY_REGISTRY_UNPROVISIONED")
        policy_registry_head = registry_heads[POLICY_REGISTRY_KIND]
        receipt_authority_registry_head = registry_heads[RECEIPT_AUTHORITY_REGISTRY_KIND]
    except ArtifactAdmissionStoreRegistryUnprovisioned as exc:
        return persist(
            denied_admission_decision(
                exc.reason_code,
                verifier_runtime_id=verifier_runtime_id,
            )
        )
    except (ArtifactAdmissionStoreError, OSError, TypeError, ValueError):
        return persist(
            denied_admission_decision(
                "ADMISSION_AUTHORITY_UNAVAILABLE",
                verifier_runtime_id=verifier_runtime_id,
            )
        )
    registry_sha = policy_registry_head.registry_sha256
    policy_registry_generation = policy_registry_head.generation
    receipt_authority_registry_sha = receipt_authority_registry_head.registry_sha256
    receipt_authority_registry_generation = receipt_authority_registry_head.generation
    for registry_path, authority_head, reason_prefix in (
        (policy_registry_path, policy_registry_head, "POLICY_REGISTRY"),
        (
            receipt_authority_registry_path,
            receipt_authority_registry_head,
            "RECEIPT_AUTHORITY_REGISTRY",
        ),
    ):
        mirror_reason = _registry_mirror_reason(
            registry_path=registry_path,
            authority_head=authority_head,
            reason_prefix=reason_prefix,
        )
        if mirror_reason:
            return persist(
                denied_admission_decision(
                    mirror_reason,
                    verifier_runtime_id=verifier_runtime_id,
                )
            )
    try:
        artifact_sha = _sha256_file(Path(args.artifact))
        bundle_bytes = _read_bounded(Path(args.bundle), 16 * 1024 * 1024)
        trusted_root_bytes = _read_bounded(Path(args.trusted_root), 4 * 1024 * 1024)
        policy_bytes = _read_bounded(Path(args.policy), 1024 * 1024)
        policy_sha = _sha256(policy_bytes)
        receipt_bytes = _read_bounded(Path(args.verification_receipt), 1024 * 1024)
        bundle_sha = _sha256(bundle_bytes)
        root_sha = _sha256(trusted_root_bytes)
    except (OSError, ValueError):
        return persist(
            denied_admission_decision(
                "ADMISSION_INPUT_INVALID",
                artifact_sha256=artifact_sha,
                bundle_sha256=bundle_sha,
                trusted_root_sha256=root_sha,
                policy_sha256=policy_sha,
                verifier_runtime_id=verifier_runtime_id,
            )
        )
    try:
        policy = ArtifactAdmissionPolicy.model_validate(load_json_without_duplicate_keys(policy_bytes))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        return persist(
            denied_admission_decision(
                "ADMISSION_POLICY_INVALID",
                artifact_sha256=artifact_sha,
                bundle_sha256=bundle_sha,
                trusted_root_sha256=root_sha,
                policy_sha256=policy_sha,
                verifier_runtime_id=verifier_runtime_id,
            )
        )
    (
        policy_registry_reason,
        registry_sha,
        policy_registry_generation,
    ) = _policy_registry_decision(
        registry_bytes=policy_registry_head.registry_bytes,
        policy=policy,
        policy_bytes=policy_bytes,
    )
    if policy_registry_reason:
        return persist(
            denied_admission_decision(
                policy_registry_reason,
                policy=policy,
                policy_sha256=policy_sha,
                policy_registry_sha256=registry_sha,
                policy_registry_generation=policy_registry_generation,
                verifier_runtime_id=verifier_runtime_id,
                artifact_sha256=artifact_sha,
                bundle_sha256=bundle_sha,
                trusted_root_sha256=root_sha,
            )
        )
    try:
        receipt_envelope = SignedArtifactVerificationReceipt.model_validate(
            load_json_without_duplicate_keys(receipt_bytes)
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        return persist(
            denied_admission_decision(
                "SIGNED_VERIFICATION_RECEIPT_INVALID",
                policy=policy,
                policy_sha256=policy_sha,
                policy_registry_sha256=registry_sha,
                policy_registry_generation=policy_registry_generation,
                verifier_runtime_id=verifier_runtime_id,
                artifact_sha256=artifact_sha,
                bundle_sha256=bundle_sha,
                trusted_root_sha256=root_sha,
            )
        )
    receipt = receipt_envelope.receipt
    receipt_sha = artifact_verification_receipt_sha256(receipt)
    (
        receipt_authority_reason,
        receipt_authority_registry_sha,
        receipt_authority_registry_generation,
        receipt_authority_key,
    ) = _receipt_authority_registry_decision(
        registry_bytes=receipt_authority_registry_head.registry_bytes,
        policy=policy,
    )
    if receipt_authority_reason:
        return persist(
            denied_admission_decision(
                receipt_authority_reason,
                policy=policy,
                policy_sha256=policy_sha,
                policy_registry_sha256=registry_sha,
                policy_registry_generation=policy_registry_generation,
                verifier_runtime_id=verifier_runtime_id,
                artifact_sha256=artifact_sha,
                bundle_sha256=bundle_sha,
                trusted_root_sha256=root_sha,
                receipt=receipt,
                receipt_authority_registry_sha256=(receipt_authority_registry_sha),
                receipt_authority_registry_generation=(receipt_authority_registry_generation),
            )
        )
    if receipt_authority_key is None:
        return persist(
            denied_admission_decision(
                "RECEIPT_AUTHORITY_KEY_NOT_TRUSTED",
                policy=policy,
                policy_sha256=policy_sha,
                policy_registry_sha256=registry_sha,
                policy_registry_generation=policy_registry_generation,
                verifier_runtime_id=verifier_runtime_id,
                artifact_sha256=artifact_sha,
                bundle_sha256=bundle_sha,
                trusted_root_sha256=root_sha,
                receipt=receipt,
                receipt_authority_registry_sha256=(receipt_authority_registry_sha),
                receipt_authority_registry_generation=(receipt_authority_registry_generation),
            )
        )
    try:
        receipt_authentication = verify_signed_artifact_receipt(
            receipt_envelope,
            receipt_authority_key,
            required_authority_id=policy.required_receipt_authority_id,
            required_key_id=policy.required_receipt_key_id,
            required_public_key_sha256=(policy.required_receipt_public_key_sha256),
        )
    except ArtifactReceiptAuthenticationError as exc:
        return persist(
            denied_admission_decision(
                exc.reason_code,
                policy=policy,
                policy_sha256=policy_sha,
                policy_registry_sha256=registry_sha,
                policy_registry_generation=policy_registry_generation,
                verifier_runtime_id=verifier_runtime_id,
                artifact_sha256=artifact_sha,
                bundle_sha256=bundle_sha,
                trusted_root_sha256=root_sha,
                receipt=receipt,
                receipt_authority_registry_sha256=(receipt_authority_registry_sha),
                receipt_authority_registry_generation=(receipt_authority_registry_generation),
            )
        )
    preflight_reason = _preflight_reason(
        policy=policy,
        receipt=receipt,
        artifact_sha256=artifact_sha,
        bundle_sha256=bundle_sha,
        trusted_root_sha256=root_sha,
        verifier_runtime_id=verifier_runtime_id,
    )
    if preflight_reason:
        return persist(
            denied_admission_decision(
                preflight_reason,
                policy=policy,
                policy_sha256=policy_sha,
                policy_registry_sha256=registry_sha,
                policy_registry_generation=policy_registry_generation,
                verifier_runtime_id=verifier_runtime_id,
                artifact_sha256=artifact_sha,
                bundle_sha256=bundle_sha,
                trusted_root_sha256=root_sha,
                receipt=receipt,
                receipt_authentication=receipt_authentication,
                receipt_authority_registry_sha256=(receipt_authority_registry_sha),
                receipt_authority_registry_generation=(receipt_authority_registry_generation),
            )
        )
    try:
        payload_type, payload, verification = _verify_sigstore_dsse(
            bundle_bytes=bundle_bytes,
            trusted_root_bytes=trusted_root_bytes,
            identity=policy.certificate_identity,
            issuer=policy.certificate_oidc_issuer,
            artifact_sha256=artifact_sha,
            bundle_sha256=bundle_sha,
            trusted_root_sha256=root_sha,
        )
    except AdmissionVerificationError as exc:
        return persist(
            denied_admission_decision(
                exc.reason_code,
                policy=policy,
                policy_sha256=policy_sha,
                policy_registry_sha256=registry_sha,
                policy_registry_generation=policy_registry_generation,
                verifier_runtime_id=verifier_runtime_id,
                artifact_sha256=artifact_sha,
                bundle_sha256=bundle_sha,
                trusted_root_sha256=root_sha,
                receipt=receipt,
                receipt_authentication=receipt_authentication,
                receipt_authority_registry_sha256=(receipt_authority_registry_sha),
                receipt_authority_registry_generation=(receipt_authority_registry_generation),
            )
        )
    admission_at_ms = int(time.time() * 1_000)
    try:
        current_registry_heads = store.registry_heads()
        if POLICY_REGISTRY_KIND not in current_registry_heads:
            raise ArtifactAdmissionStoreRegistryUnprovisioned("POLICY_REGISTRY_UNPROVISIONED")
        if RECEIPT_AUTHORITY_REGISTRY_KIND not in current_registry_heads:
            raise ArtifactAdmissionStoreRegistryUnprovisioned("RECEIPT_AUTHORITY_REGISTRY_UNPROVISIONED")
        current_policy_registry_head = current_registry_heads[POLICY_REGISTRY_KIND]
        current_receipt_authority_registry_head = current_registry_heads[RECEIPT_AUTHORITY_REGISTRY_KIND]
    except ArtifactAdmissionStoreRegistryUnprovisioned as exc:
        return persist(
            denied_admission_decision(
                exc.reason_code,
                policy=policy,
                policy_sha256=policy_sha,
                verifier_runtime_id=verifier_runtime_id,
                artifact_sha256=artifact_sha,
                bundle_sha256=bundle_sha,
                trusted_root_sha256=root_sha,
                verification=verification,
                receipt=receipt,
                receipt_authentication=receipt_authentication,
                admission_at_ms=admission_at_ms,
            )
        )
    except (ArtifactAdmissionStoreError, OSError, TypeError, ValueError):
        return persist(
            denied_admission_decision(
                "ADMISSION_AUTHORITY_UNAVAILABLE",
                policy=policy,
                policy_sha256=policy_sha,
                verifier_runtime_id=verifier_runtime_id,
                artifact_sha256=artifact_sha,
                bundle_sha256=bundle_sha,
                trusted_root_sha256=root_sha,
                verification=verification,
                receipt=receipt,
                receipt_authentication=receipt_authentication,
                admission_at_ms=admission_at_ms,
            )
        )
    policy_registry_changed = current_policy_registry_head != policy_registry_head
    receipt_authority_registry_changed = current_receipt_authority_registry_head != receipt_authority_registry_head
    if policy_registry_changed:
        policy_registry_head = current_policy_registry_head
    if receipt_authority_registry_changed:
        receipt_authority_registry_head = current_receipt_authority_registry_head
    if policy_registry_changed or receipt_authority_registry_changed:
        return persist(
            denied_admission_decision(
                (
                    "POLICY_REGISTRY_CHANGED_DURING_ADMISSION"
                    if policy_registry_changed
                    else "RECEIPT_AUTHORITY_REGISTRY_CHANGED_DURING_ADMISSION"
                ),
                policy=policy,
                policy_sha256=policy_sha,
                verifier_runtime_id=verifier_runtime_id,
                artifact_sha256=artifact_sha,
                bundle_sha256=bundle_sha,
                trusted_root_sha256=root_sha,
                verification=verification,
                receipt=receipt,
                receipt_authentication=receipt_authentication,
                admission_at_ms=admission_at_ms,
            )
        )
    for registry_path, authority_head, reason_prefix in (
        (policy_registry_path, policy_registry_head, "POLICY_REGISTRY"),
        (
            receipt_authority_registry_path,
            receipt_authority_registry_head,
            "RECEIPT_AUTHORITY_REGISTRY",
        ),
    ):
        mirror_reason = _registry_mirror_reason(
            registry_path=registry_path,
            authority_head=authority_head,
            reason_prefix=reason_prefix,
        )
        if mirror_reason:
            return persist(
                denied_admission_decision(
                    f"{reason_prefix}_CHANGED_DURING_ADMISSION",
                    policy=policy,
                    policy_sha256=policy_sha,
                    verifier_runtime_id=verifier_runtime_id,
                    artifact_sha256=artifact_sha,
                    bundle_sha256=bundle_sha,
                    trusted_root_sha256=root_sha,
                    verification=verification,
                    receipt=receipt,
                    receipt_authentication=receipt_authentication,
                    admission_at_ms=admission_at_ms,
                )
            )
    registry_sha = policy_registry_head.registry_sha256
    policy_registry_generation = policy_registry_head.generation
    receipt_authority_registry_sha = receipt_authority_registry_head.registry_sha256
    receipt_authority_registry_generation = receipt_authority_registry_head.generation
    decision = evaluate_verified_provenance(
        payload_type=payload_type,
        payload=payload,
        artifact_sha256=artifact_sha,
        bundle_sha256=bundle_sha,
        trusted_root_sha256=root_sha,
        policy_sha256=policy_sha,
        policy_registry_sha256=registry_sha,
        policy_registry_generation=policy_registry_generation,
        policy_registry_event_seq=policy_registry_head.authority_event_seq,
        policy_registry_event_hash=policy_registry_head.event_hash,
        verifier_runtime_id=verifier_runtime_id,
        receipt_authority_registry_sha256=receipt_authority_registry_sha,
        receipt_authority_registry_generation=(receipt_authority_registry_generation),
        receipt_authority_registry_event_seq=(receipt_authority_registry_head.authority_event_seq),
        receipt_authority_registry_event_hash=(receipt_authority_registry_head.event_hash),
        policy=policy,
        verification=verification,
        receipt=receipt,
        receipt_authentication=receipt_authentication,
        admission_at_ms=admission_at_ms,
    )
    return persist(decision)


if __name__ == "__main__":
    raise SystemExit(main())
