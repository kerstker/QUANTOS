"""Independent replay verifier for retained external model-provider evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from pydantic import ValidationError

from quantos.contracts.external_model_provider import (
    EXTERNAL_PROVIDER_PROBE_TEMPLATE,
    ExternalModelProviderRegistry,
    ExternalProviderProbeReceipt,
)
from quantos.contracts.hashing import source_file_sha256, stable_hash
from quantos.contracts.model_risk import (
    ModelCard,
    ModelUseStatus,
    PromptLineageStatus,
    PromptTemplateCard,
    PromptUseStatus,
    RuntimeInvocation,
    RuntimeInvocationOutcome,
    ToolManifest,
)


class ExternalModelProviderEvidenceError(ValueError):
    """Retained provider evidence is incomplete, stale, or tampered."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHALLENGE = re.compile(r"^QUANTOS_PROVIDER_PROBE_[A-F0-9]{32}$")
_SOURCE_PATHS = {
    "quantos/contracts/external_model_provider.py",
    "services/governance/external_model_provider_evidence.py",
    "services/governance/model_risk_registry.py",
    "services/governance/top_tier_assessment.py",
    "services/intelligence/external_provider_runtime.py",
    "services/intelligence/provider_selection.py",
    "services/intelligence/runtime.py",
    "tools/external_model_provider.py",
    "tools/external_model_provider_probe.py",
}
_VERIFICATION_PATHS = {
    "tests/governance/test_external_model_provider_boundary.py",
    "tests/governance/test_external_model_provider_evidence.py",
    "tests/governance/test_top_tier_assessment.py",
    "tests/intelligence/test_external_provider_runtime.py",
    "tests/tools/test_external_model_provider.py",
}
_REQUIRED_RED_TEAM_CASES = {
    "missing-credential",
    "plaintext-endpoint",
    "malformed-response",
    "wrong-model",
    "wrong-echo",
    "missing-tls-certificate",
    "forbidden-runtime-action",
}
_REMAINING_BLOCKERS = [
    "CONNECTIVITY_PROBE_ONLY_NO_RESEARCH_DECISION",
    "INDEPENDENT_EXTERNAL_REVIEW_NOT_PROVIDED",
    "PROVIDER_RESPONSE_BODY_NOT_DIGITALLY_SIGNED",
]
_EXPECTED_KEYS = {
    "schema_version",
    "mode",
    "verdict",
    "current_maturity",
    "recorded_at",
    "provider_registry_ref",
    "provider_registry_file_sha256",
    "provider_registry_sha256",
    "provider_id",
    "provider_registration_sha256",
    "transport",
    "endpoint_sha256",
    "requested_model",
    "response_authentication",
    "external_provider_exercised",
    "key_source",
    "challenge_token",
    "probe_receipt",
    "runtime_input",
    "runtime_output",
    "runtime_event",
    "runtime_history_sha256",
    "runtime_history_verified",
    "registry_verified",
    "artifacts",
    "artifact_count",
    "required_red_team_case_ids",
    "red_team_cases",
    "source_sha256",
    "verification_sha256",
    "secrets_persisted",
    "raw_body_persisted",
    "model_content_persisted",
    "execution_authority_granted",
    "promotion_authority_granted",
    "credential_authority_granted",
    "live_trading_authority_granted",
    "review_identity_assurance",
    "remaining_blockers",
    "self_sha256",
}


def _fail(detail: str) -> None:
    raise ExternalModelProviderEvidenceError(detail)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_hash_map(
    document: object,
    *,
    required_paths: set[str],
    repo_root: Path,
    label: str,
) -> None:
    if not isinstance(document, dict) or set(document) != required_paths:
        _fail(f"external provider {label} hash schema is invalid")
    for relative, expected in document.items():
        candidate = Path(relative)
        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or not isinstance(expected, str)
            or _SHA256.fullmatch(expected) is None
        ):
            _fail(f"external provider {label} hash entry is invalid")
        try:
            actual = source_file_sha256(repo_root / candidate)
        except OSError as exc:
            raise ExternalModelProviderEvidenceError(f"external provider {label} source is missing") from exc
        if actual != expected:
            _fail(f"external provider {label} source hash does not match")


def _validate_artifacts(
    records: object,
    *,
    repo_root: Path,
    registration: Any,
    recorded_at: int,
) -> None:
    if not isinstance(records, list) or len(records) != 3:
        _fail("external provider artifact schema is invalid")
    types = {
        "ModelCard": ModelCard,
        "PromptTemplateCard": PromptTemplateCard,
        "ToolManifest": ToolManifest,
    }
    loaded: dict[str, Any] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "artifact_id",
            "artifact_type",
            "content_hash",
            "payload",
        }:
            _fail("external provider artifact record schema is invalid")
        model_type = types.get(record.get("artifact_type"))
        payload = record.get("payload")
        if model_type is None or not isinstance(payload, dict):
            _fail("external provider artifact type is invalid")
        content_hash = record.get("content_hash")
        if (
            not isinstance(content_hash, str)
            or _SHA256.fullmatch(content_hash) is None
            or stable_hash(payload) != content_hash
        ):
            _fail("external provider artifact hash is invalid")
        try:
            artifact = model_type.model_validate(payload)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ExternalModelProviderEvidenceError("external provider artifact contract is invalid") from exc
        if (
            artifact.artifact_id != record.get("artifact_id")
            or artifact.content_hash != content_hash
            or artifact.recorded_at != recorded_at
        ):
            _fail("external provider artifact identity is invalid")
        loaded[record["artifact_type"]] = artifact

    if set(loaded) != set(types):
        _fail("external provider artifact set is invalid")
    model = loaded["ModelCard"]
    prompt = loaded["PromptTemplateCard"]
    manifest = loaded["ToolManifest"]
    if (
        model.model_ref != registration.runtime_model_ref
        or model.model_type != "OPENAI_COMPATIBLE_EXTERNAL_PROVIDER"
        or model.status != ModelUseStatus.RESEARCH_ONLY
        or model.prompt_lineage_status != PromptLineageStatus.REGISTERED
        or model.prompt_template_ref != registration.runtime_prompt_ref
        or model.tool_manifest_ref != registration.runtime_tool_manifest_ref
        or model.implementation_ref != "tools/external_model_provider.py"
        or model.implementation_sha256 != source_file_sha256(repo_root / model.implementation_ref)
        or prompt.prompt_ref != registration.runtime_prompt_ref
        or prompt.model_ref != registration.runtime_model_ref
        or prompt.tool_manifest_ref != registration.runtime_tool_manifest_ref
        or prompt.status != PromptUseStatus.RESEARCH_ONLY
        or prompt.template_ref != "services/intelligence/external_provider_runtime.py#EXTERNAL_PROVIDER_PROBE_TEMPLATE"
        or prompt.template_sha256 != _sha256_text(EXTERNAL_PROVIDER_PROBE_TEMPLATE)
        or manifest.manifest_ref != registration.runtime_tool_manifest_ref
        or manifest.allowed_actions != registration.allowed_actions
        or manifest.prohibited_actions != registration.prohibited_actions
        or manifest.implementation_ref != "services/intelligence/external_provider_runtime.py"
        or manifest.implementation_sha256 != source_file_sha256(repo_root / manifest.implementation_ref)
    ):
        _fail("external provider artifact lineage is invalid")


def verify_external_model_provider_evidence(
    document: Mapping[str, Any] | object,
    *,
    repo_root: str | Path,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    if not isinstance(document, Mapping) or set(document) != _EXPECTED_KEYS:
        _fail("external provider evidence schema is invalid")
    payload = dict(document)
    self_sha256 = payload.pop("self_sha256", None)
    if (
        not isinstance(self_sha256, str)
        or _SHA256.fullmatch(self_sha256) is None
        or stable_hash(payload) != self_sha256
    ):
        _fail("external provider evidence self hash is invalid")
    serialized = json.dumps(document, sort_keys=True)
    if any(
        forbidden in serialized
        for forbidden in (
            "Authorization",
            "Bearer ",
            '"api_key"',
            '"key_path"',
            '"raw_response"',
            '"response_content"',
        )
    ):
        _fail("external provider evidence contains forbidden secret or raw data fields")
    required = {
        "schema_version": 1,
        "mode": "GOVERNED_EXTERNAL_MODEL_PROVIDER_PROBE",
        "verdict": "PASS",
        "current_maturity": "M3",
        "provider_registry_ref": "config/model_provider_registry.json",
        "provider_id": "sushuo_gpt_5_6_sol",
        "transport": "OPENAI_COMPATIBLE_HTTPS",
        "requested_model": "gpt-5.6-sol",
        "response_authentication": "TLS_SERVER_AUTH_ONLY_UNSIGNED_BODY",
        "external_provider_exercised": True,
        "runtime_history_verified": True,
        "registry_verified": True,
        "artifact_count": 3,
        "secrets_persisted": False,
        "raw_body_persisted": False,
        "model_content_persisted": False,
        "execution_authority_granted": False,
        "promotion_authority_granted": False,
        "credential_authority_granted": False,
        "live_trading_authority_granted": False,
        "review_identity_assurance": "PROCESS_ROLE_ONLY",
        "remaining_blockers": _REMAINING_BLOCKERS,
    }
    if any(document.get(key) != value for key, value in required.items()):
        _fail("external provider evidence required fields are invalid")
    if document.get("key_source") not in {"EXPLICIT_FILE", "REGISTERED_ENVIRONMENT"}:
        _fail("external provider evidence key source is invalid")
    recorded_at = document.get("recorded_at")
    if type(recorded_at) is not int or recorded_at < 0:
        _fail("external provider evidence timestamp is invalid")

    registry_ref = Path(str(document["provider_registry_ref"]))
    registry_path = root / registry_ref
    try:
        registry_file_sha256 = source_file_sha256(registry_path)
        provider_registry = ExternalModelProviderRegistry.model_validate(
            json.loads(registry_path.read_text(encoding="utf-8"))
        )
        matches = [
            item
            for item in provider_registry.providers
            if item.provider_id == document["provider_id"] and item.enabled
        ]
        if len(matches) != 1:
            _fail("external provider registry selection is invalid")
        registration = matches[0]
    except (
        OSError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise ExternalModelProviderEvidenceError("external provider registry verification failed") from exc
    if (
        document.get("provider_registry_file_sha256") != registry_file_sha256
        or document.get("provider_registry_sha256") != provider_registry.registry_sha256
        or document.get("provider_registration_sha256") != registration.registration_sha256
        or document.get("endpoint_sha256") != _sha256_text(registration.endpoint)
        or document.get("requested_model") != registration.model
    ):
        _fail("external provider registry binding is invalid")

    challenge = document.get("challenge_token")
    if not isinstance(challenge, str) or _CHALLENGE.fullmatch(challenge) is None:
        _fail("external provider challenge is invalid")
    challenge_sha256 = _sha256_text(challenge)
    request_body = json.dumps(
        {
            "messages": [
                {
                    "role": "user",
                    "content": EXTERNAL_PROVIDER_PROBE_TEMPLATE.format(challenge_token=challenge),
                }
            ],
            "model": registration.model,
            "temperature": 0.0,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        receipt = ExternalProviderProbeReceipt.model_validate(document.get("probe_receipt"))
    except (ValidationError, TypeError, ValueError) as exc:
        raise ExternalModelProviderEvidenceError("external provider probe receipt is invalid") from exc
    if (
        receipt.provider_id != registration.provider_id
        or receipt.endpoint_sha256 != _sha256_text(registration.endpoint)
        or receipt.requested_model != registration.model
        or receipt.response_model != registration.model
        or receipt.challenge_sha256 != challenge_sha256
        or receipt.content_sha256 != challenge_sha256
        or receipt.request_body_sha256 != hashlib.sha256(request_body).hexdigest()
        or receipt.tls_peer_cert_sha256 == "0" * 64
        or receipt.finish_reason != "stop"
    ):
        _fail("external provider probe receipt binding is invalid")

    expected_runtime_input = {
        "purpose": "EXTERNAL_PROVIDER_CONNECTIVITY_PROBE",
        "provider_id": registration.provider_id,
        "provider_registration_sha256": registration.registration_sha256,
        "endpoint_sha256": _sha256_text(registration.endpoint),
        "model": registration.model,
        "challenge_sha256": challenge_sha256,
    }
    expected_runtime_output = {
        "action": "EVALUATE",
        "provider_id": registration.provider_id,
        "receipt": receipt.model_dump(mode="json"),
    }
    if (
        document.get("runtime_input") != expected_runtime_input
        or document.get("runtime_output") != expected_runtime_output
    ):
        _fail("external provider runtime input or output is invalid")
    event = document.get("runtime_event")
    if not isinstance(event, dict) or set(event) != {
        "sequence",
        "invocation_id",
        "recorded_at",
        "model_ref",
        "prompt_ref",
        "tool_manifest_ref",
        "action",
        "previous_hash",
        "event_hash",
        "payload",
    }:
        _fail("external provider runtime event schema is invalid")
    try:
        invocation = RuntimeInvocation.model_validate(event.get("payload"))
    except (ValidationError, TypeError, ValueError) as exc:
        raise ExternalModelProviderEvidenceError("external provider runtime invocation is invalid") from exc
    expected_event_hash = stable_hash({"previous_hash": "", "invocation": invocation.model_dump(mode="json")})
    if (
        event.get("sequence") != 1
        or event.get("invocation_id") != f"external-provider-probe-{recorded_at}"
        or event.get("invocation_id") != invocation.invocation_id
        or event.get("recorded_at") != recorded_at
        or event.get("recorded_at") != invocation.recorded_at
        or event.get("model_ref") != registration.runtime_model_ref
        or event.get("model_ref") != invocation.model_ref
        or event.get("prompt_ref") != registration.runtime_prompt_ref
        or event.get("prompt_ref") != invocation.prompt_ref
        or event.get("tool_manifest_ref") != registration.runtime_tool_manifest_ref
        or event.get("tool_manifest_ref") != invocation.tool_manifest_ref
        or event.get("action") != "EVALUATE"
        or invocation.action != "EVALUATE"
        or invocation.outcome != RuntimeInvocationOutcome.ALLOWED
        or invocation.input_sha256 != stable_hash(expected_runtime_input)
        or invocation.output_sha256 != stable_hash(expected_runtime_output)
        or event.get("previous_hash") != ""
        or event.get("event_hash") != expected_event_hash
        or document.get("runtime_history_sha256") != expected_event_hash
    ):
        _fail("external provider runtime event lineage is invalid")

    _validate_artifacts(
        document.get("artifacts"),
        repo_root=root,
        registration=registration,
        recorded_at=recorded_at,
    )
    required_cases = document.get("required_red_team_case_ids")
    red_team_cases = document.get("red_team_cases")
    if (
        not isinstance(required_cases, list)
        or set(required_cases) != _REQUIRED_RED_TEAM_CASES
        or len(required_cases) != len(_REQUIRED_RED_TEAM_CASES)
        or not isinstance(red_team_cases, list)
        or len(red_team_cases) != len(_REQUIRED_RED_TEAM_CASES)
        or {case.get("case_id") for case in red_team_cases if isinstance(case, dict)} != _REQUIRED_RED_TEAM_CASES
    ):
        _fail("external provider red-team schema is invalid")
    for case in red_team_cases:
        if not isinstance(case, dict) or set(case) != {
            "case_id",
            "expected_outcome",
            "actual_outcome",
            "passed",
            "exception_type",
        }:
            _fail("external provider red-team case schema is invalid")
        if (
            case["expected_outcome"] != "REJECTED"
            or case["actual_outcome"] != "REJECTED"
            or case["passed"] is not True
            or not isinstance(case["exception_type"], str)
            or not case["exception_type"]
        ):
            _fail("external provider red-team case did not pass")

    _validate_hash_map(
        document.get("source_sha256"),
        required_paths=_SOURCE_PATHS,
        repo_root=root,
        label="source",
    )
    _validate_hash_map(
        document.get("verification_sha256"),
        required_paths=_VERIFICATION_PATHS,
        repo_root=root,
        label="verification",
    )
    return {
        "provider_id": registration.provider_id,
        "current_maturity": "M3",
        "external_provider_exercised": True,
        "response_authentication": document["response_authentication"],
        "review_identity_assurance": document["review_identity_assurance"],
        "remaining_blockers": list(_REMAINING_BLOCKERS),
        "evidence_paths": [
            document["provider_registry_ref"],
            *sorted(_SOURCE_PATHS),
            *sorted(_VERIFICATION_PATHS),
        ],
    }


def load_and_verify_external_model_provider_evidence(
    path: str | Path,
    *,
    repo_root: str | Path,
) -> dict[str, Any]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExternalModelProviderEvidenceError("external provider evidence is unavailable or invalid JSON") from exc
    return verify_external_model_provider_evidence(document, repo_root=repo_root)


__all__ = [
    "ExternalModelProviderEvidenceError",
    "load_and_verify_external_model_provider_evidence",
    "verify_external_model_provider_evidence",
]
