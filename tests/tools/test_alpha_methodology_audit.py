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


def test_audit_keeps_ict_practitioner_provenance_visible():
    report = build_methodology_audit(
        availability={"binance_usdm_websocket_stream": "REAL_RECENT"},
        generated_at_ms=1234,
    )
    rows = {item["method_id"]: item for item in report["ranked_methods"]}
    ict = rows["ict_liquidity_sweep_session"]
    assert "PRACTITIONER_PRIMARY" in ict["evidence_tiers"]
    assert ict["readiness"] == "FORWARD_PREREG_REQUIRED"

