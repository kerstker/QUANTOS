import pytest

from services.research.alpha_methodology import (
    ALPHA_METHODS,
    DISCOVERY_CONTROLS,
    SOURCES,
    AlphaMethodSpec,
    EconomicPayer,
    EvidenceTier,
    MethodReadiness,
    MethodologyRegistryError,
    SourceSpec,
    audit_methodology,
    validate_methodology_registry,
)


def test_builtin_methodology_registry_is_valid_and_unique():
    validate_methodology_registry(SOURCES, ALPHA_METHODS, DISCOVERY_CONTROLS)
    assert len({item.source_id for item in SOURCES}) == len(SOURCES)
    assert len({item.method_id for item in ALPHA_METHODS}) == len(ALPHA_METHODS)
    assert len({item.control_id for item in DISCOVERY_CONTROLS}) == len(
        DISCOVERY_CONTROLS
    )


def test_every_alpha_method_has_mechanism_falsification_and_failure_risks():
    for item in ALPHA_METHODS:
        assert item.payer_ids
        assert all(isinstance(payer, EconomicPayer) for payer in item.payer_ids)
        assert item.persistence_assumption.strip()
        assert item.independent_bet_unit.strip()
        assert item.economic_mechanism.strip()
        assert item.testable_claim.strip()
        assert item.required_channel_ids
        assert item.holding_horizons
        assert item.cost_risks
        assert item.falsification_tests
        assert item.leakage_risks
        assert item.source_ids
        assert item.system_modules


def test_practitioner_claims_cannot_masquerade_as_peer_reviewed_sources():
    bad_source = SourceSpec(
        source_id="ict_bad_tier",
        title="Practitioner video",
        authors=("Practitioner",),
        year=2022,
        evidence_tier=EvidenceTier.PEER_REVIEWED,
        url="https://example.com/video",
        doi=None,
        practitioner_claim=True,
        scope_note="A practitioner claim without independent academic evidence.",
    )
    with pytest.raises(MethodologyRegistryError, match="practitioner claim"):
        validate_methodology_registry(
            (bad_source,),
            (
                AlphaMethodSpec(
                    method_id="bad_method",
                    name="bad",
                    source_ids=(bad_source.source_id,),
                    portability="PRACTITIONER_TRANSLATION",
                    economic_mechanism="Claimed price delivery behavior.",
                    testable_claim="A fixed signal predicts a fixed future return.",
                    required_channel_ids=("channel",),
                    holding_horizons=("1h",),
                    cost_risks=("spread",),
                    falsification_tests=("sign control",),
                    leakage_risks=("future bars",),
                    system_modules=("services/research",),
                    system_disposition="NEW_EXPERIMENT_GAP",
                    payer_ids=(EconomicPayer.URGENT_LARGE_ORDER,),
                    persistence_assumption="Urgency repeats when liquidity demand is inelastic.",
                    independent_bet_unit="one non-overlapping event",
                ),
            ),
            (),
        )


def test_method_without_identified_payer_is_rejected_before_search() -> None:
    bad = AlphaMethodSpec(
        method_id="payerless_formula",
        name="payerless",
        source_ids=(SOURCES[0].source_id,),
        portability="DIRECT_CRYPTO",
        economic_mechanism="A formula has a historical correlation.",
        testable_claim="The formula predicts a future return.",
        required_channel_ids=("binance_usdm_price",),
        holding_horizons=("7d",),
        cost_risks=("turnover",),
        falsification_tests=("sign control",),
        leakage_risks=("future bars",),
        system_modules=("services/research",),
        system_disposition="NEW_EXPERIMENT_GAP",
        payer_ids=(),
        persistence_assumption="No structural persistence was identified.",
        independent_bet_unit="one week",
    )

    with pytest.raises(MethodologyRegistryError, match="economic payer"):
        validate_methodology_registry(SOURCES, (bad,), ())


def test_audit_is_fail_closed_for_missing_and_forward_only_channels():
    rows = {row.method_id: row for row in audit_methodology({
        "binance_usdm_price": "REAL_HISTORY",
        "binance_spot_klines": "REAL_HISTORY",
        "binance_usdm_websocket_stream": "REAL_RECENT",
    })}
    assert rows["pairs_relative_value"].readiness == (
        MethodReadiness.HISTORICAL_DIAGNOSTIC_READY
    )
    assert rows["ict_liquidity_sweep_session"].readiness == (
        MethodReadiness.FORWARD_PREREG_REQUIRED
    )
    assert rows["crypto_attention_momentum"].readiness == (
        MethodReadiness.BLOCKED_MISSING_INPUT
    )
    assert "crypto_attention" in rows["crypto_attention_momentum"].missing_channels


def test_audit_priority_is_deterministic_and_never_labels_profitability():
    availability = {
        method_channel: "REAL_HISTORY"
        for item in ALPHA_METHODS
        for method_channel in item.required_channel_ids
    }
    first = audit_methodology(availability)
    second = audit_methodology(dict(reversed(tuple(availability.items()))))
    assert first == second
    assert [row.priority_rank for row in first] == list(range(1, len(first) + 1))
    assert all("PROFIT" not in row.readiness.value for row in first)


def test_discovery_controls_bind_papers_to_existing_fail_closed_modules():
    by_id = {item.control_id: item for item in DISCOVERY_CONTROLS}
    assert "dsr_selection_bias" in by_id
    assert "pbo_cscv" in by_id
    assert by_id["dsr_selection_bias"].source_ids
    assert "services/validation/deflated_sharpe.py" in (
        by_id["dsr_selection_bias"].system_modules
    )


def test_pairs_result_is_internalized_as_falsified_not_an_open_gap():
    pair_method = next(
        item for item in ALPHA_METHODS if item.method_id == "pairs_relative_value"
    )

    assert pair_method.system_disposition == "EXISTING_FALSIFIED_IMPLEMENTATION"
    assert pair_method.system_modules == (
        "services/research/pairs_relative_value.py",
        "tools/pairs_relative_value_research.py",
    )


def test_liquidity_data_rejection_is_internalized_as_blocked_implementation() -> None:
    liquidity_method = next(
        item for item in ALPHA_METHODS if item.method_id == "liquidity_premium"
    )

    assert liquidity_method.system_disposition == "EXISTING_BLOCKED_IMPLEMENTATION"
    assert liquidity_method.system_modules == (
        "services/research/liquidity_premium.py",
        "tools/liquidity_premium_research.py",
    )


def test_direct_crypto_ivol_result_is_internalized_as_falsified() -> None:
    source = next(
        item for item in SOURCES if item.source_id == "crypto_ivol_zhang_li"
    )
    method = next(
        item for item in ALPHA_METHODS
        if item.method_id == "crypto_idiosyncratic_volatility"
    )

    assert source.evidence_tier is EvidenceTier.PEER_REVIEWED
    assert source.doi == "10.1016/j.ribaf.2020.101252"
    assert method.portability == "DIRECT_CRYPTO"
    assert method.system_disposition == "EXISTING_FALSIFIED_IMPLEMENTATION"
    assert method.system_modules == (
        "services/research/idiosyncratic_volatility.py",
        "tools/idiosyncratic_volatility_research.py",
    )


def test_conflicting_crypto_max_priors_are_internalized_as_falsified() -> None:
    sources = {item.source_id: item for item in SOURCES}
    method = next(
        item for item in ALPHA_METHODS if item.method_id == "crypto_lottery_max"
    )

    assert method.source_ids == (
        "crypto_lottery_grobys_junttila",
        "crypto_max_momentum_li_urquhart_wang_zhang",
    )
    assert all(
        sources[source_id].evidence_tier is EvidenceTier.PEER_REVIEWED
        for source_id in method.source_ids
    )
    assert method.portability == "DIRECT_CRYPTO"
    assert method.system_disposition == "EXISTING_FALSIFIED_IMPLEMENTATION"
    assert "high-MAX sign control" in method.falsification_tests
    assert method.system_modules == (
        "services/research/lottery_max.py",
        "tools/lottery_max_research.py",
    )


def test_ict_sweep_claim_is_bound_to_forward_only_observable_translation() -> None:
    sources = {item.source_id: item for item in SOURCES}
    method = next(
        item for item in ALPHA_METHODS
        if item.method_id == "ict_liquidity_sweep_session"
    )

    assert sources["ict_huddleston_primary"].evidence_tier is (
        EvidenceTier.PRACTITIONER_PRIMARY
    )
    assert sources["ofi_cont_kukanov_stoikov"].evidence_tier is (
        EvidenceTier.PEER_REVIEWED
    )
    assert method.portability == "PRACTITIONER_TRANSLATION"
    assert method.system_disposition == "FORWARD_EXPERIMENT_GAP"
    assert method.system_modules == (
        "services/research/ict_liquidity_sweep.py",
        "tools/ict_liquidity_sweep_preregistration.py",
    )


def test_cross_venue_liquidation_method_names_payer_and_forward_authorities() -> None:
    method = next(
        item for item in ALPHA_METHODS
        if item.method_id == "cross_venue_liquidation_recovery"
    )

    assert method.portability == "DIRECT_CRYPTO"
    assert method.system_disposition == "FORWARD_EXPERIMENT_GAP"
    assert method.required_channel_ids == (
        "binance_usdm_websocket_stream",
        "bybit_linear_websocket_events",
    )
    assert method.payer_ids == (
        EconomicPayer.FORCED_LIQUIDATOR,
        EconomicPayer.CAPITAL_CONSTRAINED_CROSS_VENUE,
    )
    assert "30-minute" in method.independent_bet_unit
    assert method.system_modules == (
        "tools/cross_venue_liquidation_preregistration.py",
    )
