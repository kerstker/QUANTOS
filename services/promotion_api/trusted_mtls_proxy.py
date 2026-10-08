"""Translate one trusted Envoy mTLS envelope into the ASGI TLS extension.

The adapter is intentionally narrow: it accepts requests only from the
promotion API's Unix-domain-socket listener, requires an exact deployment
marker, and consumes exactly one Envoy ``SANITIZE_SET`` XFCC value.  It never
turns a certificate header received over TCP into trusted identity evidence.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, MutableMapping
import hashlib
import re
import secrets
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding


_MARKER_HEADER = b"x-quantos-mtls-proxy"
_MARKER_VALUE = b"envoy-sanitize-set-v1"
_XFCC_HEADER = b"x-forwarded-client-cert"
_MAX_XFCC_BYTES = 65_536
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_HEX_BYTES = frozenset(b"0123456789abcdefABCDEF")
_TRUSTED_URI_PREFIX = "spiffe://quantos.l2/"
_UNAUTHORIZED_BODY = b'{"detail":"trusted mTLS proxy certificate rejected"}'

AsgiReceive = Callable[[], Awaitable[dict[str, Any]]]
AsgiSend = Callable[[dict[str, Any]], Awaitable[None]]
AsgiApp = Callable[[dict[str, Any], AsgiReceive, AsgiSend], Awaitable[None]]


class TrustedMtlsProxyError(PermissionError):
    """The request did not arrive through the exact trusted proxy boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _percent_decode(value: str) -> bytes:
    raw = value.encode("ascii")
    decoded = bytearray()
    index = 0
    while index < len(raw):
        if raw[index] != 0x25:
            decoded.append(raw[index])
            index += 1
            continue
        if index + 2 >= len(raw):
            raise TrustedMtlsProxyError("INVALID_XFCC_CERTIFICATE_ENCODING")
        encoded_byte = raw[index + 1 : index + 3]
        if any(item not in _HEX_BYTES for item in encoded_byte):
            raise TrustedMtlsProxyError("INVALID_XFCC_CERTIFICATE_ENCODING")
        decoded.append(int(encoded_byte, 16))
        index += 3
    return bytes(decoded)


def _header_values(scope: Mapping[str, Any], name: bytes) -> list[bytes]:
    headers = scope.get("headers")
    if not isinstance(headers, (list, tuple)):
        raise TrustedMtlsProxyError("INVALID_ASGI_HEADERS")
    values: list[bytes] = []
    for item in headers:
        if (
            not isinstance(item, (list, tuple))
            or len(item) != 2
            or not isinstance(item[0], bytes)
            or not isinstance(item[1], bytes)
        ):
            raise TrustedMtlsProxyError("INVALID_ASGI_HEADERS")
        if item[0].lower() == name:
            values.append(item[1])
    return values


def _parse_xfcc(value: bytes) -> bytes:
    if not value or len(value) > _MAX_XFCC_BYTES or b"," in value:
        raise TrustedMtlsProxyError("INVALID_XFCC_ENVELOPE")
    try:
        text = value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise TrustedMtlsProxyError("INVALID_XFCC_ENCODING") from exc

    fields: dict[str, str] = {}
    for component in text.split(";"):
        key, separator, field_value = component.partition("=")
        if not separator or not key or key in fields:
            raise TrustedMtlsProxyError("INVALID_XFCC_FIELDS")
        fields[key] = field_value

    if set(fields) != {"Hash", "Cert", "URI"}:
        raise TrustedMtlsProxyError("INVALID_XFCC_FIELDS")
    digest = fields["Hash"]
    encoded_certificate = fields["Cert"]
    uri = fields["URI"]
    if (
        _SHA256_HEX.fullmatch(digest) is None
        or len(encoded_certificate) < 3
        or not encoded_certificate.startswith('"')
        or not encoded_certificate.endswith('"')
        or not uri.startswith(_TRUSTED_URI_PREFIX)
        or any(char.isspace() for char in uri)
    ):
        raise TrustedMtlsProxyError("INVALID_XFCC_FIELDS")

    encoded_pem = encoded_certificate[1:-1]
    try:
        pem = _percent_decode(encoded_pem)
        certificate = x509.load_pem_x509_certificate(pem)
        der = certificate.public_bytes(Encoding.DER)
    except (TypeError, ValueError) as exc:
        raise TrustedMtlsProxyError("INVALID_XFCC_CERTIFICATE") from exc
    actual_digest = hashlib.sha256(der).hexdigest()
    if not secrets.compare_digest(digest, actual_digest):
        raise TrustedMtlsProxyError("XFCC_CERTIFICATE_HASH_MISMATCH")
    return der


def build_envoy_mtls_scope(scope: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy containing a trusted TLS extension or fail closed."""

    if not isinstance(scope, Mapping):
        raise TrustedMtlsProxyError("INVALID_ASGI_SCOPE")
    if scope.get("type") not in {"http", "websocket"}:
        raise TrustedMtlsProxyError("INVALID_ASGI_SCOPE_TYPE")
    if scope.get("client") is not None or scope.get("server") is not None:
        raise TrustedMtlsProxyError("UNTRUSTED_TCP_TRANSPORT")

    markers = _header_values(scope, _MARKER_HEADER)
    certificates = _header_values(scope, _XFCC_HEADER)
    if len(markers) != 1 or markers[0] != _MARKER_VALUE:
        raise TrustedMtlsProxyError("INVALID_PROXY_MARKER")
    if len(certificates) != 1:
        raise TrustedMtlsProxyError("INVALID_XFCC_COUNT")
    certificate_der = _parse_xfcc(certificates[0])

    trusted_scope = dict(scope)
    headers = scope.get("headers")
    assert isinstance(headers, (list, tuple))
    trusted_scope["headers"] = [
        (name, value)
        for name, value in headers
        if name.lower() not in {_MARKER_HEADER, _XFCC_HEADER}
    ]
    extensions = scope.get("extensions")
    trusted_extensions: MutableMapping[str, Any] = (
        dict(extensions) if isinstance(extensions, Mapping) else {}
    )
    trusted_extensions.pop("tls", None)
    trusted_extensions["tls"] = {
        "client_cert_chain_verified": True,
        "client_cert_chain": [certificate_der],
        "verification_source": "ENVOY_SANITIZE_SET_OVER_UDS",
    }
    trusted_scope["extensions"] = trusted_extensions
    return trusted_scope


class EnvoyMtlsScopeMiddleware:
    """Require and translate the trusted Envoy certificate envelope."""

    def __init__(self, app: AsgiApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        try:
            trusted_scope = build_envoy_mtls_scope(scope)
        except TrustedMtlsProxyError:
            if scope.get("type") == "websocket":
                await send({"type": "websocket.close", "code": 4401})
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(_UNAUTHORIZED_BODY)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})
            return
        await self.app(trusted_scope, receive, send)


__all__ = [
    "EnvoyMtlsScopeMiddleware",
    "TrustedMtlsProxyError",
    "build_envoy_mtls_scope",
]
