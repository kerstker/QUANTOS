"""Runtime model/prompt/tool governance tests.

The runtime is deliberately offline: a registered research model may evaluate a
candidate, but it cannot expand its action set, emit an order-like result, or
write an unverified invocation into the history ledger.
"""

import hashlib

import pytest

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.model_risk import (
    ModelCard,
    ModelUseStatus,
    PromptLineageStatus,
    PromptTemplateCard,
    PromptUseStatus,
    ToolManifest,
)
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.governance.model_risk_registry import GovernedModelRegistry, SqliteModelRiskStore
from services.intelligence.agent_loop import ResearchAgentLoop
from services.intelligence.runtime import (
    GovernedModelRuntime,
    RESEARCH_EVALUATION_TEMPLATE,
    RuntimeGovernanceError,
)


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
        store._conn.execute(
            "UPDATE model_risk_runtime_events SET payload_json=? WHERE invocation_id=?",
            ('{"action":"APPROVE"}', "tamper"),
        )
        store._conn.commit()
        assert registry.verify_runtime_history() is False
    finally:
        store.close()


class _EvaluationStub:
    def __init__(self, catalog):
        self.catalog = catalog

    def evaluate(self, alpha_id, periods, **_kwargs):
        self.catalog.set_returns(alpha_id, (0.01, 0.0, 0.02, -0.01))
        return AlphaEvaluation(
            alpha_id=alpha_id,
            verdict=ValidationVerdict.PASS,
            deflated_sharpe=0.8,
            pbo=0.1,
            catalog_trial_count=self.catalog.global_trial_count(),
            marginal_ir=0.5,
        )


def test_agent_loop_uses_runtime_boundary_when_configured(tmp_path):
    store, registry, runtime = _runtime(tmp_path)
    try:
        catalog = AlphaCatalog()
        loop = ResearchAgentLoop(
            catalog=catalog,
            evaluator=_EvaluationStub(catalog),
            runtime=runtime,
        )
        definition = AlphaDefinition(alpha_id="a0", name="A0", factor_ids=("f0",))
        result = loop.run(
            [(definition, object())],
            timestamp=7,
            runtime_context={"dataset_version": "fixture-v1"},
        )
        assert catalog.state("a0") == AlphaState.PAPER_TRADING
        assert result["human_review_queue"] == ["a0"]
        rows = registry.runtime_rows()
        assert len(rows) == 1
        assert rows[0]["action"] == "EVALUATE"
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
        definition = AlphaDefinition(alpha_id="a0", name="A0", factor_ids=("f0",))
        result = loop.run([(definition, object())], timestamp=7)
        assert result["human_review_queue"] == []
        assert result["report"]["a0"]["error"] == "ValueError"
        assert catalog.state("a0") != AlphaState.APPROVED
        assert registry.runtime_rows() == []
    finally:
        store.close()
