from tools.alpha_methodology_audit import build_methodology_audit


def test_audit_report_is_reproducible_and_not_a_promotion_artifact():
    availability = {
        "binance_usdm_price": "REAL_HISTORY",
        "binance_spot_klines": "REAL_HISTORY",
        "binance_usdm_websocket_stream": "REAL_RECENT",
    }
    first = build_methodology_audit(
        availability=availability, generated_at_ms=1234
    )
    second = build_methodology_audit(
        availability=dict(reversed(tuple(availability.items()))),
        generated_at_ms=1234,
    )
    assert first == second
    assert first["purpose"] == "ALPHA_RESEARCH_SUPPLY_NOT_PROMOTION"
    assert first["alpha_eligible_count"] == 0
    assert first["methodology_hash"]
    assert first["schema_version"] == 2
    assert first["executable_objective"]["research_only"] is True
    assert "sharpe" not in first["executable_objective"]["formula"].lower()
    assert first["data_surface_audit"]


def test_audit_keeps_ict_practitioner_provenance_visible():
    report = build_methodology_audit(
        availability={"binance_usdm_websocket_stream": "REAL_RECENT"},
        generated_at_ms=1234,
    )
    rows = {item["method_id"]: item for item in report["ranked_methods"]}
    ict = rows["ict_liquidity_sweep_session"]
    assert "PRACTITIONER_PRIMARY" in ict["evidence_tiers"]
    assert ict["readiness"] == "FORWARD_PREREG_REQUIRED"
    assert ict["payer_ids"] == ["FORCED_LIQUIDATOR", "URGENT_LARGE_ORDER"]
    assert ict["persistence_assumption"]
    assert ict["independent_bet_unit"]


def test_audit_surfaces_missing_institutional_inputs_without_profit_claims() -> None:
    report = build_methodology_audit(
        availability={"binance_usdm_websocket_stream": "REAL_RECENT"},
        generated_at_ms=1234,
    )
    rows = {item["surface_id"]: item for item in report["data_surface_audit"]}

    assert rows["multi_venue_trade_l2"]["readiness"] == "PARTIAL"
    assert rows["borrow_survivorship"]["readiness"] == "MISSING"
    assert "BORROW_RATE" in rows["borrow_survivorship"]["missing_capabilities"]
    assert report["alpha_eligible_count"] == 0
