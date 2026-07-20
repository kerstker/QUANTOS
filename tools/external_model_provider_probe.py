"""Run a live, redacted external-provider probe through model governance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError

from quantos.contracts.external_model_provider import (
    ExternalModelProviderRegistration,
)
from quantos.contracts.hashing import source_file_sha256, stable_hash
from quantos.contracts.model_risk import (
    ModelCard,
    ModelUseStatus,
    PromptLineageStatus,
    PromptTemplateCard,
    PromptUseStatus,
    ToolManifest,
)
from services.governance.model_risk_registry import (
    GovernedModelRegistry,
    SqliteModelRiskStore,
)
from services.intelligence.external_provider_runtime import (
    EXTERNAL_PROVIDER_PROBE_TEMPLATE,
    GovernedExternalProviderRuntime,
)
from services.intelligence.provider_selection import (
    load_provider_registry,
    select_provider,
)
from services.intelligence.runtime import (
    GovernedModelRuntime,
    RuntimeGovernanceError,
)
from tools.external_model_provider import (
    OpenAICompatibleProvider,
    ProviderCredentialError,
    ProviderResponseError,
    TransportResponse,
    load_api_key,
)


REQUIRED_PROVIDER_RED_TEAM_CASES = (
    "missing-credential",
    "plaintext-endpoint",
    "malformed-response",
    "wrong-model",
    "wrong-echo",
    "missing-tls-certificate",
    "forbidden-runtime-action",
)
_SOURCE_PATHS = (
    "quantos/contracts/external_model_provider.py",
    "services/governance/external_model_provider_evidence.py",
    "services/governance/model_risk_registry.py",
    "services/governance/top_tier_assessment.py",
    "services/intelligence/external_provider_runtime.py",
    "services/intelligence/provider_selection.py",
    "services/intelligence/runtime.py",
    "tools/external_model_provider.py",
    "tools/external_model_provider_probe.py",
)
_VERIFICATION_PATHS = (
    "tests/governance/test_external_model_provider_boundary.py",
    "tests/governance/test_external_model_provider_evidence.py",
    "tests/governance/test_top_tier_assessment.py",
    "tests/intelligence/test_external_provider_runtime.py",
    "tests/tools/test_external_model_provider.py",
)
_REMAINING_BLOCKERS = (
    "CONNECTIVITY_PROBE_ONLY_NO_RESEARCH_DECISION",
    "INDEPENDENT_EXTERNAL_REVIEW_NOT_PROVIDED",
    "PROVIDER_RESPONSE_BODY_NOT_DIGITALLY_SIGNED",
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact_record(artifact: Any) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_type": type(artifact).__name__,
        "content_hash": artifact.content_hash,
        "payload": artifact.model_dump(mode="json"),
    }


class _StaticTransport:
    def __init__(self, response: TransportResponse):
        self._response = response

    def post(self, **_kwargs: Any) -> TransportResponse:
        return self._response


def _response_body(
    registration: ExternalModelProviderRegistration,
    challenge_token: str,
    *,
    model: str | None = None,
    content: str | None = None,
) -> bytes:
    return json.dumps(
        {
            "id": "red-team-response",
            "model": registration.model if model is None else model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": challenge_token if content is None else content,
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _case_result(case_id: str, action: Any, expected: tuple[type[BaseException], ...]) -> dict[str, Any]:
    try:
        action()
    except expected as exc:
        return {
            "case_id": case_id,
            "expected_outcome": "REJECTED",
            "actual_outcome": "REJECTED",
            "passed": True,
            "exception_type": type(exc).__name__,
        }
    except Exception as exc:  # pragma: no cover - retained as explicit fail evidence
        return {
            "case_id": case_id,
            "expected_outcome": "REJECTED",
            "actual_outcome": "WRONG_EXCEPTION",
            "passed": False,
            "exception_type": type(exc).__name__,
        }
    return {
        "case_id": case_id,
        "expected_outcome": "REJECTED",
        "actual_outcome": "ACCEPTED",
        "passed": False,
        "exception_type": "",
    }


def _client_for_response(
    registration: ExternalModelProviderRegistration,
    response: TransportResponse,
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(registration, transport=_StaticTransport(response))


def run_provider_boundary_red_team(
    registration: ExternalModelProviderRegistration,
    runtime: GovernedModelRuntime,
    challenge_token: str,
) -> list[dict[str, Any]]:
    certificate = b"red-team-tls-certificate"
    valid_body = _response_body(registration, challenge_token)
    base = {
        "status": 200,
        "headers": {"x-request-id": "red-team-request"},
        "body": valid_body,
        "tls_peer_cert_der": certificate,
    }
    registration_payload = registration.model_dump(mode="json")
    plaintext_payload = {**registration_payload, "endpoint": "http://vip.sushuo.biz/v1/chat/completions"}
    cases = [
        _case_result(
            "missing-credential",
            lambda: load_api_key(registration, environ={}),
            (ProviderCredentialError,),
        ),
        _case_result(
            "plaintext-endpoint",
            lambda: ExternalModelProviderRegistration.model_validate(plaintext_payload),
            (ValidationError, ValueError),
        ),
        _case_result(
            "malformed-response",
            lambda: _client_for_response(
                registration,
                TransportResponse(**{**base, "body": b"not-json"}),
            ).probe(api_key="red-team-key", challenge_token=challenge_token),
            (ProviderResponseError,),
        ),
        _case_result(
            "wrong-model",
            lambda: _client_for_response(
                registration,
                TransportResponse(
                    **{
                        **base,
                        "body": _response_body(
                            registration,
                            challenge_token,
                            model="unregistered-model",
                        ),
                    }
                ),
            ).probe(api_key="red-team-key", challenge_token=challenge_token),
            (ProviderResponseError,),
        ),
        _case_result(
            "wrong-echo",
            lambda: _client_for_response(
                registration,
                TransportResponse(
                    **{
                        **base,
                        "body": _response_body(
                            registration,
                            challenge_token,
                            content="wrong",
                        ),
                    }
                ),
            ).probe(api_key="red-team-key", challenge_token=challenge_token),
            (ProviderResponseError,),
        ),
        _case_result(
            "missing-tls-certificate",
            lambda: _client_for_response(
                registration,
                TransportResponse(**{**base, "tls_peer_cert_der": b""}),
            ).probe(api_key="red-team-key", challenge_token=challenge_token),
            (ProviderResponseError,),
        ),
        _case_result(
            "forbidden-runtime-action",
            lambda: runtime.authorize("LIVE_TRADE"),
            (RuntimeGovernanceError,),
        ),
    ]
    return cases


def build_external_model_provider_evidence(
    *,
    repo_root: str | Path,
    provider_registry_path: str | Path,
    registration: ExternalModelProviderRegistration,
    api_key: str,
    client: OpenAICompatibleProvider,
    challenge_token: str,
    recorded_at: int,
    key_source: str,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    registry_path = Path(provider_registry_path).resolve()
    try:
        registry_ref = registry_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("provider registry must stay inside the repository") from exc
    if registry_ref != "config/model_provider_registry.json":
        raise ValueError("provider registry path is not canonical")
    if key_source not in {"EXPLICIT_FILE", "REGISTERED_ENVIRONMENT"}:
        raise ValueError("provider key source is not admissible")
    if recorded_at < 0:
        raise ValueError("provider evidence timestamp must be non-negative")
    if client.registration != registration:
        raise ValueError("provider client and registration do not match")

    source_hashes = {path: source_file_sha256(root / path) for path in _SOURCE_PATHS}
    verification_hashes = {path: source_file_sha256(root / path) for path in _VERIFICATION_PATHS}
    provider_registry = load_provider_registry(registry_path)
    canonical_registration = select_provider(
        provider_registry,
        requested_provider_id=registration.provider_id,
        environ={},
    )
    if canonical_registration != registration:
        raise ValueError("provider registration does not match the canonical registry")

    manifest = ToolManifest(
        manifest_id="external-provider-tools",
        version="v1",
        implementation_ref="services/intelligence/external_provider_runtime.py",
        implementation_sha256=source_hashes["services/intelligence/external_provider_runtime.py"],
        allowed_actions=registration.allowed_actions,
        prohibited_actions=registration.prohibited_actions,
        recorded_at=recorded_at,
    )
    model = ModelCard(
        model_id="sushuo-gpt-5-6-sol",
        version="v1",
        owner_group="external-model-governance",
        model_type="OPENAI_COMPATIBLE_EXTERNAL_PROVIDER",
        implementation_ref="tools/external_model_provider.py",
        implementation_sha256=source_hashes["tools/external_model_provider.py"],
        intended_use="research-only external provider evaluation",
        prohibited_uses=("LIVE_TRADING", "ORDER_SUBMISSION", "CREDENTIAL_ACCESS"),
        prompt_lineage_status=PromptLineageStatus.REGISTERED,
        prompt_template_ref=registration.runtime_prompt_ref,
        tool_manifest_ref=registration.runtime_tool_manifest_ref,
        status=ModelUseStatus.RESEARCH_ONLY,
        limitations=(
            "TLS-authenticated provider response body is not digitally signed",
            "connectivity probe carries no alpha or trading data",
        ),
        recorded_at=recorded_at,
    )
    prompt = PromptTemplateCard(
        prompt_id="external-provider-probe",
        version="v1",
        owner_group="external-model-governance",
        template_ref=("services/intelligence/external_provider_runtime.py#EXTERNAL_PROVIDER_PROBE_TEMPLATE"),
        template_sha256=_sha256_text(EXTERNAL_PROVIDER_PROBE_TEMPLATE),
        model_ref=model.model_ref,
        intended_use="credential-free echo connectivity probe",
        prohibited_uses=("ALPHA_DISCLOSURE", "LIVE_TRADING", "ORDER_SUBMISSION"),
        tool_manifest_ref=manifest.manifest_ref,
        status=PromptUseStatus.RESEARCH_ONLY,
        recorded_at=recorded_at,
    )

    with tempfile.TemporaryDirectory(prefix="quantos-external-provider-") as directory:
        store = SqliteModelRiskStore(str(Path(directory) / "provider.db"), create=True)
        try:
            governed_registry = GovernedModelRegistry(store)
            for artifact in (model, prompt, manifest):
                if not governed_registry.record_artifact(artifact):
                    raise ValueError("external provider runtime artifact was not new")
            governed_runtime = GovernedModelRuntime(
                governed_registry,
                model_ref=model.model_ref,
                prompt_ref=prompt.prompt_ref,
                tool_manifest_ref=manifest.manifest_ref,
                prompt_template=EXTERNAL_PROVIDER_PROBE_TEMPLATE,
            )
            provider_runtime = GovernedExternalProviderRuntime(
                governed_runtime,
                registration,
            )
            result = provider_runtime.invoke_probe(
                invocation_id=f"external-provider-probe-{recorded_at}",
                challenge_token=challenge_token,
                execute=lambda challenge: client.probe(
                    api_key=api_key,
                    challenge_token=challenge,
                ),
                recorded_at=recorded_at,
            )
            red_team_cases = run_provider_boundary_red_team(
                registration,
                governed_runtime,
                challenge_token,
            )
            if not all(case["passed"] for case in red_team_cases):
                raise ValueError("external provider boundary red-team failed")
            if not governed_registry.verify_all() or not governed_registry.verify_runtime_history():
                raise ValueError("external provider registry verification failed")
            row = governed_registry.runtime_rows()[0]
            runtime_event = {
                "sequence": row["sequence"],
                "invocation_id": row["invocation_id"],
                "recorded_at": row["recorded_at"],
                "model_ref": row["model_ref"],
                "prompt_ref": row["prompt_ref"],
                "tool_manifest_ref": row["tool_manifest_ref"],
                "action": row["action"],
                "previous_hash": row["previous_hash"],
                "event_hash": row["event_hash"],
                "payload": json.loads(row["payload_json"]),
            }
            runtime_history_sha256 = governed_registry.runtime_history_hash()
        finally:
            store.close()

    receipt = result.value["receipt"]
    runtime_input = {
        "purpose": "EXTERNAL_PROVIDER_CONNECTIVITY_PROBE",
        "provider_id": registration.provider_id,
        "provider_registration_sha256": registration.registration_sha256,
        "endpoint_sha256": _sha256_text(registration.endpoint),
        "model": registration.model,
        "challenge_sha256": _sha256_text(challenge_token),
    }
    document: dict[str, Any] = {
        "schema_version": 1,
        "mode": "GOVERNED_EXTERNAL_MODEL_PROVIDER_PROBE",
        "verdict": "PASS",
        "current_maturity": "M3",
        "recorded_at": recorded_at,
        "provider_registry_ref": registry_ref,
        "provider_registry_file_sha256": source_file_sha256(registry_path),
        "provider_registry_sha256": provider_registry.registry_sha256,
        "provider_id": registration.provider_id,
        "provider_registration_sha256": registration.registration_sha256,
        "transport": registration.transport.value,
        "endpoint_sha256": _sha256_text(registration.endpoint),
        "requested_model": registration.model,
        "response_authentication": "TLS_SERVER_AUTH_ONLY_UNSIGNED_BODY",
        "external_provider_exercised": True,
        "key_source": key_source,
        "challenge_token": challenge_token,
        "probe_receipt": receipt,
        "runtime_input": runtime_input,
        "runtime_output": result.value,
        "runtime_event": runtime_event,
        "runtime_history_sha256": runtime_history_sha256,
        "runtime_history_verified": True,
        "registry_verified": True,
        "artifacts": [_artifact_record(item) for item in (model, prompt, manifest)],
        "artifact_count": 3,
        "required_red_team_case_ids": list(REQUIRED_PROVIDER_RED_TEAM_CASES),
        "red_team_cases": red_team_cases,
        "source_sha256": source_hashes,
        "verification_sha256": verification_hashes,
        "secrets_persisted": False,
        "raw_body_persisted": False,
        "model_content_persisted": False,
        "execution_authority_granted": False,
        "promotion_authority_granted": False,
        "credential_authority_granted": False,
        "live_trading_authority_granted": False,
        "review_identity_assurance": "PROCESS_ROLE_ONLY",
        "remaining_blockers": list(_REMAINING_BLOCKERS),
    }
    document["self_sha256"] = stable_hash(document)
    serialized = json.dumps(document, sort_keys=True)
    if api_key in serialized or "Bearer " in serialized:
        raise ValueError("provider credential entered versioned evidence")
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument(
        "--registry",
        default="config/model_provider_registry.json",
    )
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--key-file")
    parser.add_argument(
        "--output",
        default="evidence/external_model_provider_baseline.json",
    )
    parser.add_argument("--recorded-at", type=int)
    parser.add_argument("--challenge-token")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    registry_path = (root / args.registry).resolve()
    provider_registry = load_provider_registry(registry_path)
    registration = select_provider(
        provider_registry,
        requested_provider_id=args.provider_id,
    )
    environment_key = str(os.environ.get(registration.api_key_env, "")).strip()
    key_source = "REGISTERED_ENVIRONMENT" if environment_key else "EXPLICIT_FILE"
    api_key = load_api_key(
        registration,
        key_file=args.key_file,
    )
    challenge_token = args.challenge_token or ("QUANTOS_PROVIDER_PROBE_" + secrets.token_hex(16).upper())
    recorded_at = int(time.time()) if args.recorded_at is None else args.recorded_at
    client = OpenAICompatibleProvider(registration)
    document = build_external_model_provider_evidence(
        repo_root=root,
        provider_registry_path=registry_path,
        registration=registration,
        api_key=api_key,
        client=client,
        challenge_token=challenge_token,
        recorded_at=recorded_at,
        key_source=key_source,
    )
    from services.governance.external_model_provider_evidence import (
        verify_external_model_provider_evidence,
    )

    summary = verify_external_model_provider_evidence(document, repo_root=root)
    output = (root / args.output).resolve()
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise ValueError("external provider evidence output must stay in repository") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "provider_id": summary["provider_id"],
                "current_maturity": summary["current_maturity"],
                "external_provider_exercised": summary["external_provider_exercised"],
                "output": output.relative_to(root).as_posix(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "REQUIRED_PROVIDER_RED_TEAM_CASES",
    "build_external_model_provider_evidence",
    "main",
    "run_provider_boundary_red_team",
]
