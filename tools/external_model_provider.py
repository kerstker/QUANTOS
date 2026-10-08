"""OpenAI-compatible HTTPS adapter for an explicitly selected research provider.

Network access is intentionally confined to this tools boundary. The adapter
returns a redacted receipt and never persists credentials, response content, or
raw response bodies.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
from typing import Protocol
from urllib.parse import urlsplit

from quantos.contracts.external_model_provider import (
    ExternalModelProviderRegistration,
    ExternalProviderProbeReceipt,
    ProviderTlsMode,
    ProviderTransport,
)
from services.intelligence.external_provider_runtime import (
    render_external_provider_probe_prompt,
)


class ProviderCredentialError(PermissionError):
    """The registered external provider credential is unavailable."""


class ProviderTransportError(RuntimeError):
    """The HTTPS exchange failed without exposing credential or response data."""


class ProviderResponseError(ValueError):
    """The provider response failed the registered response contract."""


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    tls_peer_cert_der: bytes


class HttpsTransport(Protocol):
    def post(
        self,
        *,
        endpoint: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> TransportResponse: ...


class StdlibHttpsTransport:
    def post(
        self,
        *,
        endpoint: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> TransportResponse:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ProviderTransportError("registered provider endpoint is not admissible")
        path = parsed.path or "/"
        context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port or 443,
            timeout=timeout_seconds,
            context=context,
        )
        try:
            connection.request("POST", path, body=body, headers=dict(headers))
            response = connection.getresponse()
            certificate = connection.sock.getpeercert(binary_form=True) if connection.sock is not None else b""
            response_body = response.read(max_response_bytes + 1)
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            return TransportResponse(
                status=response.status,
                headers=response_headers,
                body=response_body,
                tls_peer_cert_der=certificate or b"",
            )
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise ProviderTransportError(f"external provider HTTPS exchange failed: {type(exc).__name__}") from exc
        finally:
            connection.close()


def load_api_key(
    registration: ExternalModelProviderRegistration,
    *,
    key_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    if (
        not isinstance(registration, ExternalModelProviderRegistration)
        or registration.transport != ProviderTransport.OPENAI_COMPATIBLE_HTTPS
    ):
        raise ProviderCredentialError("provider credential is unavailable")
    environment = os.environ if environ is None else environ
    value = str(environment.get(registration.api_key_env, "")).strip()
    if not value and key_file is not None:
        try:
            value = Path(key_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ProviderCredentialError("provider credential is unavailable") from exc
    if len(value) < 8 or any(character.isspace() for character in value):
        raise ProviderCredentialError("provider credential is unavailable")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _usage_value(usage: Mapping[str, object], field: str) -> int:
    value = usage.get(field)
    if type(value) is not int or value < 0:
        raise ProviderResponseError("provider usage contract is invalid")
    return value


class OpenAICompatibleProvider:
    def __init__(
        self,
        registration: ExternalModelProviderRegistration,
        *,
        transport: HttpsTransport | None = None,
    ) -> None:
        if not isinstance(registration, ExternalModelProviderRegistration):
            raise TypeError("provider client requires canonical registration")
        if (
            registration.transport != ProviderTransport.OPENAI_COMPATIBLE_HTTPS
            or registration.tls_mode != ProviderTlsMode.SYSTEM_CA_HOSTNAME_VERIFIED
        ):
            raise ProviderTransportError("provider is not registered for verified HTTPS")
        self.registration = registration
        self._transport = StdlibHttpsTransport() if transport is None else transport

    def probe(
        self,
        *,
        api_key: str,
        challenge_token: str,
    ) -> ExternalProviderProbeReceipt:
        if len(api_key.strip()) < 8 or any(character.isspace() for character in api_key):
            raise ProviderCredentialError("provider credential is unavailable")
        prompt = render_external_provider_probe_prompt(challenge_token)
        request_document = {
            "messages": [{"role": "user", "content": prompt}],
            "model": self.registration.model,
            "temperature": 0.0,
        }
        request_body = json.dumps(
            request_document,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if api_key.encode("utf-8") in request_body:
            raise ProviderCredentialError("provider credential entered request body")
        response = self._transport.post(
            endpoint=self.registration.endpoint,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "QUANTOS-L2-external-provider-probe/1",
            },
            body=request_body,
            timeout_seconds=self.registration.timeout_seconds,
            max_response_bytes=self.registration.max_response_bytes,
        )
        if response.status != 200:
            raise ProviderResponseError(f"provider returned HTTP status {response.status}")
        if len(response.body) > self.registration.max_response_bytes:
            raise ProviderResponseError("provider response exceeds registered size limit")
        if not response.tls_peer_cert_der:
            raise ProviderResponseError("provider TLS peer certificate is unavailable")
        try:
            document = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderResponseError("provider response is not valid UTF-8 JSON") from exc
        if not isinstance(document, dict):
            raise ProviderResponseError("provider response envelope is invalid")
        response_model = document.get("model")
        response_id = document.get("id")
        choices = document.get("choices")
        usage = document.get("usage")
        if (
            response_model != self.registration.model
            or not isinstance(response_id, str)
            or not response_id
            or not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
            or not isinstance(usage, dict)
        ):
            raise ProviderResponseError("provider response envelope is invalid")
        choice = choices[0]
        message = choice.get("message")
        finish_reason = choice.get("finish_reason")
        if not isinstance(message, dict) or message.get("content") != challenge_token or finish_reason != "stop":
            raise ProviderResponseError("provider echo response did not match the probe")
        prompt_tokens = _usage_value(usage, "prompt_tokens")
        completion_tokens = _usage_value(usage, "completion_tokens")
        total_tokens = _usage_value(usage, "total_tokens")
        request_id = str(response.headers.get("x-request-id", "")).strip() or response_id
        try:
            return ExternalProviderProbeReceipt(
                provider_id=self.registration.provider_id,
                endpoint_sha256=_sha256_text(self.registration.endpoint),
                requested_model=self.registration.model,
                response_model=response_model,
                challenge_sha256=_sha256_text(challenge_token),
                request_body_sha256=_sha256_bytes(request_body),
                response_body_sha256=_sha256_bytes(response.body),
                content_sha256=_sha256_text(challenge_token),
                tls_peer_cert_sha256=_sha256_bytes(response.tls_peer_cert_der),
                provider_request_id_sha256=_sha256_text(request_id),
                finish_reason=finish_reason,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
        except (TypeError, ValueError) as exc:
            raise ProviderResponseError("provider receipt contract is invalid") from exc


__all__ = [
    "HttpsTransport",
    "OpenAICompatibleProvider",
    "ProviderCredentialError",
    "ProviderResponseError",
    "ProviderTransportError",
    "StdlibHttpsTransport",
    "TransportResponse",
    "load_api_key",
]
