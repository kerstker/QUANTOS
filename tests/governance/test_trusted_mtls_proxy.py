from __future__ import annotations

import hashlib
from urllib.parse import quote

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI


def _certificate() -> tuple[bytes, bytes]:
    from datetime import UTC, datetime, timedelta

    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "operator-device")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "quantos-l2-ca")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=8))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier("spiffe://quantos.l2/operator-device/one")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM),
        certificate.public_bytes(serialization.Encoding.DER),
    )


def _xfcc(pem: bytes, der: bytes) -> bytes:
    return (
        f'Hash={hashlib.sha256(der).hexdigest()};'
        f'Cert="{quote(pem.decode("ascii"), safe="")}";'
        'URI=spiffe://quantos.l2/operator-device/one'
    ).encode("ascii")


def _scope(*, uds: bool = True, marker: bool = True, xfcc: bytes | None = None):
    headers = [(b"host", b"governance.quantos.internal")]
    if marker:
        headers.append((b"x-quantos-mtls-proxy", b"envoy-sanitize-set-v1"))
    if xfcc is not None:
        headers.append((b"x-forwarded-client-cert", xfcc))
    return {
        "type": "http",
        "scheme": "http",
        "client": None if uds else ("127.0.0.1", 41_000),
        "server": None if uds else ("127.0.0.1", 8441),
        "headers": headers,
        "extensions": {},
    }


def test_envoy_sanitized_certificate_becomes_trusted_tls_scope() -> None:
    from services.governance.strong_identity import extract_verified_client_certificate
    from services.promotion_api.trusted_mtls_proxy import build_envoy_mtls_scope

    pem, der = _certificate()
    scope = build_envoy_mtls_scope(_scope(xfcc=_xfcc(pem, der)))

    assert extract_verified_client_certificate(scope) == der
    assert scope["extensions"]["tls"] == {
        "client_cert_chain_verified": True,
        "client_cert_chain": [der],
        "verification_source": "ENVOY_SANITIZE_SET_OVER_UDS",
    }
    assert all(
        name not in {b"x-forwarded-client-cert", b"x-quantos-mtls-proxy"}
        for name, _value in scope["headers"]
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda scope: scope.update({"client": ("127.0.0.1", 42_000)}),
        lambda scope: scope.update({"server": ("127.0.0.1", 8441)}),
        lambda scope: scope["headers"].pop(1),
        lambda scope: scope["headers"].append(
            (b"x-quantos-mtls-proxy", b"envoy-sanitize-set-v1")
        ),
        lambda scope: scope["headers"].append(
            (b"x-forwarded-client-cert", scope["headers"][-1][1])
        ),
        lambda scope: scope["headers"].__setitem__(
            -1,
            (b"x-forwarded-client-cert", scope["headers"][-1][1] + b",Hash=other"),
        ),
        lambda scope: scope["headers"].__setitem__(
            -1,
            (b"x-forwarded-client-cert", scope["headers"][-1][1].replace(b"Hash=", b"Hash=00")),
        ),
        lambda scope: scope["headers"].__setitem__(
            -1,
            (b"x-forwarded-client-cert", b'Hash=' + b"0" * 64 + b';Cert="not-a-cert"'),
        ),
        lambda scope: scope["headers"].__setitem__(
            -1,
            (b"x-forwarded-client-cert", scope["headers"][-1][1].replace(b"%20", b"%G0", 1)),
        ),
    ],
)
def test_proxy_adapter_rejects_tcp_spoof_duplicates_and_malformed_envelopes(
    mutate,
) -> None:
    from services.promotion_api.trusted_mtls_proxy import (
        TrustedMtlsProxyError,
        build_envoy_mtls_scope,
    )

    pem, der = _certificate()
    scope = _scope(xfcc=_xfcc(pem, der))
    mutate(scope)
    with pytest.raises(TrustedMtlsProxyError):
        build_envoy_mtls_scope(scope)


def test_missing_envoy_certificate_fails_closed_without_trusting_old_extension() -> None:
    from services.promotion_api.trusted_mtls_proxy import (
        TrustedMtlsProxyError,
        build_envoy_mtls_scope,
    )

    scope = _scope(xfcc=None)
    scope["extensions"] = {
        "tls": {
            "client_cert_chain_verified": True,
            "client_cert_chain": [b"caller-fabricated"],
        }
    }
    with pytest.raises(TrustedMtlsProxyError):
        build_envoy_mtls_scope(scope)


@pytest.mark.asyncio
async def test_middleware_returns_401_and_never_calls_app_on_proxy_failure() -> None:
    from services.promotion_api.trusted_mtls_proxy import EnvoyMtlsScopeMiddleware

    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    messages: list[dict[str, object]] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    middleware = EnvoyMtlsScopeMiddleware(app)
    await middleware(_scope(xfcc=b"malformed"), receive, send)

    assert called is False
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 401
    assert messages[1] == {
        "type": "http.response.body",
        "body": b'{"detail":"trusted mTLS proxy certificate rejected"}',
    }


def test_promotion_api_transport_mode_is_explicit_and_fail_closed() -> None:
    from services.promotion_api.main import configure_trusted_transport
    from services.promotion_api.trusted_mtls_proxy import EnvoyMtlsScopeMiddleware

    disabled = FastAPI()
    configure_trusted_transport(disabled, {})
    assert not any(
        item.cls is EnvoyMtlsScopeMiddleware for item in disabled.user_middleware
    )

    enabled = FastAPI()
    configure_trusted_transport(
        enabled,
        {"QUANTOS_TRUSTED_MTLS_PROXY_MODE": "ENVOY_UDS_V1"},
    )
    assert sum(
        item.cls is EnvoyMtlsScopeMiddleware for item in enabled.user_middleware
    ) == 1

    with pytest.raises(RuntimeError, match="unsupported trusted mTLS proxy mode"):
        configure_trusted_transport(
            FastAPI(),
            {"QUANTOS_TRUSTED_MTLS_PROXY_MODE": "trust-all-internal-headers"},
        )
