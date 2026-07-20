"""Contracts for switchable research-only external model providers.

The contracts contain provider identity and redacted probe receipts only. They
never contain credentials, raw responses, model content, or execution authority.
"""

from __future__ import annotations

from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .hashing import stable_hash


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_HTTPS_ENDPOINT = re.compile(
    r"^https://(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)"
    r"(?::(?P<port>[0-9]{1,5}))?(?P<path>/[^?#]*)$"
)
_REQUIRED_PROHIBITED = frozenset(
    {
        "APPROVE",
        "RESET_KILL_SWITCH",
        "LIVE_TRADE",
        "ORDER_SUBMISSION",
        "ACCESS_CREDENTIALS",
    }
)

EXTERNAL_PROVIDER_PROBE_TEMPLATE = "Reply with exactly this token and nothing else: {challenge_token}"


class ProviderTransport(str, Enum):
    CALLER_SUPPLIED_OFFLINE = "CALLER_SUPPLIED_OFFLINE"
    OPENAI_COMPATIBLE_HTTPS = "OPENAI_COMPATIBLE_HTTPS"


class ProviderTlsMode(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    SYSTEM_CA_HOSTNAME_VERIFIED = "SYSTEM_CA_HOSTNAME_VERIFIED"


class _FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class ExternalModelProviderRegistration(_FrozenContract):
    provider_id: str = Field(min_length=1)
    transport: ProviderTransport
    enabled: bool
    endpoint: str = ""
    model: str = Field(min_length=1)
    api_key_env: str = ""
    timeout_seconds: int = Field(ge=0, le=300)
    max_response_bytes: int = Field(ge=0, le=4 * 1024 * 1024)
    tls_mode: ProviderTlsMode
    runtime_model_ref: str = Field(min_length=1)
    runtime_prompt_ref: str = Field(min_length=1)
    runtime_tool_manifest_ref: str = Field(min_length=1)
    allowed_actions: tuple[str, ...] = Field(min_length=1)
    prohibited_actions: tuple[str, ...] = Field(min_length=1)

    @field_validator("allowed_actions", "prohibited_actions")
    @classmethod
    def _validate_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("provider actions must be unique and non-empty")
        return normalized

    @model_validator(mode="after")
    def _validate_transport_profile(self) -> "ExternalModelProviderRegistration":
        overlap = set(self.allowed_actions) & set(self.prohibited_actions)
        if overlap:
            raise ValueError("provider allowed and prohibited actions overlap")
        if tuple(self.allowed_actions) != ("EVALUATE",):
            raise ValueError("provider may allow only EVALUATE")
        if not _REQUIRED_PROHIBITED <= set(self.prohibited_actions):
            raise ValueError("provider is missing required prohibited actions")

        if self.transport == ProviderTransport.CALLER_SUPPLIED_OFFLINE:
            if (
                self.endpoint
                or self.api_key_env
                or self.timeout_seconds != 0
                or self.max_response_bytes != 0
                or self.tls_mode != ProviderTlsMode.NOT_APPLICABLE
            ):
                raise ValueError("offline provider cannot declare network or credential fields")
            return self

        match = _HTTPS_ENDPOINT.fullmatch(self.endpoint)
        if match is None or "@" in self.endpoint:
            raise ValueError("external provider requires a simple HTTPS endpoint")
        if match.group("path") != "/v1/chat/completions":
            raise ValueError("external provider endpoint must use /v1/chat/completions")
        if match.group("port") is not None and not 1 <= int(match.group("port")) <= 65535:
            raise ValueError("external provider endpoint port is invalid")
        if _ENV_NAME.fullmatch(self.api_key_env) is None:
            raise ValueError("external provider requires a registered credential environment name")
        if self.timeout_seconds <= 0 or self.max_response_bytes <= 0:
            raise ValueError("external provider requires positive timeout and response limits")
        if self.tls_mode != ProviderTlsMode.SYSTEM_CA_HOSTNAME_VERIFIED:
            raise ValueError("external provider must verify system CA and hostname")
        return self

    @property
    def registration_sha256(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class ExternalModelProviderRegistry(_FrozenContract):
    schema_version: int = 1
    selection_env: str = Field(min_length=1)
    default_provider_id: str = Field(min_length=1)
    providers: tuple[ExternalModelProviderRegistration, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_registry(self) -> "ExternalModelProviderRegistry":
        if self.schema_version != 1:
            raise ValueError("unsupported provider registry schema")
        if _ENV_NAME.fullmatch(self.selection_env) is None:
            raise ValueError("provider selection environment name is invalid")
        provider_ids = [item.provider_id for item in self.providers]
        if len(set(provider_ids)) != len(provider_ids):
            raise ValueError("provider IDs must be unique")
        if self.default_provider_id not in provider_ids:
            raise ValueError("default provider is not registered")
        return self

    @property
    def registry_sha256(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class ExternalProviderProbeReceipt(_FrozenContract):
    provider_id: str = Field(min_length=1)
    endpoint_sha256: str
    requested_model: str = Field(min_length=1)
    response_model: str = Field(min_length=1)
    challenge_sha256: str
    request_body_sha256: str
    response_body_sha256: str
    content_sha256: str
    tls_peer_cert_sha256: str
    provider_request_id_sha256: str
    finish_reason: str = Field(min_length=1)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @field_validator(
        "endpoint_sha256",
        "challenge_sha256",
        "request_body_sha256",
        "response_body_sha256",
        "content_sha256",
        "tls_peer_cert_sha256",
        "provider_request_id_sha256",
        mode="before",
    )
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        normalized = value.strip().lower() if isinstance(value, str) else ""
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError("probe receipt requires SHA-256 digests")
        return normalized

    @model_validator(mode="after")
    def _validate_receipt(self) -> "ExternalProviderProbeReceipt":
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("provider token totals do not reconcile")
        if self.tls_peer_cert_sha256 == "0" * 64:
            raise ValueError("provider TLS certificate digest cannot be the zero digest")
        return self


__all__ = [
    "EXTERNAL_PROVIDER_PROBE_TEMPLATE",
    "ExternalModelProviderRegistration",
    "ExternalModelProviderRegistry",
    "ExternalProviderProbeReceipt",
    "ProviderTlsMode",
    "ProviderTransport",
]
