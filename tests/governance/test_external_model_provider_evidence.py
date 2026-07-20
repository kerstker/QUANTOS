import copy
import hashlib
import json
from pathlib import Path

import pytest

from quantos.contracts.hashing import stable_hash
from services.governance.external_model_provider_evidence import (
    ExternalModelProviderEvidenceError,
    verify_external_model_provider_evidence,
)
from services.intelligence.provider_selection import (
    load_provider_registry,
    select_provider,
)
from tools.external_model_provider import (
    OpenAICompatibleProvider,
    TransportResponse,
)
from tools.external_model_provider_probe import (
    REQUIRED_PROVIDER_RED_TEAM_CASES,
    build_external_model_provider_evidence,
)


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "config/model_provider_registry.json"
CHALLENGE = "QUANTOS_PROVIDER_PROBE_0123456789ABCDEF0123456789ABCDEF"
SECRET = "test-secret-that-must-never-enter-versioned-evidence"


class FakeTransport:
    def post(self, **_kwargs):
        body = json.dumps(
            {
                "id": "chatcmpl-evidence-test",
                "model": "gpt-5.6-sol",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": CHALLENGE},
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return TransportResponse(
            status=200,
            headers={"x-request-id": "external-evidence-request"},
            body=body,
            tls_peer_cert_der=b"external-evidence-certificate",
        )


def _document():
    registration = select_provider(
        load_provider_registry(REGISTRY_PATH),
        requested_provider_id="sushuo_gpt_5_6_sol",
        environ={},
    )
    client = OpenAICompatibleProvider(registration, transport=FakeTransport())
    return build_external_model_provider_evidence(
        repo_root=ROOT,
        provider_registry_path=REGISTRY_PATH,
        registration=registration,
        api_key=SECRET,
        client=client,
        challenge_token=CHALLENGE,
        recorded_at=100,
        key_source="EXPLICIT_FILE",
    )


def _rehash(document):
    payload = {key: value for key, value in document.items() if key != "self_sha256"}
    document["self_sha256"] = stable_hash(payload)


def test_external_provider_evidence_replays_registered_runtime_without_secret():
    document = _document()

    summary = verify_external_model_provider_evidence(document, repo_root=ROOT)

    assert summary["provider_id"] == "sushuo_gpt_5_6_sol"
    assert summary["current_maturity"] == "M3"
    assert summary["external_provider_exercised"] is True
    serialized = json.dumps(document, sort_keys=True)
    assert SECRET not in serialized
    assert "Bearer " not in serialized
    assert "raw_response" not in serialized
    assert "response_content" not in serialized
    assert set(document["required_red_team_case_ids"]) == set(REQUIRED_PROVIDER_RED_TEAM_CASES)
    assert all(case["passed"] for case in document["red_team_cases"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda doc: doc.__setitem__("provider_id", "other"),
        lambda doc: doc["probe_receipt"].__setitem__("response_model", "other"),
        lambda doc: doc["probe_receipt"].__setitem__("content_sha256", "0" * 64),
        lambda doc: doc["runtime_event"].__setitem__("event_hash", "0" * 64),
        lambda doc: doc["runtime_output"]["receipt"].__setitem__("response_body_sha256", "0" * 64),
        lambda doc: doc["source_sha256"].__setitem__("tools/external_model_provider.py", "0" * 64),
        lambda doc: doc["red_team_cases"][0].__setitem__("passed", False),
    ],
)
def test_external_provider_evidence_rejects_semantic_tamper_even_when_rehashed(mutation):
    document = copy.deepcopy(_document())
    mutation(document)
    _rehash(document)

    with pytest.raises(ExternalModelProviderEvidenceError):
        verify_external_model_provider_evidence(document, repo_root=ROOT)


def test_external_provider_evidence_rejects_raw_response_field_even_when_rehashed():
    document = _document()
    document["raw_response"] = {"content": CHALLENGE}
    _rehash(document)

    with pytest.raises(ExternalModelProviderEvidenceError, match="schema"):
        verify_external_model_provider_evidence(document, repo_root=ROOT)


def test_external_provider_evidence_binds_request_body_to_challenge_and_model():
    document = _document()
    prompt = f"Reply with exactly this token and nothing else: {CHALLENGE}"
    body = json.dumps(
        {
            "messages": [{"role": "user", "content": prompt}],
            "model": "gpt-5.6-sol",
            "temperature": 0.0,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert document["probe_receipt"]["request_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert document["probe_receipt"]["content_sha256"] == hashlib.sha256(CHALLENGE.encode("utf-8")).hexdigest()
