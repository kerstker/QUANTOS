"""Network-free bridge from an external provider receipt to the governed runtime."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import re

from quantos.contracts.external_model_provider import (
    EXTERNAL_PROVIDER_PROBE_TEMPLATE,
    ExternalModelProviderRegistration,
    ExternalProviderProbeReceipt,
    ProviderTransport,
)
from services.intelligence.runtime import GovernedModelRuntime, RuntimeResult


_CHALLENGE = re.compile(r"^QUANTOS_PROVIDER_PROBE_[A-F0-9]{32}$")


class ExternalProviderRuntimeError(ValueError):
    """External provider output is not bound to its registered runtime input."""


def render_external_provider_probe_prompt(challenge_token: str) -> str:
    if not isinstance(challenge_token, str) or _CHALLENGE.fullmatch(challenge_token) is None:
        raise ExternalProviderRuntimeError("provider challenge token is invalid")
    return EXTERNAL_PROVIDER_PROBE_TEMPLATE.format(challenge_token=challenge_token)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class GovernedExternalProviderRuntime:
    def __init__(
        self,
        runtime: GovernedModelRuntime,
        registration: ExternalModelProviderRegistration,
    ) -> None:
        if not isinstance(runtime, GovernedModelRuntime):
            raise TypeError("external provider bridge requires GovernedModelRuntime")
        if not isinstance(registration, ExternalModelProviderRegistration):
            raise TypeError("external provider bridge requires canonical registration")
        if registration.transport != ProviderTransport.OPENAI_COMPATIBLE_HTTPS:
            raise ExternalProviderRuntimeError("external provider bridge requires HTTPS transport")
        if (
            runtime.model.model_ref != registration.runtime_model_ref
            or runtime.prompt.prompt_ref != registration.runtime_prompt_ref
            or runtime.manifest.manifest_ref != registration.runtime_tool_manifest_ref
        ):
            raise ExternalProviderRuntimeError("provider registration and runtime lineage do not match")
        self._runtime = runtime
        self.registration = registration

    def _validate_receipt(
        self,
        receipt: ExternalProviderProbeReceipt,
        challenge_token: str,
    ) -> dict[str, object]:
        if not isinstance(receipt, ExternalProviderProbeReceipt):
            raise ExternalProviderRuntimeError("provider returned no canonical probe receipt")
        challenge_sha256 = _sha256_text(challenge_token)
        if (
            receipt.provider_id != self.registration.provider_id
            or receipt.endpoint_sha256 != _sha256_text(self.registration.endpoint)
            or receipt.requested_model != self.registration.model
            or receipt.response_model != self.registration.model
            or receipt.challenge_sha256 != challenge_sha256
            or receipt.content_sha256 != challenge_sha256
            or receipt.tls_peer_cert_sha256 == "0" * 64
            or receipt.finish_reason != "stop"
        ):
            raise ExternalProviderRuntimeError("provider receipt is not bound to the registered probe")
        return {
            "action": "EVALUATE",
            "provider_id": self.registration.provider_id,
            "receipt": receipt.model_dump(mode="json"),
        }

    def invoke_probe(
        self,
        *,
        invocation_id: str,
        challenge_token: str,
        execute: Callable[[str], ExternalProviderProbeReceipt],
        recorded_at: int,
    ) -> RuntimeResult:
        render_external_provider_probe_prompt(challenge_token)
        self._runtime.authorize("EVALUATE")
        if not callable(execute):
            raise TypeError("external provider execute boundary must be callable")
        receipt = execute(challenge_token)
        value = self._validate_receipt(receipt, challenge_token)
        input_payload = {
            "purpose": "EXTERNAL_PROVIDER_CONNECTIVITY_PROBE",
            "provider_id": self.registration.provider_id,
            "provider_registration_sha256": self.registration.registration_sha256,
            "endpoint_sha256": _sha256_text(self.registration.endpoint),
            "model": self.registration.model,
            "challenge_sha256": _sha256_text(challenge_token),
        }
        return self._runtime.invoke(
            invocation_id=invocation_id,
            action="EVALUATE",
            input_payload=input_payload,
            execute=lambda _payload: value,
            recorded_at=recorded_at,
        )


__all__ = [
    "EXTERNAL_PROVIDER_PROBE_TEMPLATE",
    "ExternalProviderRuntimeError",
    "GovernedExternalProviderRuntime",
    "render_external_provider_probe_prompt",
]
