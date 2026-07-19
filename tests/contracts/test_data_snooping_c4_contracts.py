"""Pass-C/C4 contract freeze for family authority identity records.

These tests deliberately describe contracts only.  They do not import an
authority service, open ExperimentStore, or assume that constructing a frozen
value grants permission to issue the identity carried by that value.
"""

from __future__ import annotations

import importlib
from types import ModuleType

import pytest
from pydantic import ValidationError

from quantos.contracts.canonical import canonical_sha256


_CONTRACT_FIELDS: dict[str, frozenset[str]] = {
    "StructuredResearchQuestion": frozenset(
        {
            "question_schema_version",
            "market_domain",
            "universe_domain",
            "signal_class",
            "label_definition",
            "evaluation_horizon",
        }
    ),
    "ResearchQuestionRef": frozenset({"research_question_id", "question_hash", "question"}),
    "ResearchQuestionAlias": frozenset({"alias", "alias_hash", "research_question_id"}),
    "SourceManifest": frozenset(
        {
            "source_manifest_schema_version",
            "generator_kind",
            "strategy_class",
            "generator_entrypoint",
            "source_tree_hash",
        }
    ),
    "StrategyLineageRootRef": frozenset(
        {
            "strategy_lineage_root_id",
            "source_manifest_hash",
            "source_manifest",
        }
    ),
    "StrategyLineageNodeRef": frozenset(
        {
            "strategy_lineage_node_id",
            "strategy_lineage_root_id",
            "parent_strategy_lineage_node_id",
            "node_manifest_hash",
        }
    ),
    "DeploymentMandate": frozenset(
        {
            "mandate_schema_version",
            "portfolio_sleeve",
            "capital_authority",
            "risk_budget_authority",
            "market_domain",
            "universe_domain",
            "label_definition",
            "evaluation_horizon",
            "benchmark_id",
            "cost_policy_class",
        }
    ),
    "SelectionScopeRef": frozenset({"selection_scope_id", "deployment_mandate_hash", "deployment_mandate"}),
    "SelectionScopeAlias": frozenset({"alias", "alias_hash", "selection_scope_id"}),
    "SelectionScopeOutcome": frozenset(
        {
            "selection_scope_id",
            "outcome",
            "selection_decision_id",
            "family_id",
            "winner_alpha_id",
            "evidence_ref_hash",
        }
    ),
    "ResearchProgramRef": frozenset(
        {
            "research_program_id",
            "selection_scope_id",
            "research_question_id",
            "question_hash",
            "strategy_lineage_root_id",
            "source_manifest_hash",
        }
    ),
    "SelectionDecisionRef": frozenset(
        {
            "selection_decision_id",
            "selection_scope_id",
            "decision_ordinal",
            "family_id",
            "decision_class",
        }
    ),
    "OosExposureRef": frozenset(
        {
            "oos_exposure_id",
            "selection_scope_id",
            "selection_decision_id",
            "family_id",
            "exposure_ordinal",
            "observed_pit_authority_id",
            "observed_pit_handle_id",
            "observed_pit_content_hash",
            "dataset_id",
            "dataset_version",
            "universe_id",
            "universe_hash",
            "ordered_period_calendar_hash",
            "knowledge_time_policy_id",
            "knowledge_time_policy_version",
            "label_horizon",
            "purge_periods",
            "embargo_periods",
            "cost_contract_hash",
            "benchmark_contract_hash",
            "period_start_us",
            "period_end_us",
            "exposure_fingerprint",
            "exposure_version",
            "observed_pit_authority_head",
        }
    ),
    "OosExposureConsumption": frozenset(
        {
            "oos_exposure_id",
            "selection_decision_id",
            "family_id",
            "access_attempt_id",
            "expected_exposure_version",
            "consumed_at_us",
            "consumption_authority_id",
            "consumption_record_hash",
            "consumption_authority_head",
        }
    ),
}

_AUTHORITY_ISSUED_IDENTITIES = frozenset(
    {
        "research_question_id",
        "strategy_lineage_root_id",
        "strategy_lineage_node_id",
        "selection_scope_id",
        "research_program_id",
        "selection_decision_id",
        "family_id",
        "oos_exposure_id",
    }
)


def _research_module() -> ModuleType:
    return importlib.import_module("quantos.contracts.research")


def _contracts() -> dict[str, type[object]]:
    module = _research_module()
    missing = tuple(name for name in _CONTRACT_FIELDS if not hasattr(module, name))
    assert not missing, "missing C4 contract symbols: " + ", ".join(missing)
    return {name: getattr(module, name) for name in _CONTRACT_FIELDS}


def _question(contracts: dict[str, type[object]]) -> object:
    return contracts["StructuredResearchQuestion"](
        question_schema_version="STRUCTURED_RESEARCH_QUESTION_V1",
        market_domain="CRYPTO_SPOT",
        universe_domain="LIQUID_LARGE_CAP",
        signal_class="CROSS_SECTIONAL_CARRY",
        label_definition="NET_EXCESS_RETURN",
        evaluation_horizon="P7D",
    )


def _source_manifest(contracts: dict[str, type[object]]) -> object:
    return contracts["SourceManifest"](
        source_manifest_schema_version="SOURCE_MANIFEST_V1",
        generator_kind="GUIDED_SEARCH",
        strategy_class="CROSS_SECTIONAL_LONG_SHORT",
        generator_entrypoint="services.intelligence.family_search:run",
        source_tree_hash="1" * 64,
    )


def _mandate(contracts: dict[str, type[object]]) -> object:
    return contracts["DeploymentMandate"](
        mandate_schema_version="DEPLOYMENT_MANDATE_V1",
        portfolio_sleeve="CRYPTO_MARKET_NEUTRAL",
        capital_authority="L2_DRY_RUN_CAPITAL_V1",
        risk_budget_authority="RESEARCH_RISK_BUDGET_V1",
        market_domain="CRYPTO_SPOT",
        universe_domain="LIQUID_LARGE_CAP",
        label_definition="NET_EXCESS_RETURN",
        evaluation_horizon="P7D",
        benchmark_id="EQUAL_WEIGHT_UNIVERSE_V1",
        cost_policy_class="CONSERVATIVE_CRYPTO_V1",
    )


def test_c4_contract_symbols_are_present() -> None:
    module = _research_module()
    missing = tuple(name for name in _CONTRACT_FIELDS if not hasattr(module, name))
    assert not missing, "missing C4 contract symbols: " + ", ".join(missing)


def test_c4_contract_fields_are_exact_frozen_and_extra_forbid() -> None:
    contracts = _contracts()
    for name, expected_fields in _CONTRACT_FIELDS.items():
        model = contracts[name]
        assert frozenset(model.model_fields) == expected_fields, name  # type: ignore[attr-defined]
        assert model.model_config.get("frozen") is True, name  # type: ignore[attr-defined]
        assert model.model_config.get("extra") == "forbid", name  # type: ignore[attr-defined]


def test_canonical_issuance_inputs_contain_no_authority_issued_identity() -> None:
    contracts = _contracts()
    for name in ("StructuredResearchQuestion", "SourceManifest", "DeploymentMandate"):
        fields = frozenset(contracts[name].model_fields)  # type: ignore[attr-defined]
        assert fields.isdisjoint(_AUTHORITY_ISSUED_IDENTITIES), name

    # benchmark_id is an economic mandate dimension, not an authority-issued
    # family/scope identity, and therefore remains part of the canonical input.
    assert "benchmark_id" in contracts["DeploymentMandate"].model_fields  # type: ignore[attr-defined]


def test_deployment_mandate_cannot_absorb_non_economic_scope_reset_metadata() -> None:
    contracts = _contracts()
    model = contracts["DeploymentMandate"]
    payload = _mandate(contracts).model_dump(mode="python")  # type: ignore[attr-defined]
    for forbidden in (
        "requester",
        "owner",
        "title",
        "free_text_title",
        "process_id",
        "research_question_id",
        "strategy_lineage_root_id",
        "strategy_class",
        "alias",
    ):
        with pytest.raises(ValidationError):
            model(**payload, **{forbidden: "scope-reset-attempt"})


def test_question_manifest_and_mandate_refs_bind_their_canonical_payload_hash() -> None:
    contracts = _contracts()
    question = _question(contracts)
    question_hash = canonical_sha256(question)
    question_ref = contracts["ResearchQuestionRef"](
        research_question_id="question-authority-0001",
        question_hash=question_hash,
        question=question,
    )
    assert question_ref.question_hash == question_hash  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        contracts["ResearchQuestionRef"](
            research_question_id="question-authority-0001",
            question_hash="f" * 64,
            question=question,
        )

    manifest = _source_manifest(contracts)
    manifest_hash = canonical_sha256(manifest)
    root = contracts["StrategyLineageRootRef"](
        strategy_lineage_root_id="lineage-root-authority-0001",
        source_manifest_hash=manifest_hash,
        source_manifest=manifest,
    )
    assert root.source_manifest_hash == manifest_hash  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        contracts["StrategyLineageRootRef"](
            strategy_lineage_root_id="lineage-root-authority-0001",
            source_manifest_hash="e" * 64,
            source_manifest=manifest,
        )

    mandate = _mandate(contracts)
    mandate_hash = canonical_sha256(mandate)
    scope = contracts["SelectionScopeRef"](
        selection_scope_id="selection-scope-authority-0001",
        deployment_mandate_hash=mandate_hash,
        deployment_mandate=mandate,
    )
    assert scope.deployment_mandate_hash == mandate_hash  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        contracts["SelectionScopeRef"](
            selection_scope_id="selection-scope-authority-0001",
            deployment_mandate_hash="d" * 64,
            deployment_mandate=mandate,
        )


def test_program_ref_carries_content_provenance_not_only_authority_ids() -> None:
    contracts = _contracts()
    program = contracts["ResearchProgramRef"](
        research_program_id="research-program-authority-0001",
        selection_scope_id="selection-scope-authority-0001",
        research_question_id="question-authority-0001",
        question_hash="a" * 64,
        strategy_lineage_root_id="lineage-root-authority-0001",
        source_manifest_hash="b" * 64,
    )
    assert program.question_hash == "a" * 64  # type: ignore[attr-defined]
    assert program.source_manifest_hash == "b" * 64  # type: ignore[attr-defined]


def test_selection_scope_outcome_has_exactly_one_terminal_branch() -> None:
    contracts = _contracts()
    outcome = contracts["SelectionScopeOutcome"]
    closed = outcome(
        selection_scope_id="selection-scope-authority-0001",
        outcome="CLOSED_WITHOUT_PROMOTION",
    )
    assert closed.selection_decision_id is None  # type: ignore[attr-defined]
    assert closed.family_id is None  # type: ignore[attr-defined]
    assert closed.winner_alpha_id is None  # type: ignore[attr-defined]
    assert closed.evidence_ref_hash is None  # type: ignore[attr-defined]

    winner_payload = {
        "selection_scope_id": "selection-scope-authority-0001",
        "outcome": "WINNER_PINNED",
        "selection_decision_id": "selection-decision-authority-0001",
        "family_id": "family-authority-0001",
        "winner_alpha_id": "alpha-content-0001",
        "evidence_ref_hash": "c" * 64,
    }
    assert outcome(**winner_payload).winner_alpha_id == "alpha-content-0001"

    with pytest.raises(ValidationError):
        outcome(
            selection_scope_id="selection-scope-authority-0001",
            outcome="WINNER_PINNED",
            family_id="family-authority-0001",
            winner_alpha_id="alpha-content-0001",
            evidence_ref_hash="c" * 64,
        )
    with pytest.raises(ValidationError):
        outcome(
            selection_scope_id="selection-scope-authority-0001",
            outcome="CLOSED_WITHOUT_PROMOTION",
            selection_decision_id="selection-decision-authority-0001",
            family_id="family-authority-0001",
            winner_alpha_id="alpha-content-0001",
            evidence_ref_hash="c" * 64,
        )


def test_decision_and_exposure_contracts_freeze_ordinals_terminal_range_and_tuple() -> None:
    contracts = _contracts()
    decision = contracts["SelectionDecisionRef"](
        selection_decision_id="selection-decision-authority-0001",
        selection_scope_id="selection-scope-authority-0001",
        decision_ordinal=1,
        family_id="family-authority-0001",
        decision_class="PUBLISHABLE",
    )
    with pytest.raises(ValidationError):
        decision.decision_ordinal = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        contracts["SelectionDecisionRef"](
            selection_decision_id="selection-decision-authority-0002",
            selection_scope_id="selection-scope-authority-0001",
            decision_ordinal=0,
            family_id="family-authority-0002",
            decision_class="DIAGNOSTIC_ONLY",
        )

    exposure_identity = {
        "observed_pit_content_hash": "4" * 64,
        "dataset_version": "sha256:observed-pit-v1",
        "universe_id": "liquid-large-cap-v1",
        "universe_hash": "5" * 64,
        "ordered_period_calendar_hash": "6" * 64,
        "label_horizon": "P7D",
        "purge_periods": 1,
        "embargo_periods": 1,
    }
    exposure_payload = {
        "oos_exposure_id": "oos-exposure-authority-0001",
        "selection_scope_id": "selection-scope-authority-0001",
        "selection_decision_id": "selection-decision-authority-0001",
        "family_id": "family-authority-0001",
        "exposure_ordinal": 1,
        "observed_pit_authority_id": "observed-pit-authority-v1",
        "observed_pit_handle_id": "observed-pit-handle-authority-0001",
        **exposure_identity,
        "dataset_id": "crypto-observed-pit",
        "knowledge_time_policy_id": "pit-knowledge-time-policy",
        "knowledge_time_policy_version": "v1",
        "cost_contract_hash": "a" * 64,
        "benchmark_contract_hash": "b" * 64,
        "exposure_fingerprint": canonical_sha256(exposure_identity),
        "period_start_us": 1_700_000_000_000_000,
        "period_end_us": 1_710_000_000_000_000,
        "exposure_version": 1,
        "observed_pit_authority_head": "7" * 64,
    }
    exposure = contracts["OosExposureRef"](**exposure_payload)
    consumption = contracts["OosExposureConsumption"](
        oos_exposure_id=exposure.oos_exposure_id,  # type: ignore[attr-defined]
        selection_decision_id=exposure.selection_decision_id,  # type: ignore[attr-defined]
        family_id=exposure.family_id,  # type: ignore[attr-defined]
        access_attempt_id="oos-access-attempt-authority-0001",
        expected_exposure_version=exposure.exposure_version,  # type: ignore[attr-defined]
        consumed_at_us=1_710_000_000_000_001,
        consumption_authority_id="family-authority-v3",
        consumption_record_hash="8" * 64,
        consumption_authority_head="9" * 64,
    )
    assert consumption.oos_exposure_id == exposure.oos_exposure_id  # type: ignore[attr-defined]

    with pytest.raises(ValidationError):
        contracts["OosExposureRef"](**{**exposure_payload, "period_end_us": exposure_payload["period_start_us"]})
    with pytest.raises(ValidationError):
        contracts["OosExposureRef"](**{**exposure_payload, "exposure_ordinal": 0})
    for field, tampered in (
        ("observed_pit_content_hash", "a" * 64),
        ("dataset_version", "sha256:different"),
        ("universe_id", "different-universe"),
        ("universe_hash", "b" * 64),
        ("ordered_period_calendar_hash", "c" * 64),
        ("label_horizon", "P14D"),
        ("purge_periods", 2),
        ("embargo_periods", 2),
    ):
        with pytest.raises(ValidationError):
            contracts["OosExposureRef"](**{**exposure_payload, field: tampered})


def test_canonical_input_contracts_reject_non_nfc_and_all_contracts_reject_extras() -> None:
    contracts = _contracts()
    mandate_payload = _mandate(contracts).model_dump(mode="python")  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        contracts["DeploymentMandate"](**{**mandate_payload, "portfolio_sleeve": "e\u0301"})

    question_payload = _question(contracts).model_dump(mode="python")  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        contracts["StructuredResearchQuestion"](**question_payload, caller_id="forbidden")
