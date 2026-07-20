import hashlib
import json
from pathlib import Path

import pytest

from quantos.contracts.external_model_provider import (
    ExternalProviderProbeReceipt,
    ProviderTransport,
)
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
    ExternalProviderRuntimeError,
    GovernedExternalProviderRuntime,
)
from services.intelligence.provider_selection import (
    ProviderSelectionError,
    load_provider_registry,
    select_provider,
)
from services.intelligence.runtime import GovernedModelRuntime


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "config/model_provider_registry.json"
CHALLENGE = "QUANTOS_PROVIDER_PROBE_0123456789ABCDEF0123456789ABCDEF"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _runtime(tmp_path):
    provider_registry = load_provider_registry(REGISTRY_PATH)
    registration = select_provider(
        provider_registry,
        requested_provider_id="sushuo_gpt_5_6_sol",
        environ={},
    )
    store = SqliteModelRiskStore(str(tmp_path / "external-provider.db"), create=True)
    registry = GovernedModelRegistry(store)
    manifest = ToolManifest(
        manifest_id="external-provider-tools",
        version="v1",
        implementation_ref="services/intelligence/external_provider_runtime.py",
        implementation_sha256="a" * 64,
        allowed_actions=registration.allowed_actions,
        prohibited_actions=registration.prohibited_actions,
        recorded_at=1,
    )
    model = ModelCard(
        model_id="sushuo-gpt-5-6-sol",
        version="v1",
        owner_group="external-model-governance",
        model_type="OPENAI_COMPATIBLE_EXTERNAL_PROVIDER",
        implementation_ref="tools/external_model_provider.py",
        implementation_sha256="b" * 64,
        intended_use="research-only external provider evaluation",
        prohibited_uses=("LIVE_TRADING", "ORDER_SUBMISSION", "CREDENTIAL_ACCESS"),
        prompt_lineage_status=PromptLineageStatus.REGISTERED,
        prompt_template_ref=registration.runtime_prompt_ref,
        tool_manifest_ref=registration.runtime_tool_manifest_ref,
        status=ModelUseStatus.RESEARCH_ONLY,
        limitations=("TLS-authenticated unsigned provider response",),
        recorded_at=1,
    )
    prompt = PromptTemplateCard(
        prompt_id="external-provider-probe",
        version="v1",
        owner_group="external-model-governance",
        template_ref=("services/intelligence/external_provider_runtime.py#EXTERNAL_PROVIDER_PROBE_TEMPLATE"),
        template_sha256=_sha(EXTERNAL_PROVIDER_PROBE_TEMPLATE),
        model_ref=model.model_ref,
        intended_use="credential-free echo connectivity probe",
        prohibited_uses=("ALPHA_DISCLOSURE", "LIVE_TRADING", "ORDER_SUBMISSION"),
        tool_manifest_ref=manifest.manifest_ref,
        status=PromptUseStatus.RESEARCH_ONLY,
        recorded_at=1,
    )
    for artifact in (manifest, model, prompt):
        assert registry.record_artifact(artifact) is True
    governed = GovernedModelRuntime(
        registry,
        model_ref=model.model_ref,
        prompt_ref=prompt.prompt_ref,
        tool_manifest_ref=manifest.manifest_ref,
        prompt_template=EXTERNAL_PROVIDER_PROBE_TEMPLATE,
    )
    runtime = GovernedExternalProviderRuntime(governed, registration)
    return store, registry, runtime, registration


def _receipt(registration) -> ExternalProviderProbeReceipt:
    return ExternalProviderProbeReceipt(
        provider_id=registration.provider_id,
        endpoint_sha256=_sha(registration.endpoint),
        requested_model=registration.model,
        response_model=registration.model,
        challenge_sha256=_sha(CHALLENGE),
        request_body_sha256=_sha("request"),
        response_body_sha256=_sha("response"),
        content_sha256=_sha(CHALLENGE),
        tls_peer_cert_sha256=_sha("certificate"),
        provider_request_id_sha256=_sha("request-id"),
        finish_reason="stop",
        prompt_tokens=10,
        completion_tokens=3,
        total_tokens=13,
    )


def test_provider_selection_is_offline_by_default_and_explicitly_switchable():
    registry = load_provider_registry(REGISTRY_PATH)

    assert select_provider(registry, environ={}).transport == ProviderTransport.CALLER_SUPPLIED_OFFLINE
    selected = select_provider(
        registry,
        environ={"QUANTOS_MODEL_PROVIDER": "sushuo_gpt_5_6_sol"},
    )
    assert selected.transport == ProviderTransport.OPENAI_COMPATIBLE_HTTPS
    assert selected.endpoint == "https://vip.sushuo.biz/v1/chat/completions"
    assert selected.model == "gpt-5.6-sol"


def test_provider_selection_rejects_unknown_or_disabled_provider():
    registry = load_provider_registry(REGISTRY_PATH)
    with pytest.raises(ProviderSelectionError):
        select_provider(registry, requested_provider_id="unknown", environ={})

    disabled = registry.model_copy(
        update={
            "providers": tuple(
                item.model_copy(update={"enabled": False}) if item.provider_id == "sushuo_gpt_5_6_sol" else item
                for item in registry.providers
            )
        }
    )
    with pytest.raises(ProviderSelectionError):
        select_provider(
            disabled,
            requested_provider_id="sushuo_gpt_5_6_sol",
            environ={},
        )


def test_external_provider_runtime_records_only_redacted_receipt(tmp_path):
    store, registry, runtime, registration = _runtime(tmp_path)
    try:
        result = runtime.invoke_probe(
            invocation_id="external-provider-probe-1",
            challenge_token=CHALLENGE,
            execute=lambda challenge: _receipt(registration),
            recorded_at=10,
        )

        assert result.value["action"] == "EVALUATE"
        assert result.value["receipt"]["content_sha256"] == _sha(CHALLENGE)
        serialized = json.dumps(result.value, sort_keys=True)
        assert CHALLENGE not in serialized
        assert "Bearer" not in serialized
        assert registry.verify_runtime_history() is True
        assert len(registry.runtime_rows()) == 1
    finally:
        store.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_id", "other"),
        ("response_model", "other-model"),
        ("challenge_sha256", "0" * 64),
        ("content_sha256", "0" * 64),
        ("tls_peer_cert_sha256", "0" * 64),
    ],
)
def test_external_provider_runtime_rejects_unbound_or_unverified_receipt(tmp_path, field, value):
    store, registry, runtime, registration = _runtime(tmp_path)
    receipt = _receipt(registration).model_copy(update={field: value})
    try:
        with pytest.raises(ExternalProviderRuntimeError):
            runtime.invoke_probe(
                invocation_id=f"bad-{field}",
                challenge_token=CHALLENGE,
                execute=lambda challenge: receipt,
                recorded_at=10,
            )
        assert registry.runtime_rows() == []
    finally:
        store.close()
