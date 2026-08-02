"""Fail-closed runtime boundary for registered research models and prompts.

This module executes only caller-supplied offline compute. It has no model SDK,
network, credential, broker, promotion, or order surface. A call is permitted
only when its model, exact prompt text, and tool manifest are immutable registry
artifacts with mutually consistent lineage.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
from typing import Any, Protocol

from pydantic import ValidationError

from quantos.contracts.hashing import stable_hash
from quantos.contracts.model_risk import (
    ExternalProviderBinding,
    ExternalProviderBindingStatus,
    ExternalProviderResponse,
    ModelCard,
    ModelUseStatus,
    PromptLineageStatus,
    PromptTemplateCard,
    PromptUseStatus,
    RuntimeInvocation,
    RuntimeInvocationOutcome,
    ToolManifest,
)
from services.governance.model_risk_registry import (
    GovernedModelRegistry,
    ModelRiskStoreError,
)


RESEARCH_EVALUATION_TEMPLATE = (
    "Evaluate the supplied alpha candidate using only registered point-in-time "
    "evidence and return a structured research-only result."
)

_FORBIDDEN_ACTIONS = frozenset(
    {
        "APPROVE",
        "RESET_KILL_SWITCH",
        "LIVE_TRADE",
        "ORDER_SUBMISSION",
        "ACCESS_CREDENTIALS",
    }
)
_SENSITIVE_OUTPUT_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "broker",
        "credential",
        "credentials",
        "order",
        "order_intent",
        "orders",
        "secret",
    }
)


class RuntimeGovernanceError(PermissionError):
    """The invocation cannot prove registered research-only lineage."""


class RuntimeExecutionError(RuntimeError):
    """Registered offline compute failed before an invocation could be recorded."""


class ExternalModelProvider(Protocol):
    def invoke(self, request: Mapping[str, Any]) -> ExternalProviderResponse:
        """Return a provider response bound to the canonical request digest."""


class ExternalProviderVerifier(Protocol):
    def verify(
        self,
        *,
        binding: ExternalProviderBinding,
        response: ExternalProviderResponse,
    ) -> bool:
        """Verify an externally produced response receipt without exposing secrets."""


class DenyExternalProviderVerifier:
    def verify(
        self,
        *,
        binding: ExternalProviderBinding,
        response: ExternalProviderResponse,
    ) -> bool:
        del binding, response
        return False


@dataclass(frozen=True)
class RuntimeResult:
    value: Any
    invocation: RuntimeInvocation
    event_hash: str


def _artifact_id(ref: str, prefix: str) -> str:
    name, separator, version = ref.rpartition("@")
    if not separator or not name or not version:
        raise RuntimeGovernanceError(f"invalid registered reference: {ref!r}")
    return f"{prefix}:{name}:{version}"


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).strip().lower() in _SENSITIVE_OUTPUT_KEYS:
                return True
            if _contains_sensitive_key(nested):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(item) for item in value)
    return False


class GovernedModelRuntime:
    def __init__(
        self,
        registry: GovernedModelRegistry,
        *,
        model_ref: str,
        prompt_ref: str,
        tool_manifest_ref: str,
        prompt_template: str,
    ) -> None:
        if not isinstance(registry, GovernedModelRegistry):
            raise TypeError("runtime requires a governed model registry")
        self._registry = registry
        self._model_id = _artifact_id(model_ref, "model-card")
        self._prompt_id = _artifact_id(prompt_ref, "prompt-card")
        self._manifest_id = _artifact_id(tool_manifest_ref, "tool-manifest")
        self._prompt_template = prompt_template
        self.model = self._load(self._model_id, ModelCard)
        self.prompt = self._load(self._prompt_id, PromptTemplateCard)
        self.manifest = self._load(self._manifest_id, ToolManifest)
        self._assert_lineage()

    def _load(self, artifact_id: str, expected_type: type):
        try:
            artifact = self._registry.load_artifact(artifact_id)
        except ModelRiskStoreError as exc:
            raise RuntimeGovernanceError(
                f"registered runtime artifact is unavailable: {artifact_id}"
            ) from exc
        if not isinstance(artifact, expected_type):
            raise RuntimeGovernanceError(
                f"registered runtime artifact has the wrong type: {artifact_id}"
            )
        return artifact

    def _assert_lineage(self) -> None:
        if self.model.status != ModelUseStatus.RESEARCH_ONLY:
            raise RuntimeGovernanceError("runtime model is not research-only and active")
        if self.model.prompt_lineage_status != PromptLineageStatus.REGISTERED:
            raise RuntimeGovernanceError("runtime model has no registered prompt lineage")
        if self.prompt.status != PromptUseStatus.RESEARCH_ONLY:
            raise RuntimeGovernanceError("runtime prompt is disabled")
        if self.model.prompt_template_ref != self.prompt.prompt_ref:
            raise RuntimeGovernanceError("model and prompt references do not match")
        if self.prompt.model_ref != self.model.model_ref:
            raise RuntimeGovernanceError("prompt and model references do not match")
        if (
            self.model.tool_manifest_ref != self.manifest.manifest_ref
            or self.prompt.tool_manifest_ref != self.manifest.manifest_ref
        ):
            raise RuntimeGovernanceError("runtime tool-manifest lineage does not match")
        template_digest = hashlib.sha256(
            self._prompt_template.encode("utf-8")
        ).hexdigest()
        if template_digest != self.prompt.template_sha256:
            raise RuntimeGovernanceError("runtime prompt text does not match its registered digest")

    def _assert_registry_integrity(self) -> None:
        if not all(
            self._registry.verify_artifact(artifact_id)
            for artifact_id in (self._model_id, self._prompt_id, self._manifest_id)
        ):
            raise RuntimeGovernanceError("runtime lineage failed registry integrity verification")

    def authorize(self, action: str) -> str:
        self._assert_registry_integrity()
        normalized = action.strip().upper() if isinstance(action, str) else ""
        if not normalized:
            raise RuntimeGovernanceError("runtime action is required")
        if normalized in _FORBIDDEN_ACTIONS:
            raise RuntimeGovernanceError(f"runtime action is forbidden: {normalized}")
        if normalized in self.manifest.prohibited_actions:
            raise RuntimeGovernanceError(f"tool manifest prohibits action: {normalized}")
        if normalized not in self.manifest.allowed_actions:
            raise RuntimeGovernanceError(f"tool manifest does not allow action: {normalized}")
        return normalized

    def invoke(
        self,
        *,
        invocation_id: str,
        action: str,
        input_payload: Mapping[str, Any],
        execute: Callable[[Mapping[str, Any]], Any],
        recorded_at: int,
    ) -> RuntimeResult:
        normalized_action = self.authorize(action)
        if not isinstance(input_payload, Mapping):
            raise RuntimeGovernanceError("runtime input must be a structured mapping")
        if not callable(execute):
            raise TypeError("runtime execute boundary must be callable")
        if recorded_at < 0:
            raise RuntimeGovernanceError("runtime timestamp must be non-negative")
        input_hash = stable_hash(dict(input_payload))
        try:
            value = execute(input_payload)
        except Exception as exc:
            raise RuntimeExecutionError(
                f"registered runtime compute failed: {type(exc).__name__}"
            ) from exc
        if isinstance(value, Mapping):
            returned_action = value.get("action")
            if returned_action is not None and returned_action != normalized_action:
                raise RuntimeGovernanceError(
                    "runtime output attempted to override its authorized action"
                )
        if _contains_sensitive_key(value):
            raise RuntimeGovernanceError(
                "runtime output contains a sensitive or order-bearing field"
            )
        invocation = RuntimeInvocation(
            invocation_id=invocation_id,
            model_ref=self.model.model_ref,
            prompt_ref=self.prompt.prompt_ref,
            tool_manifest_ref=self.manifest.manifest_ref,
            action=normalized_action,
            input_sha256=input_hash,
            output_sha256=stable_hash(value),
            outcome=RuntimeInvocationOutcome.ALLOWED,
            recorded_at=recorded_at,
        )
        event_hash = self._registry.record_runtime_invocation(invocation)
        return RuntimeResult(value=value, invocation=invocation, event_hash=event_hash)

    def invoke_external(
        self,
        *,
        invocation_id: str,
        action: str,
        input_payload: Mapping[str, Any],
        binding_ref: str,
        provider: ExternalModelProvider,
        recorded_at: int,
        verifier: ExternalProviderVerifier | None = None,
    ) -> RuntimeResult:
        normalized_action = self.authorize(action)
        if not isinstance(input_payload, Mapping):
            raise RuntimeGovernanceError("runtime input must be a structured mapping")
        if recorded_at < 0:
            raise RuntimeGovernanceError("runtime timestamp must be non-negative")
        if not callable(getattr(provider, "invoke", None)):
            raise TypeError("external provider must implement invoke")
        verifier = verifier or DenyExternalProviderVerifier()
        if not callable(getattr(verifier, "verify", None)):
            raise TypeError("external provider verifier must implement verify")

        binding_id = _artifact_id(binding_ref, "provider-binding")
        binding = self._load(binding_id, ExternalProviderBinding)
        if not self._registry.verify_artifact(binding_id):
            raise RuntimeGovernanceError("provider binding failed registry integrity verification")
        if binding.status != ExternalProviderBindingStatus.RESEARCH_ONLY:
            raise RuntimeGovernanceError("external provider binding is disabled")
        if binding.model_ref != self.model.model_ref:
            raise RuntimeGovernanceError("provider binding does not match the runtime model")
        if normalized_action not in binding.allowed_actions:
            raise RuntimeGovernanceError("provider binding does not allow the runtime action")

        request = {
            "action": normalized_action,
            "input": dict(input_payload),
            "model_ref": self.model.model_ref,
            "prompt_ref": self.prompt.prompt_ref,
            "prompt_sha256": self.prompt.template_sha256,
            "provider_id": binding.provider_id,
            "provider_model_ref": binding.provider_model_ref,
            "tool_manifest_ref": self.manifest.manifest_ref,
        }
        request_hash = stable_hash(request)
        try:
            raw_response = provider.invoke(request)
            response = ExternalProviderResponse.model_validate(raw_response)
        except (ValidationError, TypeError, ValueError) as exc:
            raise RuntimeGovernanceError(
                f"external provider returned an invalid response: {type(exc).__name__}"
            ) from exc
        except Exception as exc:
            raise RuntimeExecutionError(
                f"external provider failed: {type(exc).__name__}"
            ) from exc

        if (
            response.provider_id != binding.provider_id
            or response.provider_model_ref != binding.provider_model_ref
        ):
            raise RuntimeGovernanceError("external provider response identity mismatch")
        if response.request_sha256 != request_hash:
            raise RuntimeGovernanceError("external provider request digest mismatch")
        try:
            verified = verifier.verify(binding=binding, response=response)
        except Exception as exc:
            raise RuntimeGovernanceError(
                f"external provider verification failed: {type(exc).__name__}"
            ) from exc
        if verified is not True:
            raise RuntimeGovernanceError("external provider response is not externally verified")

        value = response.response
        if isinstance(value, Mapping):
            returned_action = value.get("action")
            if returned_action is not None and returned_action != normalized_action:
                raise RuntimeGovernanceError(
                    "runtime output attempted to override its authorized action"
                )
        if _contains_sensitive_key(value):
            raise RuntimeGovernanceError(
                "runtime output contains a sensitive or order-bearing field"
            )
        invocation = RuntimeInvocation(
            invocation_id=invocation_id,
            model_ref=self.model.model_ref,
            prompt_ref=self.prompt.prompt_ref,
            tool_manifest_ref=self.manifest.manifest_ref,
            action=normalized_action,
            input_sha256=stable_hash(dict(input_payload)),
            output_sha256=response.response_sha256,
            outcome=RuntimeInvocationOutcome.ALLOWED,
            recorded_at=recorded_at,
        )
        event_hash = self._registry.record_runtime_invocation(invocation)
        return RuntimeResult(value=value, invocation=invocation, event_hash=event_hash)


__all__ = [
    "GovernedModelRuntime",
    "DenyExternalProviderVerifier",
    "ExternalModelProvider",
    "ExternalProviderVerifier",
    "RESEARCH_EVALUATION_TEMPLATE",
    "RuntimeExecutionError",
    "RuntimeGovernanceError",
    "RuntimeResult",
]
