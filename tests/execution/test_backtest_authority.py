from __future__ import annotations

import math
from dataclasses import replace

import pytest

from services.execution.backtest_authority import (
    BacktestIntegrityBlocked,
    RunManifestV2,
    build_run_manifest_v2,
    exact_identity_hash,
    is_authoritative_backtest_record,
    quarantine_legacy_backtest,
)


def _manifest(**overrides) -> RunManifestV2:
    inputs = {
        "release_identity": "git:0123456789abcdef",
        "data_identity": {"bars": "bars-v3", "funding": "funding-v2"},
        "universe": ["BTCUSDT", "ETHUSDT"],
        "alpha_definition": {"expr": "cs_rank(neg(global_ls))", "q": 0.2},
        "periods": [100, 200, 300],
        "decision_events": [{"t": 100}, {"t": 200}, {"t": 300}],
        "execution_events": [{"t": 101}, {"t": 201}, {"t": 301}],
        "weights_by_period": {
            100: {"BTCUSDT": "0.5"},
            200: {"BTCUSDT": "0.4"},
            300: {"BTCUSDT": "0.3"},
        },
        "prices_by_period": {
            101: {"BTCUSDT": "100"},
            201: {"BTCUSDT": "110"},
            301: {"BTCUSDT": "120"},
        },
        "cost_model": {"kind": "historical_l2", "version": 2},
        "funding_policy": {"kind": "settlement_events", "completeness": "required"},
        "venue_rules": {"snapshot_ids": ["rules-100", "rules-200"]},
        "risk_limits": {"max_leverage": "2", "max_gross": "2000000"},
        "filters": {"BTCUSDT": {"step_size": "0.001", "min_qty": "0.001"}},
        "impact_model": {"kind": "shadow_calibrated", "snapshot": "impact-7"},
        "adv_by_period": {101: {"BTCUSDT": "100000000"}},
        "parameters": {"aum": "1000000", "cadence": "P1D"},
        "random_seed": 7,
    }
    inputs.update(overrides)
    return build_run_manifest_v2(**inputs)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("release_identity", "git:different"),
        ("data_identity", {"bars": "different"}),
        ("universe", ["BTCUSDT", "SOLUSDT"]),
        ("alpha_definition", {"expr": "different"}),
        ("periods", [100, 250, 300]),
        ("decision_events", [{"t": 99}, {"t": 200}, {"t": 300}]),
        ("execution_events", [{"t": 102}, {"t": 201}, {"t": 301}]),
        (
            "weights_by_period",
            {
                100: {"BTCUSDT": "0.49"},
                200: {"BTCUSDT": "0.4"},
                300: {"BTCUSDT": "0.3"},
            },
        ),
        (
            "prices_by_period",
            {
                101: {"BTCUSDT": "101"},
                201: {"BTCUSDT": "110"},
                301: {"BTCUSDT": "120"},
            },
        ),
        ("cost_model", {"kind": "different"}),
        ("funding_policy", {"kind": "different"}),
        ("venue_rules", {"snapshot_ids": ["different"]}),
        ("risk_limits", {"max_leverage": "3"}),
        ("filters", {"BTCUSDT": {"step_size": "0.01"}}),
        ("impact_model", {"kind": "different"}),
        ("adv_by_period", {101: {"BTCUSDT": "1"}}),
        ("parameters", {"aum": "999999"}),
        ("random_seed", 8),
    ],
)
def test_every_selection_bearing_input_changes_run_identity(field, changed):
    baseline = _manifest()
    changed_manifest = _manifest(**{field: changed})
    assert changed_manifest.manifest_hash != baseline.manifest_hash
    assert changed_manifest.run_id != baseline.run_id


def test_manifest_identity_does_not_round_distinct_float_inputs_together():
    baseline = _manifest(cost_model={"fee": 0.1})
    adjacent = _manifest(cost_model={"fee": math.nextafter(0.1, 1.0)})

    assert baseline.cost_model_hash != adjacent.cost_model_hash
    assert baseline.run_id != adjacent.run_id


def test_manifest_identity_rejects_nonfinite_or_opaque_inputs():
    with pytest.raises(ValueError, match="non-finite float"):
        _manifest(cost_model={"fee": float("nan")})
    with pytest.raises(TypeError, match="unsupported authority identity type"):
        _manifest(cost_model={"opaque": object()})


def test_manifest_is_canonical_and_round_trips():
    manifest = _manifest()
    restored = RunManifestV2.from_dict(manifest.as_dict())
    assert restored == manifest
    assert restored.manifest_hash == manifest.manifest_hash
    assert restored.run_id == manifest.manifest_hash


def _record(manifest: RunManifestV2) -> dict:
    engines = {
        name: True
        for name in (
            "market",
            "liquidity",
            "concentration",
            "correlation",
            "drawdown",
            "regime",
            "stress",
            "capacity",
            "model_alpha",
        )
    }
    risk = {
        "decision": "RISK_APPROVED",
        "status": "APPROVED",
        "provenance": "REAL_RESEARCH_RISK_FULL",
        "gates_run": 9,
        "gates_passed": 9,
        "engines_run": engines,
        "engines_skipped": {},
        "engines_pending": {},
    }
    reconciliation = {"residual": "0", "ending_equity": "1000"}
    risk_history = [{"decision_id": "d1", "result": risk}]
    result_payload = {
        "schema_version": 1,
        "run_id": manifest.run_id,
        "reconciliation": reconciliation,
        "ending_state": {"cashflow_identity": reconciliation},
        "ledger_events": [{"event_type": "TRADE"}],
        "liquidations": [],
        "decision_contexts": [{"decision_id": "d1"}],
        "research_risk_results": risk_history,
        "execution_results": [{"execution_id": "e1", "status": "FILLED"}],
    }
    return {
        "run_id": manifest.run_id,
        "strategy_id": "alpha-a",
        "detail": {
            "run_manifest_v2": manifest.as_dict(),
            "manifest_hash": manifest.manifest_hash,
            "engine_semantics": "LINEAR_PERPETUAL_EVENT_V2",
            "evidence_status": "AUTHORITATIVE",
            "engine_attestations": {
                "causal_available_time": True,
                "strict_next_event_execution": True,
                "decimal_contracts": True,
                "settlement_event_funding": True,
                "historical_venue_rules": True,
                "margin_liquidation_state_machine": True,
                "complete_cashflow_identity": True,
                "deployment_equivalent_risk": True,
                "capacity_is_load_bearing": True,
                "full_research_risk_9_of_9": True,
                "risk_history_ledger_replayed": True,
            },
            "research_risk": risk,
            "research_risk_history": risk_history,
            "result_hash_payload": result_payload,
            "result_hash": exact_identity_hash(
                result_payload, name="authoritative_backtest_result"
            ),
        },
    }


def test_self_asserted_v2_record_is_not_authority_without_external_signature():
    manifest = _manifest()
    assert is_authoritative_backtest_record(_record(manifest)) is False

    legacy = {"run_id": "old", "detail": {"mode": "multi_period"}}
    assert is_authoritative_backtest_record(legacy) is False

    tampered_hash = _record(manifest)
    tampered_hash["detail"]["manifest_hash"] = "0" * 64
    assert is_authoritative_backtest_record(tampered_hash) is False

    tampered_run = _record(manifest)
    tampered_run["run_id"] = "old-id"
    assert is_authoritative_backtest_record(tampered_run) is False

    missing_attestation = _record(manifest)
    missing_attestation["detail"]["engine_attestations"]["decimal_contracts"] = False
    assert is_authoritative_backtest_record(missing_attestation) is False

    unreplayed_risk_history = _record(manifest)
    unreplayed_risk_history["detail"]["engine_attestations"][
        "risk_history_ledger_replayed"
    ] = False
    assert is_authoritative_backtest_record(unreplayed_risk_history) is False

    tampered_result = _record(manifest)
    tampered_result["detail"]["result_hash_payload"]["reconciliation"][
        "ending_equity"
    ] = "999"
    assert is_authoritative_backtest_record(tampered_result) is False


@pytest.mark.parametrize(
    "field",
    ("periods", "decision_events", "execution_events", "weights_by_period", "prices_by_period"),
)
def test_manifest_rejects_misaligned_period_economic_records(field):
    changes = {
        "periods": [100, 200],
        "decision_events": [{"t": 100}, {"t": 200}],
        "execution_events": [{"t": 101}, {"t": 201}],
        "weights_by_period": {100: {"BTCUSDT": "0.5"}, 200: {"BTCUSDT": "0.4"}},
        "prices_by_period": {101: {"BTCUSDT": "100"}, 201: {"BTCUSDT": "110"}},
    }
    with pytest.raises(ValueError, match="one-to-one aligned"):
        _manifest(**{field: changes[field]})


def test_manifest_rejects_noncausal_execution_timeline():
    with pytest.raises(ValueError, match="execution event must be strictly after decision"):
        _manifest(
            decision_events=[{"t": 100}, {"t": 200}],
            execution_events=[{"t": 100}, {"t": 201}],
            periods=[100, 200],
            weights_by_period={
                100: {"BTCUSDT": "0.5"},
                200: {"BTCUSDT": "0.4"},
            },
            prices_by_period={
                101: {"BTCUSDT": "100"},
                201: {"BTCUSDT": "110"},
            },
        )


@pytest.mark.parametrize(
    "field, events",
    (
        (
            "decision_events",
            [
                {"t": 100, "decision_time_ms": 99},
                {"t": 200},
                {"t": 300},
            ],
        ),
        (
            "execution_events",
            [
                {"t": 101, "execution_time_ms": 102},
                {"t": 201},
                {"t": 301},
            ],
        ),
    ),
)
def test_manifest_rejects_conflicting_event_time_fields(field, events):
    with pytest.raises(ValueError, match="conflicting event times"):
        _manifest(**{field: events})


def test_manifest_validates_every_present_event_time_field():
    with pytest.raises(ValueError, match="decision_time_ms time"):
        _manifest(
            decision_events=[
                {"t": 100, "decision_time_ms": "100"},
                {"t": 200},
                {"t": 300},
            ]
        )


def test_legacy_engine_is_explicitly_quarantined():
    with pytest.raises(BacktestIntegrityBlocked, match="INVALIDATED_PERP_LEDGER_V1"):
        quarantine_legacy_backtest("dry_run_candidate_multi")


def test_all_legacy_authoritative_driver_entrypoints_are_quarantined():
    from tools import dry_run_carry_authoritative_multi
    from tools import dry_run_crowd_contra_multi
    from tools import dry_run_top_contra_multi

    for entrypoint in (
        dry_run_carry_authoritative_multi.main,
        dry_run_crowd_contra_multi.main,
        dry_run_top_contra_multi.main,
    ):
        with pytest.raises(BacktestIntegrityBlocked, match="INVALIDATED_PERP_LEDGER_V1"):
            entrypoint()


def test_legacy_score_bearing_variant_search_blocks_before_registry_use():
    from tools import crowd_contra_variants

    with pytest.raises(BacktestIntegrityBlocked, match="INVALIDATED_PERP_LEDGER_V1"):
        crowd_contra_variants._main(object())


def test_variant_search_cli_blocks_before_opening_production_registry(monkeypatch):
    from tools import crowd_contra_variants

    calls = []
    monkeypatch.setattr(
        crowd_contra_variants,
        "production_experiment_registry",
        lambda: calls.append("opened"),
    )
    with pytest.raises(BacktestIntegrityBlocked, match="INVALIDATED_PERP_LEDGER_V1"):
        crowd_contra_variants.main()
    assert calls == []
