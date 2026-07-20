import hashlib
import json
from pathlib import Path

import pytest

from services.intelligence.provider_selection import (
    load_provider_registry,
    select_provider,
)
from tools.external_model_provider import (
    OpenAICompatibleProvider,
    ProviderCredentialError,
    ProviderResponseError,
    TransportResponse,
    load_api_key,
)


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "config/model_provider_registry.json"
CHALLENGE = "QUANTOS_PROVIDER_PROBE_0123456789ABCDEF0123456789ABCDEF"
SECRET = "test-secret-that-must-never-enter-a-receipt"


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _registration():
    return select_provider(
        load_provider_registry(REGISTRY_PATH),
        requested_provider_id="sushuo_gpt_5_6_sol",
        environ={},
    )


def _body(*, content: str = CHALLENGE, model: str = "gpt-5.6-sol") -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-provider-test",
            "model": model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {
                "prompt_tokens": 17,
                "completion_tokens": 4,
                "total_tokens": 21,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class FakeTransport:
    def __init__(self, response: TransportResponse):
        self.response = response
        self.calls = []

    def post(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _transport_response(**overrides) -> TransportResponse:
    values = {
        "status": 200,
        "headers": {"x-request-id": "provider-request-1"},
        "body": _body(),
        "tls_peer_cert_der": b"certificate-der-fixture",
    }
    values.update(overrides)
    return TransportResponse(**values)


def test_client_sends_minimal_probe_and_returns_only_hashed_content():
    response = _transport_response()
    transport = FakeTransport(response)
    client = OpenAICompatibleProvider(_registration(), transport=transport)

    receipt = client.probe(api_key=SECRET, challenge_token=CHALLENGE)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["endpoint"] == "https://vip.sushuo.biz/v1/chat/completions"
    assert call["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert SECRET.encode("utf-8") not in call["body"]
    request = json.loads(call["body"])
    assert request["model"] == "gpt-5.6-sol"
    assert request["temperature"] == 0.0
    assert request["messages"] == [
        {
            "role": "user",
            "content": f"Reply with exactly this token and nothing else: {CHALLENGE}",
        }
    ]
    serialized = receipt.model_dump_json()
    assert SECRET not in serialized
    assert CHALLENGE not in serialized
    assert receipt.content_sha256 == hashlib.sha256(CHALLENGE.encode()).hexdigest()
    assert receipt.response_body_sha256 == _sha_bytes(response.body)
    assert receipt.tls_peer_cert_sha256 == _sha_bytes(response.tls_peer_cert_der)
    assert receipt.total_tokens == 21


def test_key_loader_prefers_registered_environment_and_supports_explicit_file(tmp_path):
    registration = _registration()
    key_path = tmp_path / "key.txt"
    key_path.write_text(" file-secret \n", encoding="utf-8")

    assert load_api_key(registration, environ={"NEW_API_KEY": " env-secret "}) == "env-secret"
    assert load_api_key(registration, key_file=key_path, environ={}) == "file-secret"


@pytest.mark.parametrize("environment", [{}, {"NEW_API_KEY": "   "}])
def test_key_loader_fails_closed_without_nonempty_key(environment):
    with pytest.raises(ProviderCredentialError, match="credential is unavailable"):
        load_api_key(_registration(), environ=environment)


@pytest.mark.parametrize(
    "response",
    [
        _transport_response(status=401),
        _transport_response(body=b"not-json"),
        _transport_response(body=_body(content="wrong")),
        _transport_response(body=_body(model="other-model")),
        _transport_response(tls_peer_cert_der=b""),
        _transport_response(body=json.dumps({"choices": []}).encode()),
    ],
)
def test_client_rejects_unauthenticated_or_malformed_provider_response(response):
    client = OpenAICompatibleProvider(
        _registration(),
        transport=FakeTransport(response),
    )
    with pytest.raises(ProviderResponseError) as caught:
        client.probe(api_key=SECRET, challenge_token=CHALLENGE)
    assert SECRET not in str(caught.value)
    assert response.body.decode("utf-8", errors="ignore") not in str(caught.value)


def test_client_rejects_response_over_registered_limit():
    registration = _registration().model_copy(update={"max_response_bytes": 8})
    client = OpenAICompatibleProvider(
        registration,
        transport=FakeTransport(_transport_response()),
    )
    with pytest.raises(ProviderResponseError, match="response exceeds"):
        client.probe(api_key=SECRET, challenge_token=CHALLENGE)
