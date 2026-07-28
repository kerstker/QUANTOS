"""Runtime model/prompt/tool governance tests.

The runtime is deliberately offline: a registered research model may evaluate a
candidate, but it cannot expand its action set, emit an order-like result, or
write an unverified invocation into the history ledger.
"""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from quantos.contracts.model_risk import (
    ExternalProviderBinding,
    ExternalProviderBindingStatus,
    ModelCard,
    ModelUseStatus,
    PromptLineageStatus,
    PromptTemplateCard,
    PromptUseStatus,
    ToolManifest,
)
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.panel_backtest import PanelPeriod
from services.governance.model_risk_registry import GovernedModelRegistry, SqliteModelRiskStore
from services.intelligence.agent_loop import ResearchAgentLoop
from services.intelligence.runtime import (
    GovernedModelRuntime,
    RESEARCH_EVALUATION_TEMPLATE,
    RuntimeExecutionError,
    RuntimeGovernanceError,
)
from tests.alpha.fixtures import catalog_bound_evaluation


def _runtime(tmp_path):
    store = SqliteModelRiskStore(str(tmp_path / "model-risk.db"), create=True)
    registry = GovernedModelRegistry(store)
    manifest = ToolManifest(
        manifest_id="research-runtime-tools",
        version="v1",
        implementation_ref="services/intelligence/guardrails.py",
        implementation_sha256="a" * 64,
        allowed_actions=("EVALUATE",),
        prohibited_actions=(
            "APPROVE",
            "RESET_KILL_SWITCH",
            "LIVE_TRADE",
            "ACCESS_CREDENTIALS",
        ),
        recorded_at=1,
    )
    model = ModelCard(
        model_id="research-runtime",
        version="v1",
        owner_group="research-intelligence",
        model_type="DETERMINISTIC_POLICY_LOOP",
        implementation_ref="services/intelligence/agent_loop.py",
        implementation_sha256="b" * 64,
        intended_use="offline candidate evaluation",
        prohibited_uses=("LIVE_TRADING", "ORDER_SUBMISSION", "CREDENTIAL_ACCESS"),
        prompt_lineage_status=PromptLineageStatus.REGISTERED,
        prompt_template_ref="research-evaluation@v1",
        tool_manifest_ref=manifest.manifest_ref,
        status=ModelUseStatus.RESEARCH_ONLY,
        recorded_at=1,
    )
    prompt = PromptTemplateCard(
        prompt_id="research-evaluation",
        version="v1",
        owner_group="research-intelligence",
        template_ref="inline:research-evaluation-v1",
        template_sha256=hashlib.sha256(
            RESEARCH_EVALUATION_TEMPLATE.encode("utf-8")
        ).hexdigest(),
        model_ref=model.model_ref,
        intended_use="structured offline evaluation only",
        prohibited_uses=("LIVE_TRADING", "ORDER_SUBMISSION", "CREDENTIAL_ACCESS"),
        tool_manifest_ref=manifest.manifest_ref,
        status=PromptUseStatus.RESEARCH_ONLY,
        recorded_at=1,
    )
    for artifact in (manifest, model, prompt):
        assert registry.record_artifact(artifact) is True
    runtime = GovernedModelRuntime(
        registry,
        model_ref=model.model_ref,
        prompt_ref=prompt.prompt_ref,
        tool_manifest_ref=manifest.manifest_ref,
        prompt_template=RESEARCH_EVALUATION_TEMPLATE,
    )
    return store, registry, runtime


def _provider_binding(registry, runtime, *, provider_id="provider-a"):
    binding = ExternalProviderBinding(
        binding_id="research-provider",
        version="v1",
        provider_id=provider_id,
        provider_model_ref="external-evaluator-v1",
        model_ref=runtime.model.model_ref,
        provider_deployment_ref="external-deployment-a",
        verification_policy_ref="external-receipt-policy-v1",
        allowed_actions=("EVALUATE",),
        status=ExternalProviderBindingStatus.RESEARCH_ONLY,
        recorded_at=1,
    )
    assert registry.record_external_provider_binding(binding) is True
    return binding


class _ExternalProvider:
    def __init__(self, value=None, *, mutate=None, error=None):
        self.value = value or {"action": "EVALUATE", "verdict": "RESEARCH_ONLY"}
        self.mutate = mutate
        self.error = error

    def invoke(self, request):
        if self.error is not None:
            raise self.error
        payload = {
            "provider_id": "provider-a",
            "provider_model_ref": "external-evaluator-v1",
            "external_request_id": "external-request-1",
            "request_sha256": stable_hash(dict(request)),
            "response": self.value,
            "response_sha256": stable_hash(self.value),
            "verification_receipt_ref": "receipt:external-request-1",
            "received_at": 2,
        }
        if self.mutate is not None:
            self.mutate(payload)
        return payload


class _ExternalVerifier:
    def verify(self, *, binding, response):
        return (
            binding.verification_policy_ref == "external-receipt-policy-v1"
            and response.verification_receipt_ref == "receipt:external-request-1"
        )


def test_runtime_requires_registered_lineage_and_records_hash_chained_event(tmp_path):
    store, registry, runtime = _runtime(tmp_path)
    try:
        first = runtime.invoke(
            invocation_id="inv-1",
            action="EVALUATE",
            input_payload={"alpha_id": "a0", "timestamp": 1},
            execute=lambda payload: {"action": "EVALUATE", "verdict": "RESEARCH_ONLY"},
            recorded_at=1,
        )
        second = runtime.invoke(
            invocation_id="inv-1",
            action="EVALUATE",
            input_payload={"alpha_id": "a0", "timestamp": 1},
            execute=lambda payload: {"action": "EVALUATE", "verdict": "RESEARCH_ONLY"},
            recorded_at=1,
        )
        assert first.value["verdict"] == "RESEARCH_ONLY"
        assert first.event_hash == second.event_hash
        assert len(registry.runtime_rows()) == 1
        assert registry.verify_runtime_history() is True
    finally:
        store.close()


@pytest.mark.parametrize("action", ["APPROVE", "RESET_KILL_SWITCH", "LIVE_TRADE", "ACCESS_CREDENTIALS"])
def test_runtime_rejects_human_only_and_forbidden_actions(tmp_path, action):
    store, _registry, runtime = _runtime(tmp_path)
    try:
        with pytest.raises(RuntimeGovernanceError):
            runtime.invoke(
                invocation_id=f"denied-{action}",
                action=action,
                input_payload={"alpha_id": "a0"},
                execute=lambda payload: {"action": action},
                recorded_at=1,
            )
    finally:
        store.close()


def test_runtime_rejects_action_override_and_sensitive_output(tmp_path):
    store, registry, runtime = _runtime(tmp_path)
    try:
        with pytest.raises(RuntimeGovernanceError, match="action"):
            runtime.invoke(
                invocation_id="override",
                action="EVALUATE",
                input_payload={"alpha_id": "a0"},
                execute=lambda payload: {"action": "APPROVE"},
                recorded_at=1,
            )
        with pytest.raises(RuntimeGovernanceError, match="sensitive"):
            runtime.invoke(
                invocation_id="secret-output",
                action="EVALUATE",
                input_payload={"alpha_id": "a0"},
                execute=lambda payload: {"credential": "never"},
                recorded_at=1,
            )
        assert registry.runtime_rows() == []
    finally:
        store.close()


def test_runtime_history_detects_tampering(tmp_path):
    store, registry, runtime = _runtime(tmp_path)
    try:
        runtime.invoke(
            invocation_id="tamper",
            action="EVALUATE",
            input_payload={"alpha_id": "a0"},
            execute=lambda payload: {"action": "EVALUATE"},
            recorded_at=1,
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._conn.execute(
                "UPDATE model_risk_runtime_events SET payload_json=? "
                "WHERE invocation_id=?",
                ('{"action":"APPROVE"}', "tamper"),
            )
        path = store.path
        store.close()
        connection = sqlite3.connect(path)
        connection.execute("DROP TRIGGER model_risk_runtime_no_update")
        connection.execute(
            "UPDATE model_risk_runtime_events SET payload_json=? "
            "WHERE invocation_id=?",
            ('{"action":"APPROVE"}', "tamper"),
        )
        connection.commit()
        connection.close()
        from services.governance.model_risk_registry import ModelRiskStoreError

        with pytest.raises(ModelRiskStoreError):
            SqliteModelRiskStore(path, create=False)
    finally:
        try:
            store.close()
        except sqlite3.ProgrammingError:
            pass


def test_runtime_history_tail_truncation_is_detected_by_pinned_head(tmp_path):
    store, _registry, runtime = _runtime(tmp_path)
    path = store.path
    runtime.invoke(
        invocation_id="tail",
        action="EVALUATE",
        input_payload={"alpha_id": "a0"},
        execute=lambda payload: {"action": "EVALUATE"},
        recorded_at=1,
    )
    store.close()

    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER model_risk_runtime_no_delete")
    connection.execute("DELETE FROM model_risk_runtime_events")
    connection.execute(
        "CREATE TRIGGER model_risk_runtime_no_delete "
        "BEFORE DELETE ON model_risk_runtime_events "
        "BEGIN SELECT RAISE(ABORT, 'model risk runtime history is append-only'); END"
    )
    connection.commit()
    connection.close()
    from services.governance.model_risk_registry import ModelRiskStoreError

    with pytest.raises(ModelRiskStoreError, match="runtime head mismatch"):
        SqliteModelRiskStore(path, create=False)


def test_external_provider_requires_registered_binding_and_verified_receipt(tmp_path):
    store, registry, runtime = _runtime(tmp_path)
    try:
        binding = _provider_binding(registry, runtime)
        result = runtime.invoke_external(
            invocation_id="external-1",
            action="EVALUATE",
            input_payload={"alpha_id": "a0", "timestamp": 1},
            binding_ref=binding.binding_ref,
            provider=_ExternalProvider(),
            verifier=_ExternalVerifier(),
            recorded_at=2,
        )
        assert result.value["verdict"] == "RESEARCH_ONLY"
        assert result.invocation.output_sha256 == stable_hash(result.value)
        assert len(registry.runtime_rows()) == 1
        assert registry.verify_runtime_history() is True
    finally:
        store.close()


def test_external_provider_default_verifier_denies_without_recording(tmp_path):
    store, registry, runtime = _runtime(tmp_path)
    try:
        binding = _provider_binding(registry, runtime)
        with pytest.raises(RuntimeGovernanceError, match="externally verified"):
            runtime.invoke_external(
                invocation_id="external-denied",
                action="EVALUATE",
                input_payload={"alpha_id": "a0"},
                binding_ref=binding.binding_ref,
                provider=_ExternalProvider(),
                recorded_at=2,
            )
        assert registry.runtime_rows() == []
    finally:
        store.close()


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda payload: payload.update(provider_id="provider-b"), "identity mismatch"),
        (lambda payload: payload.update(request_sha256="f" * 64), "request digest mismatch"),
        (lambda payload: payload.update(response_sha256="f" * 64), "invalid response"),
    ],
)
def test_external_provider_rejects_identity_and_digest_tampering_without_recording(
    tmp_path, mutate, match
):
    store, registry, runtime = _runtime(tmp_path)
    try:
        binding = _provider_binding(registry, runtime)
        with pytest.raises(RuntimeGovernanceError, match=match):
            runtime.invoke_external(
                invocation_id="external-tampered",
                action="EVALUATE",
                input_payload={"alpha_id": "a0"},
                binding_ref=binding.binding_ref,
                provider=_ExternalProvider(mutate=mutate),
                verifier=_ExternalVerifier(),
                recorded_at=2,
            )
        assert registry.runtime_rows() == []
    finally:
        store.close()


def test_external_provider_failure_is_redacted_and_sensitive_output_is_denied(tmp_path):
    store, registry, runtime = _runtime(tmp_path)
    try:
        binding = _provider_binding(registry, runtime)
        with pytest.raises(RuntimeExecutionError) as captured:
            runtime.invoke_external(
                invocation_id="external-error",
                action="EVALUATE",
                input_payload={"alpha_id": "a0"},
                binding_ref=binding.binding_ref,
                provider=_ExternalProvider(error=RuntimeError("api_key=never-log")),
                verifier=_ExternalVerifier(),
                recorded_at=2,
            )
        assert "never-log" not in str(captured.value)

        with pytest.raises(RuntimeGovernanceError, match="sensitive"):
            runtime.invoke_external(
                invocation_id="external-sensitive",
                action="EVALUATE",
                input_payload={"alpha_id": "a0"},
                binding_ref=binding.binding_ref,
                provider=_ExternalProvider(value={"order": {"symbol": "BTC"}}),
                verifier=_ExternalVerifier(),
                recorded_at=2,
            )
        assert registry.runtime_rows() == []
    finally:
        store.close()


def test_external_runtime_boundary_has_no_network_or_provider_sdk_imports():
    source = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "intelligence"
        / "runtime.py"
    ).read_text(encoding="utf-8")
    forbidden = ("import requests", "import httpx", "import openai", "import anthropic")
    assert all(marker not in source for marker in forbidden)


class _EvaluationStub:
    def __init__(self, catalog):
        self.catalog = catalog

    def prepare_catalog_observation(self, alpha_id, periods):
        del periods
        self.catalog.set_returns(alpha_id, (0.011, 0.0, 0.021, -0.01))

    def evaluate(self, alpha_id, periods, **kwargs):
        pbo_evidence = kwargs["pbo_evidence"]
        return catalog_bound_evaluation(
            self.catalog,
            alpha_id,
            verdict=ValidationVerdict.PASS,
            pbo_evidence=pbo_evidence,
        )


def _runtime_candidates():
    candidates = []
    for candidate_index in range(2):
        periods = [
            PanelPeriod(
                instruments=("x", "y", "z", "w"),
                signals=(1.0 + candidate_index, -1.0, 0.5, -0.5 - candidate_index),
                forward_returns=(
                    0.01 * ((period_index + candidate_index) % 3),
                    -0.005,
                    0.002,
                    -0.001,
                ),
                event_time=period_index,
            )
            for period_index in range(12)
        ]
        candidates.append(
            (
                AlphaDefinition(
                    alpha_id=f"a{candidate_index}",
                    name=f"A{candidate_index}",
                    factor_ids=(f"f{candidate_index}",),
                ),
                periods,
            )
        )
    return candidates


def test_agent_loop_uses_runtime_boundary_when_configured(tmp_path):
    store, registry, runtime = _runtime(tmp_path)
    try:
        catalog = AlphaCatalog()
        loop = ResearchAgentLoop(
            catalog=catalog,
            evaluator=_EvaluationStub(catalog),
            runtime=runtime,
        )
        result = loop.run(
            _runtime_candidates(),
            timestamp=7,
            runtime_context={"dataset_version": "fixture-v1"},
        )
        assert catalog.state("a0") == AlphaState.PAPER_TRADING
        assert result["human_review_queue"] == ["a0", "a1"]
        rows = registry.runtime_rows()
        assert len(rows) == 2
        assert all(row["action"] == "EVALUATE" for row in rows)
        assert registry.verify_runtime_history() is True
    finally:
        store.close()


def test_agent_loop_fails_closed_when_runtime_context_is_missing(tmp_path):
    store, registry, runtime = _runtime(tmp_path)
    try:
        catalog = AlphaCatalog()
        loop = ResearchAgentLoop(
            catalog=catalog,
            evaluator=_EvaluationStub(catalog),
            runtime=runtime,
        )
        result = loop.run(_runtime_candidates(), timestamp=7)
        assert result["human_review_queue"] == []
        assert result["report"]["a0"]["error"] == "ValueError"
        assert catalog.state("a0") != AlphaState.APPROVED
        assert registry.runtime_rows() == []
    finally:
        store.close()
